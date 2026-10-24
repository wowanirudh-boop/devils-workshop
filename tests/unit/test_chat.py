import asyncio

import pytest

from devils_workshop.chat.runner import send
from devils_workshop.chat.scenes import (
    ProposedScene,
    apply_scene_proposal,
    parse_scene_proposal,
    prepare_scene_proposal,
)
from devils_workshop.chat.session import CHANGE, CONTINUE, SCENE, WRITE, ChapterChat
from devils_workshop.context.assemble import AssemblyError
from devils_workshop.history import list_snapshots, restore, snapshot, write_project_file
from devils_workshop.llm.catalog import Catalog, parse_model
from devils_workshop.project.project import load_project
from devils_workshop.settings import AppSettings
from devils_workshop.tools.plancheck import run_plan_check

from tests.conftest import settings, settings_file, write
from tests.fakes import MODELS, PROSE, FakeOpenRouter, reply

SCENE_ONE = "The ledger room smelled of brine. Ada was already talking. " * 3
SCENE_TWO = "From the archive window the tower was a black thumb against the sky. " * 3


# --- history ---------------------------------------------------------------------------------

def test_snapshot_and_restore(sample):
    original = (sample / "outline.md").read_text()
    write_project_file(sample, "outline.md", "changed", "edit outline")
    write_project_file(sample, "notes/new.md", "brand new", "add note")
    snaps = list_snapshots(sample)
    assert [s.action for s in snaps] == ["add note", "edit outline"]  # newest first
    assert snaps[0].missing == ["notes/new.md"] and snaps[1].files == ["outline.md"]

    restore(sample, snaps[1].id)
    assert (sample / "outline.md").read_text() == original
    restore(sample, snaps[0].id)
    assert not (sample / "notes" / "new.md").exists()  # it didn't exist before, so it's removed

    safety = [s for s in list_snapshots(sample) if s.action.startswith("before-restore")]
    assert len(safety) == 2  # every restore can itself be undone
    with pytest.raises(FileNotFoundError):
        restore(sample, "nope")


def test_snapshot_skips_duplicates(sample):
    snap = snapshot(sample, ["outline.md", "outline.md"], "x")
    assert snap.files == ["outline.md"]


def test_snapshots_at_the_same_clock_time_stay_unique_and_ordered(sample, monkeypatch):
    import devils_workshop.history as history
    from datetime import datetime

    frozen = datetime(2026, 10, 20, 0, 49, 21, 919483)
    monkeypatch.setattr(history, "datetime", type("FrozenClock", (), {"now": staticmethod(lambda: frozen)}))
    first = snapshot(sample, ["outline.md"], "edit")
    second = snapshot(sample, ["outline.md"], "edit")  # same action too, so the folder names would clash
    third = snapshot(sample, ["outline.md"], "add")  # sorts before "edit" by name alone
    assert len({first.id, second.id, third.id}) == 3
    assert [s.id for s in list_snapshots(sample)] == [third.id, second.id, first.id]  # newest first


# --- whole-chapter chats --------------------------------------------------------------------

def test_write_change_versions_restore_and_final(sample):
    project = load_project(sample)
    chat = ChapterChat.load(sample, 3)
    prepared = chat.prepare_write(project)
    assert prepared.kind == WRITE and [m.role for m in prepared.messages] == ["system", "user"]
    chapter = chat.apply(project, prepared, "First draft. " * 30)
    assert (chapter.version, chapter.status) == (1, "draft")
    assert (sample / "chapters" / "ch03.md").read_text().startswith('---\nchapter: 3\ntitle: "Low Tide"\nversion: 1')

    change = chat.prepare_change(project, "Make Tobias warmer.")
    assert [m.role for m in change.messages] == ["system", "user", "assistant", "user"]
    assert change.messages[2].content.startswith("First draft.")
    assert "Change now:\nMake Tobias warmer." in change.messages[3].content
    assert "Earlier changes" not in change.messages[3].content
    chat.apply(project, change, "Second draft. " * 30)

    second = chat.prepare_change(project, "Shorter ending.")
    assert "Earlier changes to this chapter (already made; keep them):\n1. Make Tobias warmer." in \
        second.messages[3].content
    assert second.messages[2].content.startswith("Second draft.")
    assert second.messages[:2] == prepared.messages[:2]  # the original setup, unchanged
    chat.apply(project, second, "Third draft. " * 30)

    assert [(v.number, v.current) for v in chat.versions(project)] == [(1, False), (2, False), (3, True)]
    restored = chat.restore_version(project, 1)
    assert restored.version == 4 and restored.text.startswith("First draft.")
    assert chat.mark_final(project).status == "final"
    assert len(chat.versions(project)) == 4

    again = ChapterChat.load(sample, 3)  # the chat was saved
    assert [c["text"] for c in again.change_requests] == ["Make Tobias warmer.", "Shorter ending."]
    assert again.opening == prepared.messages[:2]


def test_change_needs_a_written_chapter_and_a_request(sample):
    project = load_project(sample)
    chat = ChapterChat.load(sample, 3)
    with pytest.raises(AssemblyError, match="hasn't been written"):
        chat.prepare_change(project, "x")
    chat.apply(project, chat.prepare_write(project), PROSE)
    with pytest.raises(AssemblyError, match="Describe the change"):
        chat.prepare_change(project, "   ")


def test_change_request_adds_newly_mentioned_files(sample):
    project = load_project(sample)
    chat = ChapterChat.load(sample, 3)
    chat.apply(project, chat.prepare_write(project), PROSE)
    change = chat.prepare_change(project, "Have Will Harrow watch from his boat.")
    assert '<new_reference>\n<entry name="Will Harrow"' in change.messages[3].content
    chat.apply(project, change, PROSE)
    assert "entry:characters/will-harrow.md" in chat.sent_keys
    later = chat.prepare_change(project, "Will Harrow waves.")
    assert "<new_reference>" not in later.messages[3].content  # already sent in this chat


def test_writing_again_starts_a_fresh_chat(sample):
    project = load_project(sample)
    chat = ChapterChat.load(sample, 3)
    chat.apply(project, chat.prepare_write(project), PROSE)
    chat.apply(project, chat.prepare_change(project, "Colder."), PROSE)
    chat.apply(project, chat.prepare_write(project), "Brand new take. " * 30)
    assert chat.change_requests == [] and chat.text(project).version == 3


def test_continue_whole_chapter(sample):
    project = load_project(sample)
    chat = ChapterChat.load(sample, 3)
    chat.apply(project, chat.prepare_write(project), "Mara climbed the stairs and")
    prepared = chat.prepare_continue(project)
    assert prepared.kind == CONTINUE and "<continue>" in prepared.messages[-1].content
    assert prepared.messages[-2].content == "Mara climbed the stairs and"
    assert prepared.target_words == 3000 - 5
    chapter = chat.apply(project, prepared, "found the door open.")
    assert chapter.text == "Mara climbed the stairs and found the door open." and chapter.version == 1


def test_chapter_written_outside_the_app_can_still_be_changed(sample):
    project = load_project(sample)  # the sample already has chapters/ch01.md, but no saved chat
    chat = ChapterChat.load(sample, 1)
    change = chat.prepare_change(project, "Open on the gulls instead.")
    assert change.messages[2].content.startswith("Brindle Cove woke to the wrong kind of quiet.")
    chapter = chat.apply(project, change, PROSE)
    assert chapter.version == 2 and (sample / "chapters" / "versions" / "ch01.v1.md").is_file()


# --- scene-mode chats ---------------------------------------------------------------------------

def test_scene_by_scene_chat(sample):
    project = load_project(sample)
    chat = ChapterChat.load(sample, 2)
    first = chat.prepare_write(project)
    assert (first.kind, first.scene) == (SCENE, 1)
    chat.apply(project, first, SCENE_ONE)
    assert chat.next_scene(project) == 2

    second = chat.prepare_write(project, notes="Make it cold.")
    assert second.scene == 2
    assert [m.role for m in second.messages] == ["system", "user", "assistant", "user"]
    assert second.messages[:2] == first.messages[:2] and second.messages[2].content == SCENE_ONE.strip()
    assert "Notes for this request:\nMake it cold." in second.messages[3].content
    chapter = chat.apply(project, second, SCENE_TWO)
    assert set(chapter.scenes) == {1, 2} and chapter.version == 1
    assert chat.next_scene(project) is None
    with pytest.raises(AssemblyError, match="All scenes"):
        chat.prepare_write(project)

    reloaded = ChapterChat.load(sample, 2)
    assert reloaded.mode == "scenes" and len(reloaded.turns) == 3  # scene 1, scene-2 request, scene 2


def test_change_one_scene(sample):
    project = load_project(sample)
    chat = ChapterChat.load(sample, 2)
    chat.apply(project, chat.prepare_write(project), SCENE_ONE)
    chat.apply(project, chat.prepare_write(project), SCENE_TWO)
    change = chat.prepare_change(project, "Ada should whisper.", scene=1)
    assert "### Scene 1\n" in change.messages[-2].content and "### Scene 2\n" in change.messages[-2].content
    assert 'Write the complete new version of scene 1 ("The archive") only' in change.messages[-1].content
    chapter = chat.apply(project, change, "Ada whispered. " * 20)
    assert chapter.scenes[1].startswith("Ada whispered.") and chapter.scenes[2] == SCENE_TWO.strip()
    assert chapter.version == 2
    assert chat.turns[0].content.startswith("Ada whispered.")  # the saved chat follows the new scene
    with pytest.raises(AssemblyError, match="Scene 3"):
        chat.prepare_change(project, "x", scene=3)


def test_restart_scene_mode_chapter(sample):
    project = load_project(sample)
    chat = ChapterChat.load(sample, 2)
    chat.apply(project, chat.prepare_write(project), SCENE_ONE)
    restart = chat.prepare_write(project, restart=True)
    assert restart.scene == 1
    chapter = chat.apply(project, restart, "A new opening. " * 20)
    assert chapter.version == 2 and set(chapter.scenes) == {1}


def test_secrets_stay_hidden_until_their_scene_across_a_real_chat(sample):
    project = load_project(sample)
    chat = ChapterChat.load(sample, 4)
    secret = "helped her father recopy"
    burn = "A fresh burn across her left palm"
    seen = []
    for _ in range(3):
        prepared = chat.prepare_write(project)
        text = "\n".join(m.content for m in prepared.messages)
        seen.append((burn in text, secret in text))
        chat.apply(project, prepared, f"Scene {prepared.scene} prose. " * 20)
    assert seen == [(False, False), (True, False), (True, True)]


def test_chapter_chats_never_see_other_chapters(sample):
    project = load_project(sample)
    ChapterChat.load(sample, 3).apply(project, ChapterChat.load(sample, 3).prepare_write(project),
                                      "UNIQUE-CHAPTER-THREE-PROSE " * 10)
    project = load_project(sample)
    for chapter in (2, 4):
        text = "\n".join(m.content for m in ChapterChat.load(sample, chapter).prepare_write(project).messages)
        assert "UNIQUE-CHAPTER-THREE-PROSE" not in text and "Brindle Cove woke" not in text


# --- sending, end to end -----------------------------------------------------------------------

def test_send_and_apply_end_to_end(sample):
    project = load_project(sample)
    chat = ChapterChat.load(sample, 3)
    prepared = chat.prepare_write(project)
    fake = FakeOpenRouter().queue(reply())
    partial_seen = []

    def on_event(event):
        partial = sample / "chapters" / "ch03.partial.md"
        if partial.exists():
            partial_seen.append(partial.read_text())

    result = asyncio.run(send(prepared, project=project, settings=AppSettings(),
                              catalog=Catalog([parse_model(m) for m in MODELS]), client=fake.client(),
                              on_event=on_event, sleep=lambda s: asyncio.sleep(0)))
    assert result.ok and result.text == PROSE
    assert partial_seen and partial_seen[-1] == PROSE  # streamed to the partial file as it arrived
    assert not (sample / "chapters" / "ch03.partial.md").exists()
    assert fake.chat_bodies()[0]["model"] == "z-ai/glm-5.3"
    chat.apply(project, prepared, result.text)
    assert load_project(sample).chapters[3].text == PROSE
    assert (result.log_folder.name.startswith("ch03-write"))


def test_story_models_override_app_defaults(sample):
    settings(sample, models__primary="moonshotai/kimi-k3", models__fallback="z-ai/glm-5.3")
    project = load_project(sample)
    prepared = ChapterChat.load(sample, 3).prepare_write(project)
    fake = FakeOpenRouter().queue(reply(model="moonshotai/kimi-k3"))
    asyncio.run(send(prepared, project=project, settings=AppSettings(),
                     catalog=Catalog([parse_model(m) for m in MODELS]), client=fake.client()))
    assert fake.chat_bodies()[0]["model"] == "moonshotai/kimi-k3"


# --- proposing scenes --------------------------------------------------------------------------

def test_scene_proposal_round_trip(sample):
    project = load_project(sample)
    request = prepare_scene_proposal(project, 3)
    system, user = request.messages[0].content, request.messages[1].content
    assert "Reply with JSON only" in system and "Plan the scenes for chapter 3" in user
    assert "Write chapter 3" not in user and "<style_guide>" in user

    reply_text = ('Here you go:\n```json\n{"scenes": [{"title": "The climb", "beats": ["They climb at low tide."],'
                  ' "pov": "Mara Venn", "characters": ["Tobias Reed"]}, {"title": "The case",'
                  ' "beats": ["Tobias talks about her father.", "Mara finds the key."], "pov": "Tobias Reed"}]}\n```')
    scenes = parse_scene_proposal(reply_text)
    assert [s.title for s in scenes] == ["The climb", "The case"] and scenes[1].pov == "Tobias Reed"
    apply_scene_proposal(project, 3, scenes)

    project = load_project(sample)
    chapter = project.outline.chapter(3)
    assert [s.title for s in chapter.scenes] == ["The climb", "The case"]
    assert chapter.scenes[0].settings == {"characters": ["Tobias Reed"]}  # POV same as the chapter's: not repeated
    assert chapter.scenes[1].settings == {"pov": "Tobias Reed"}
    assert project.outline.chapter(4).title == "The Missing Page"  # the next chapter is untouched
    assert run_plan_check(project).issues == []
    with pytest.raises(ValueError, match="already has scenes"):
        apply_scene_proposal(project, 3, scenes)


@pytest.mark.parametrize("bad", ["no json here", "{not valid}", '{"scenes": []}'])
def test_scene_proposal_bad_replies(bad):
    with pytest.raises(ValueError):
        parse_scene_proposal(bad)


def test_scene_proposal_into_last_chapter(sample):
    project = load_project(sample)
    outline = (sample / "outline.md").read_text() + "\n# Chapter 5: Coda\n- Everyone meets at the inn.\n"
    write(sample, "outline.md", outline)
    project = load_project(sample)
    apply_scene_proposal(project, 5, [ProposedScene("The inn", ["They meet."])])
    text = (sample / "outline.md").read_text()
    assert text.endswith("- Everyone meets at the inn.\n\n## Scene 1: The inn\n- They meet.\n")
