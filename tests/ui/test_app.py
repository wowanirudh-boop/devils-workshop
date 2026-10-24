"""Clicking through the real screens with NiceGUI's simulated user, against a fake OpenRouter."""

import asyncio
import sys
from pathlib import Path

import pytest
from nicegui import ui
from nicegui.testing.user_simulation import user_simulation

from devils_workshop.llm.catalog import Catalog
from devils_workshop.project.project import load_project
from devils_workshop.settings import ProviderChoice
from devils_workshop.ui.state import STATE

from tests.conftest import write
from tests.fakes import TEST_KEY, FakeOpenRouter, reply

MAIN = Path(__file__).parent / "app_main.py"
DRAFT = ("The tide had gone out and left the harbour wall standing in its own reflection. Mara and Tobias "
         "climbed the forty-one iron steps together, counting under their breath.")


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DW_SETTINGS_DIR", str(tmp_path / "settings"))
    monkeypatch.setenv("OPENROUTER_API_KEY", TEST_KEY)
    # Where "New story" saves by default; Windows ignores HOME, so patch the function itself.
    monkeypatch.setattr("devils_workshop.ui.home.default_stories_folder", lambda: tmp_path / "stories")
    fake = FakeOpenRouter()
    STATE.root, STATE.settings, STATE.catalog, STATE.catalog_notes = None, None, Catalog([]), []
    STATE.client_factory = lambda key: fake.client(key)
    yield fake
    STATE.root = None


def click(user, label: str) -> None:
    """Click the button with this label (plain find() would also match other text containing it)."""
    user.find(kind=ui.button, content=label).click()


async def settled(user, path: str, tries: int = 100) -> None:
    """Wait until a page shown again (after a save) has been opened again, so a later open() isn't undone by it."""
    count = user.back_history.count(path)
    for _ in range(tries):
        await asyncio.sleep(0.02)
        if user.back_history.count(path) >= count and user.back_history[-1:] == [path] and \
                not any(t.get_name().startswith("navigate reload") for t in asyncio.all_tasks()):
            return


def simulate(steps):
    async def go():
        async with user_simulation(main_file=MAIN) as user:
            await steps(user)
    asyncio.run(go())


def test_open_sample_and_see_dashboard(app_env, tmp_path):
    async def steps(user):
        await user.open("/")
        await user.should_see("Your stories")
        user.find(marker="start-try-the-sample-story").click()
        await user.should_see("Save it in")
        click(user, "Open the sample")
        await user.should_see("Next step")
        await user.should_see("Chapter 2 · Ink and Salt")  # chapter 1 comes written
        for title in ("The Stopped Clock", "Ink and Salt", "Low Tide", "The Missing Page"):
            await user.should_see(title)
        await user.should_see("Subplots")

    simulate(steps)
    assert (tmp_path / "stories" / "The Saltmarsh Clock" / "Story settings.md").is_file()


def open_sample(tmp_path) -> Path:
    from devils_workshop.project.newproject import create_project
    root = create_project(tmp_path / "story", sample=True)
    STATE.open_story(root)
    return root


def test_write_a_chapter_then_ask_for_a_change(app_env, tmp_path):
    root = open_sample(tmp_path)
    app_env.queue(reply(DRAFT), reply("A warmer version. " + DRAFT))

    async def steps(user):
        await user.open("/story/chapter/3")
        await user.should_see("Low Tide")
        await user.should_see("What this chapter should do")
        click(user, "Write chapter 3")
        await user.should_see("What's included")
        await user.should_see("POV character")
        user.find(marker="send").click()
        await user.should_see("Saved as version 1", retries=50)
        await user.should_see(marker="change-request", retries=50)  # the page reloads with the text first

        user.find(marker="change-request").type("Make Tobias warmer.")
        click(user, "Review & send")
        await user.should_see("What's included")
        user.find(marker="send").click()
        await user.should_see("Saved as version 2", retries=50)

    simulate(steps)
    chapter = load_project(root).chapters[3]
    assert chapter.text.startswith("A warmer version.") and chapter.version == 2
    first, second = app_env.chat_bodies()
    assert first["messages"][1]["content"].endswith("</task>")
    assert [m["role"] for m in second["messages"]] == ["system", "user", "assistant", "user"]
    assert "Change now:\nMake Tobias warmer." in second["messages"][3]["content"]


def test_failed_request_saves_nothing(app_env, tmp_path):
    root = open_sample(tmp_path)
    app_env.queue(reply("I'm sorry, but I can't help with that."),
                  reply("As an AI, I must decline.", model="moonshotai/kimi-k3"))

    async def steps(user):
        await user.open("/story/chapter/3")
        click(user, "Write chapter 3")
        await user.should_see("What's included")
        user.find(marker="send").click()
        await user.should_see("Both models failed", retries=50)

    simulate(steps)
    assert 3 not in load_project(root).chapters


def test_settings_and_other_screens_render(app_env, tmp_path):
    open_sample(tmp_path)

    async def steps(user):
        await user.open("/settings")
        await user.should_see("OpenRouter connection")
        await user.should_see("Using the OPENROUTER_API_KEY environment variable.")
        await user.should_see("Main model")
        for path, text in [("/story/files", "Every change keeps a copy"), ("/story/prompts", "Save system prompt"),
                           ("/story/check", "No problems found."), ("/story/costs", "No requests yet."),
                           ("/story/logs", "No requests yet."), ("/story/activity", "No requests yet."),
                           ("/story/history", "No changes yet."), ("/story", "Next step"),
                           ("/story/read", "Save as PDF…")]:
            await user.open(path)
            await user.should_see(text)

    simulate(steps)


def test_the_writer_and_the_planner_each_choose_their_models(app_env, tmp_path):
    """Settings shows the writer's and the planner's models side by side, and each saves its own. The planner's
    Providers… choice is its own, even for the model the writer also uses."""
    from devils_workshop.planner.output import create_planner_story
    from devils_workshop.ui import planner as planner_screen
    from tests.unit.test_planner_agent import FIRST

    planner_screen.CHATS.clear()
    planner_screen.PENDING.clear()
    app_env.endpoints["z-ai/glm-5.3"] = [
        {"provider_name": "DeepInfra", "tag": "deepinfra/fp4", "quantization": "fp4", "status": 0,
         "pricing": {"prompt": "0.0000004", "completion": "0.0000016"}, "context_length": 202752}]
    app_env.queue(reply(FIRST))
    STATE.open_story(create_planner_story(tmp_path / "Lamp", "Lamp"))

    async def steps(user):
        await user.open("/settings")
        await user.should_see("Writer models")
        await user.should_see("Planner models")
        user.find(marker="providers-open-planner").click()  # the planner's main model's (GLM, as the writer's)
        await user.should_see("For the planner.", retries=50)
        await user.should_see("DeepInfra · fp4", retries=50)
        user.find(marker="providers-mode").elements.pop().value = "throughput"
        user.find(marker="providers-save").click()
        await user.should_see("Providers saved")
        user.find(marker="planner-fallback").elements.pop().value = None
        click(user, "Save settings")
        await user.should_see("Settings saved")
        await settled(user, "/settings")  # the page is shown again: let that land before going on

        await user.open("/story/planner")
        await user.should_see("No fallback model")
        user.find(marker="planner-message").type("A keeper's daughter; the lamp signals at 2 a.m.")
        user.find(marker="planner-send").click()
        await user.should_see("Give Maren a craft habit", retries=50)

        await user.open("/story")
        await user.should_see(marker="overview-models-planner")
        await user.should_see(marker="overview-models-writer")

    simulate(steps)
    settings = STATE.app_settings()
    assert settings.models_of("planner") == ("z-ai/glm-5.3", None)
    assert settings.models_of("writer") == ("z-ai/glm-5.3", "moonshotai/kimi-k3")  # the writer's are unchanged
    glm = settings.model("z-ai/glm-5.3").providers
    assert glm.planner.mode == "throughput" and glm.writer.mode == "auto"
    planned = app_env.chat_bodies()[0]
    assert planned["model"] == "z-ai/glm-5.3" and planned["provider"] == {"sort": "throughput"}


def test_story_settings_as_a_form(app_env, tmp_path):
    from devils_workshop.project.story_settings import read_settings
    root = open_sample(tmp_path)

    async def steps(user):
        await user.open("/story/files?tab=settings")
        await user.should_see("How the writer writes it")
        user.find(marker="settings-genre").elements.pop().value = "Harbour mystery"
        user.find(marker="settings-status").elements.pop().value = "completed"
        user.find(marker="settings-save").click()
        await user.should_see("Story settings saved")

    simulate(steps)
    data = read_settings(root)
    assert data["genre"] == "Harbour mystery" and data["status"] == "completed" and data["completed_on"]
    assert load_project(root).config.genre == "Harbour mystery"


def test_ask_ai_to_change_a_file_but_never_the_storys_text(app_env, tmp_path):
    root = open_sample(tmp_path)
    app_env.queue(reply('<edit section="Appearance">\nAI-EDIT-MARKER Salt-grey hair.\n</edit>'
                        "<summary>Grey hair now.</summary>"))

    async def steps(user):
        await user.open("/story/files?path=chapters/ch01.md")
        await user.should_see("Every change keeps a copy")
        await user.should_not_see(marker="ai-edit")  # never the story's text
        await user.open("/story/files?path=characters/mara-venn.md")
        await user.should_see(marker="ai-edit")
        user.find(marker="ai-edit").click()
        await user.should_see("Ask AI to change mara-venn.md")
        user.find(marker="ai-edit-request").type("Give her grey hair")
        user.find(marker="ai-edit-propose").click()
        await user.should_see("Grey hair now.", retries=50)
        await user.should_see(marker="ai-edit-diff")
        user.find(marker="ai-edit-accept").click()
        await user.should_see("mara-venn.md changed")

    simulate(steps)
    assert "AI-EDIT-MARKER" in (root / "characters" / "mara-venn.md").read_text(encoding="utf-8")
    assert app_env.chat_bodies()[0]["temperature"] == 0.5  # the planner's options


def test_read_a_chapter_ask_for_changes_approve_and_write_the_next(app_env, tmp_path):
    from devils_workshop.planner.output import create_planner_story
    from devils_workshop.ui import auto as auto_screen
    from devils_workshop.ui import review_panel
    from tests.unit.test_reader_loop import answers, planned, write
    review_panel.TASKS.clear()
    auto_screen.CURRENT.clear()
    STATE.catalog = SEEING_CATALOG()
    root = create_planner_story(tmp_path / "Salt", "Salt and Iron")
    fake = planned(root)
    write(root, fake, [1])
    app_env.respond(answers)
    STATE.open_story(root)

    async def steps(user):
        await user.open("/story/read?chapter=1")
        await user.should_see(marker="review-panel")
        user.find(marker="review-request").type("Make the argument colder")
        user.find(marker="review-send").click()
        await user.should_see("The change, waiting for your approval", retries=80)
        await user.should_see("CHANGE-MARKER make the argument colder")
        await user.should_see("Later chapters: She trusts Tobias less from now on.")
        user.find(marker="review-approve").click()
        await user.should_see("Changed scenes 1, 2", retries=80)
        await user.should_see("REVISED-PROSE", retries=50)
        user.find(marker="review-next").click()
        await user.should_see("Chapter 2", retries=150)
        for _ in range(600):  # each scene written, then checked: wait for the run to end
            run = next(iter(review_panel.auto.CURRENT.values()), None)
            if run is not None and not run.running:
                break
            await asyncio.sleep(0.05)
        assert run is not None and run.status == "finished", run and run.reason

    simulate(steps)
    project = load_project(root)
    assert project.chapters[1].scenes[1].startswith("REVISED-PROSE")
    assert sorted(project.chapters[2].scenes) == [1, 2]


def SEEING_CATALOG():
    from tests.unit.test_planner import SEEING
    return SEEING


def test_the_library_in_progress_and_completed_sorted_listed_and_new_versions(app_env, tmp_path):
    from devils_workshop.library import set_completed
    from devils_workshop.project.newproject import create_project
    from devils_workshop.project.story_settings import update_settings
    for name, genre in (("Bravo", "Gothic"), ("Alpha", "Space opera")):
        root = create_project(tmp_path / name, title=name)
        update_settings(root, genre=genre, characters=[f"{name} Lead"])
        STATE.open_story(root)
    set_completed(str(tmp_path / "Bravo"), True)

    async def steps(user):
        await user.open("/")
        await user.should_see("In progress (1)")
        await user.should_see("Completed (1)")
        await user.should_see("Alpha")
        await user.should_see("Space opera")
        await user.should_not_see("Bravo")
        await user.open("/?tab=completed&q=space")
        await user.should_see("No stories match your search.")
        await user.open("/?tab=completed&view=list")
        await user.should_see(marker="library-list")
        await user.should_see("Gothic")
        await user.should_see("Bravo Lead")
        user.find(marker="story-version").click()
        await user.should_see("Start a new version of Bravo?")
        user.find(marker="story-version-go").click()
        await user.should_see("Version started", retries=50)

    simulate(steps)
    assert (tmp_path / "Bravo (version 2)" / "Story settings.md").is_file()


def test_story_screens_without_a_story_send_you_home(app_env):
    async def steps(user):
        await user.open("/story/activity")
        await user.should_see("No story is open.")
        await user.should_see("Choose a story")

    simulate(steps)


def own_folder(tmp_path) -> Path:
    root = tmp_path / "My Story"
    write(root, "World.md", "WORLD-MARKER Mara Venn keeps the harbour clock.")
    write(root, "Plot outline.md", "Chapter 1: The Stopped Clock\nCH1-MARKER The clock has stopped.\n\n"
                                   "Chapter 2: Ink and Salt\nCH2-MARKER Ada shows Mara the ledger room.\n")
    return root


def test_open_your_own_folder_set_it_up_and_write(app_env, tmp_path):
    root = own_folder(tmp_path)
    app_env.queue(reply(DRAFT))

    async def steps(user):
        await user.open("/")
        click(user, "Open a folder")
        user.find(marker="folder-path").type(f'"{root}"')  # with the quotes Windows' "Copy as path" adds
        user.find(marker="open-folder").click()
        await user.should_see("Use this folder for a story")
        await user.should_see("Plot outline.md")
        await user.should_see("Chapters: 1–2")
        click(user, "Save and open")
        await user.should_see("Next step")
        await user.should_see("Chapter 1 · The Stopped Clock")
        await user.should_see("Ink and Salt")

        await user.open("/story/chapter/2")
        click(user, "Write chapter 2")
        await user.should_see("What's included")
        await user.should_see("World.md")
        user.find(marker="send").click()
        await user.should_see("Saved as version 1", retries=50)
        await user.should_see(marker="change-request", retries=50)  # the page has reloaded

        await user.open("/story/setup")
        await user.should_see("What's sent")

    simulate(steps)
    assert "## Files" in (root / "Story settings.md").read_text(encoding="utf-8")
    assert load_project(root).chapters[2].text.startswith("The tide had gone out")
    sent = app_env.chat_bodies()[0]["messages"][1]["content"]
    assert "WORLD-MARKER" in sent and "CH2-MARKER" in sent and "CH1-MARKER" not in sent


def test_the_folder_holding_your_stories_offers_them(app_env, tmp_path):
    from devils_workshop.project.newproject import create_project
    create_project(tmp_path / "stories" / "The Saltmarsh Clock", sample=True)

    async def steps(user):
        await user.open("/")
        click(user, "Open a folder")
        user.find(marker="folder-path").type(str(tmp_path / "stories"))
        user.find(marker="open-folder").click()
        await user.should_see("This folder holds these stories")
        user.find(marker="open-story").click()
        await user.should_see("Next step")

    simulate(steps)


def test_auto_write_a_range_then_read_it(app_env, tmp_path):
    root = open_sample(tmp_path)
    more = "The bell rang twice from the drowned church, and then the island was quiet again. " * 4
    app_env.queue(reply("AUTO-THREE " + DRAFT, finish="length"), reply(more))  # cut off short: continued once

    async def steps(user):
        await user.open("/story/auto")
        await user.should_see("Start auto-write")
        user.find(marker="auto-from").elements.pop().value = 3
        user.find(marker="auto-to").elements.pop().value = 3
        await user.should_see("1 chapter to write")
        click(user, "Start auto-write")
        await user.should_see("Finished", retries=50)
        assert len(user.find(marker="auto-step").elements) == 1
        await user.should_see("continued once")

        await user.open("/story/read?chapter=3")
        await user.should_see("Save as PDF…")
        pages = [e.content for e in user.find(kind=ui.html).elements if "AUTO-THREE" in e.content]
        assert pages and "Low Tide" in pages[0]
        user.find(marker="reader-save").click()  # a PDF book, as offered
        await user.should_see("A book: title page, contents")
        user.find(marker="reader-save-go").click()
        await user.should_see(marker="reader-saved", retries=50)
        click(user, "Close")
        user.find(marker="reader-save").click()  # and one web page
        await user.should_see("Page size")
        user.find(marker="reader-kind").elements.pop().value = "html"
        user.find(marker="reader-save-go").click()
        for _ in range(100):  # the web page is saved in the background
            if (root / "exports" / "The Saltmarsh Clock.html").is_file():
                break
            await asyncio.sleep(0.05)

    simulate(steps)
    assert load_project(root).chapters[3].text.startswith("AUTO-THREE")
    assert (root / "exports" / "The Saltmarsh Clock.html").is_file()
    pdf = (root / "exports" / "The Saltmarsh Clock - chapter 3.pdf").read_bytes()  # opened at chapter 3
    assert pdf.startswith(b"%PDF") and len(pdf) > 2000
    assert len(app_env.chat_bodies()) == 2  # no review stop: the chapter, then one continuation


def test_write_all_scenes_from_the_chapter_screen(app_env, tmp_path):
    """Chosen in Auto-write: scenes written at the same time, each continued once, then the joins smoothed."""
    root = open_sample(tmp_path)
    STATE.app_settings().scenes_together = True
    scene = "The archive window looked out on the black water of the harbour. " * 12
    more = "Somewhere below, the tide turned against the harbour wall and kept on turning. " * 3
    smoothed = "SCENE-TWO-SMOOTHED They left the archive together and went down to the water. " * 5

    def answer(body):
        task = body["messages"][-1]["content"]
        if "<continue>" in task:
            return reply(more)
        if "Rewrite the opening of scene 2" in task:
            return reply(f'<opening scene="2">\n{smoothed}\n</opening>')
        return reply(("SCENE-ONE " if "Write scene 1 of 2" in task else "SCENE-TWO ") + scene, finish="length")
    app_env.respond(answer)

    async def steps(user):
        await user.open("/story/chapter/2")
        await user.should_see("Write all scenes automatically")
        click(user, "Write all scenes automatically")
        await user.should_see("Finished", retries=50)
        await user.should_see("smooth the joins between scenes")

    simulate(steps)
    scenes = load_project(root).chapters[2].scenes
    assert scenes[1].startswith("SCENE-ONE") and scenes[2].startswith("SCENE-TWO-SMOOTHED")
    assert scenes[2].endswith("kept on turning.")  # the rest of scene 2 is as written
    bodies = app_env.chat_bodies()
    scene_two = next(b for b in bodies if "Write scene 2 of 2" in b["messages"][-1]["content"])["messages"]
    assert [m["role"] for m in scene_two] == ["system", "user"]  # its own chat, with scene 1 as beats
    assert len(bodies) == 5  # two scenes, a continuation of each, and the joins


def test_default_instructions_added_once_used_by_every_story(app_env, tmp_path):
    root = open_sample(tmp_path)
    app_env.queue(reply(DRAFT))

    async def steps(user):
        await user.open("/defaults")
        await user.should_see("No default instructions yet.")
        click(user, "Write one here")
        await user.should_see("Write a default instruction")
        user.find(marker="default-name").elements.pop().value = "House style"
        user.find(marker="default-create").click()
        await user.should_see("House style.md")
        user.find(marker="default-editor").elements.pop().value = "DEFAULT-STYLE-MARKER Short sentences only."
        user.find(marker="default-save").click()
        await user.should_see("4 words")

        await user.open("/story/chapter/3")
        click(user, "Write chapter 3")
        await user.should_see("Your default instructions")  # its own group in What's included
        user.find(marker="send").click()
        await user.should_see("Saved as version 1", retries=50)
        await user.should_see(marker="change-request", retries=50)  # the page has reloaded

        await user.open("/story/files?tab=prompt")
        await user.should_see("House style.md (writer and planner)")
        user.find(marker="story-defaults-switch").elements.pop().value = False
        await user.should_see("no longer uses your default instructions")

    simulate(steps)
    system = app_env.chat_bodies()[0]["messages"][0]["content"]  # instructions to follow, after the built-in ones
    assert "DEFAULT-STYLE-MARKER" in system and system.index("Reply with the prose only") < system.index("DEFAULT")
    assert "DEFAULT-STYLE-MARKER" not in app_env.chat_bodies()[0]["messages"][1]["content"]
    assert "- Default instructions: off" in (root / "Story settings.md").read_text(encoding="utf-8")
    assert (tmp_path / "settings" / "Default instructions" / "House style.md").is_file()


def upload(user, marker: str, name: str, data: bytes):
    """Drop a file on the upload box with this marker (as the browser would send it)."""
    box = user.find(marker=marker).elements.pop()
    return box.handle_uploads([ui.upload.SmallFileUpload(name=name, content_type="text/plain", _data=data)])


def test_add_rename_and_delete_your_own_files(app_env, tmp_path):
    root = own_folder(tmp_path)
    from devils_workshop.project.own_files import guess_role, settings_text
    roles = {name: guess_role(name, (root / name).read_text()) for name in ("World.md", "Plot outline.md")}
    write(root, "story.yaml", settings_text(roles, "My Story", 3000))
    STATE.open_story(root)

    async def steps(user):
        await user.open("/story/files")
        user.find(marker="files-add").click()
        await user.should_see("Add files")
        await upload(user, "files-upload", "Chapter 3 notes.md", b"CH3-MARKER The tide goes out.")
        await user.should_see("Added Chapter 3 notes.md (sent as: chapter 3 only)")
        user.find(marker="files-add-done").click()
        await user.should_see("What's sent")  # where you can change what the new file is for

        await user.open("/story/files?path=Chapter%203%20notes.md")
        user.find(marker="file-rename").click()
        await user.should_see("Rename Chapter 3 notes.md")
        user.find(marker="rename-name").elements.pop().value = "Low tide notes ch3"
        user.find(marker="rename-go").click()
        await user.should_see("Renamed to Low tide notes ch3.md")

        await user.open("/story/files?path=World.md")
        user.find(marker="file-delete").click()
        await user.should_see("Delete World.md?")
        user.find(marker="delete-go").click()
        await user.should_see("Deleted World.md")

        await user.open("/story/history")
        await user.should_see("Deleted World.md")
        await user.should_see("Renamed Chapter 3 notes.md → Low tide notes ch3.md")
        await user.should_see("Added Chapter 3 notes.md")

    simulate(steps)
    listed = load_project(root).config.own_files
    assert not (root / "World.md").exists() and "World.md" not in listed
    assert (root / "Low tide notes ch3.md").read_text() == "CH3-MARKER The tide goes out."
    assert listed["Low tide notes ch3.md"] == "chapter 3"


def test_add_a_character_to_a_story_made_by_the_app(app_env, tmp_path):
    root = open_sample(tmp_path)

    async def steps(user):
        await user.open("/story/files")
        user.find(marker="files-add").click()
        await user.should_see("Add it as")
        await upload(user, "files-upload", "Old Bess.txt", b"## Profile\nA lighthouse keeper who counts ships.")
        await user.should_see("Added Old Bess.txt as a character")
        user.find(marker="files-add-done").click()
        await user.should_see("characters/old-bess.md")

    simulate(steps)
    assert "Old Bess" in [e.name for e in load_project(root).entries]


def test_delete_a_story_made_by_mistake(app_env, tmp_path, monkeypatch):
    import shutil

    from devils_workshop.project.newproject import create_project
    keep = create_project(tmp_path / "stories" / "Keep me", title="Keep me")
    oops = create_project(tmp_path / "stories" / "Oops", title="Oops")
    STATE.open_story(oops)
    binned = []
    monkeypatch.setattr("devils_workshop.ui.home.move_to_recycle_bin",
                        lambda folder: (binned.append(folder), shutil.rmtree(folder))[-1])  # None: Windows

    async def steps(user):
        await user.open("/")
        await user.should_see("Oops")
        user.find(marker="story-delete").click()
        await user.should_see("Delete Oops?")
        await user.should_see("go to the Recycle Bin" if sys.platform == "win32" else "move to")  # Linux: Trash
        user.find(marker="story-delete-go").click()
        await user.should_see("Oops was moved to the Recycle Bin")
        await user.should_not_see(marker="story-card")

        STATE.open_story(keep)
        await user.open("/")
        user.find(marker="story-delete").click()
        await user.should_see("Delete Keep me?")
        user.find(marker="story-forget-only").click()
        await user.should_see("its folder is still there")

    simulate(steps)
    assert binned == [oops] and not oops.exists() and keep.is_dir()
    assert STATE.app_settings().recent_projects == []


def test_any_text_file_as_an_instruction_source(app_env, tmp_path):
    root = open_sample(tmp_path)
    app_env.queue(reply(DRAFT))

    async def steps(user):
        await user.open("/defaults")
        click(user, "Add files…")
        await user.should_see("Add files")
        await upload(user, "default-upload", "Rules.json", b'{"rule": "JSON-RULE-MARKER no adverbs"}')
        await upload(user, "default-upload", "cover.png", b"\x89PNG\r\n\x1a\n\x00\x00")
        click(user, "Done")
        await user.should_see("Rules.json")
        await user.should_see("cover.png: This isn't a text file")

        await user.open("/story/files")
        user.find(marker="files-add").click()
        await upload(user, "files-upload", "harbour.yaml", b"tides: YAML-LORE-MARKER twice a day")
        await user.should_see("Added harbour.yaml as a character")
        user.find(marker="files-add-done").click()
        await user.should_see("characters/harbour.md")

        await user.open("/story/chapter/3")
        click(user, "Write chapter 3")
        await user.should_see("Rules.json")  # in What's included
        user.find(marker="send").click()
        await user.should_see("Saved as version 1", retries=50)

    simulate(steps)
    sent = app_env.chat_bodies()[0]["messages"][0]["content"]  # a default instruction: in the system message
    assert "JSON-RULE-MARKER" in sent
    assert not (tmp_path / "settings" / "Default instructions" / "cover.png").exists()
    assert "YAML-LORE-MARKER" in (root / "characters" / "harbour.md").read_text(encoding="utf-8")


def test_plan_a_story_from_an_idea_then_write_it(app_env, tmp_path):
    from devils_workshop.planner import run as planner_run
    from devils_workshop.planner.state import PlannerState
    from devils_workshop.ui import planner as planner_screen
    from tests.unit.test_planner import PNG, SEEING, planner_answers
    planner_run.RUNS.clear()
    planner_screen.CHATS.clear()
    planner_screen.PENDING.clear()
    STATE.catalog = SEEING
    app_env.respond(planner_answers)

    async def steps(user):
        await user.open("/")
        user.find(marker="start-plan-a-new-story").click()
        await user.should_see("Working title")
        user.find(marker="plan-title").type("Salt and Iron")
        user.find(marker="plan-create").click()
        await user.should_see("Stage 1 of 2")
        click(user, "Attach…")
        await user.should_see(marker="planner-upload")
        await upload(user, "planner-upload", "Idea.md", b"IDEA-MARKER A clockmaker's daughter on a tidal island.")
        await upload(user, "planner-upload", "Mara.png", PNG)
        user.find(marker="planner-upload-done").click()
        await user.should_see("Mara.png")  # attached, shown under the message box
        user.find(marker="planner-message").type("Here's my idea.")
        user.find(marker="planner-send").click()
        await user.should_see("Give Mara a secret", retries=50)  # the planner's suggestions
        await user.should_see("Attached: Idea.md, Mara.png")
        user.find(marker="planner-approve").click()
        await user.should_see("Approve the plan and plan the chapters?")
        user.find(marker="planner-approve-go").click()
        await user.should_see(marker="planner-start-writing", retries=100)
        await user.should_see("2 chapters, about 6,000 words, 4 scenes")
        user.find(marker="planner-start-writing").click()
        await user.should_see("Next step")
        await user.open("/story/chapter/1")
        await user.should_see("The Stopped Clock")
        await user.should_see("C1 first")

    simulate(steps)
    root = tmp_path / "stories" / "Salt and Iron"
    notes, chat = app_env.chat_bodies()[0]["messages"][1]["content"], app_env.chat_bodies()[1]["messages"][1]["content"]
    assert notes[1]["type"] == "image_url"  # the picture was seen, once, for its notes
    assert "IDEA-MARKER" in chat and "NOTES-MARKER on Mara.png" in chat  # then the notes went with the chat
    assert PlannerState.load(root).stage == "done"
    assert [c.title for c in load_project(root).outline.chapters] == ["The Stopped Clock", "Low Tide"]


def test_a_story_read_from_a_web_page_becomes_the_story_it_adapts(app_env, tmp_path, monkeypatch):
    from devils_workshop.planner import webpage
    from devils_workshop.planner.state import PlannerState
    from devils_workshop.ui import planner as planner_screen
    planner_screen.CHATS.clear()
    planner_screen.PENDING.clear()
    asked = []

    def read_page(url, saved=None):
        asked.append(url)
        if "broken" in url:
            raise webpage.WebPageError("That page could not be found. Check the link and try again.")
        return webpage.WebPage("The Lamp Keeper", url, "WEB-MARKER Mara climbed the iron steps.", 1200, pages=2)

    monkeypatch.setattr(webpage, "read_page", read_page)

    async def steps(user):
        await user.open("/")
        user.find(marker="start-plan-a-new-story").click()
        await user.should_see("Working title")
        user.find(marker="plan-title").type("Lamps")
        user.find(marker="plan-create").click()
        await user.should_see("Stage 1 of 2")
        user.find(marker="planner-web-page").click()
        await user.should_see("Read a story from a web page")
        user.find(marker="planner-web-url").type("https://tales.example.com/broken")
        user.find(marker="planner-web-fetch").click()
        await user.should_see("That page could not be found", retries=50)  # the extractor's own reason
        user.find(marker="planner-web-url").clear().type("https://tales.example.com/lamp-keeper")
        user.find(marker="planner-web-fetch").click()
        await user.should_see("The Lamp Keeper.md", retries=50)  # attached to the next message

    simulate(steps)
    root = tmp_path / "stories" / "Lamps"
    state = PlannerState.load(root)
    assert asked == ["https://tales.example.com/broken", "https://tales.example.com/lamp-keeper"]
    assert (state.registry.kind, state.registry.reference) == ("adaptation", "The Lamp Keeper.md")
    kept = (root / "planner" / "inputs" / "The Lamp Keeper.md").read_text(encoding="utf-8")
    assert kept.startswith("# The Lamp Keeper\n\nFrom https://tales.example.com/lamp-keeper, 2 pages joined.")
    assert "WEB-MARKER" in kept and planner_screen.PENDING[planner_screen._key(root)] == ["The Lamp Keeper.md"]


def test_the_planner_asks_about_open_decisions_then_writes_the_guide_first(app_env, tmp_path):
    from devils_workshop.planner import run as planner_run
    from devils_workshop.planner.output import create_planner_story
    from devils_workshop.planner.state import PlannerState
    from devils_workshop.ui import planner as planner_screen
    from tests.unit.test_planner import SEEING, planner_answers
    from tests.unit.test_planner_foundation import FIRST
    planner_run.RUNS.clear()
    planner_screen.CHATS.clear()
    planner_screen.PENDING.clear()
    STATE.catalog = SEEING

    def answers(body):
        if "story development partner" in body["messages"][0]["content"]:
            return reply(FIRST)
        return planner_answers(body)

    app_env.respond(answers)
    root = create_planner_story(tmp_path / "Lamp", "Lamp")
    STATE.open_story(root)

    async def steps(user):
        await user.open("/story/planner")
        user.find(marker="planner-message").type("A lighthouse, a missing father.")
        user.find(marker="planner-send").click()
        await user.should_see("Still to decide (3)", retries=50)
        await user.should_see(marker="planner-agenda")
        await user.should_see("Story plan.md, Decisions.md and Source notes.md")
        user.find(marker="planner-approve").click()
        await user.should_see("3 important decisions are still open")
        user.find(marker="planner-scene-words").elements.pop().value = 1000
        user.find(marker="planner-approve-go").click()
        await user.should_see(marker="planner-start-writing", retries=100)
        await user.should_see("The writer's guide")
        await user.should_see(marker="planner-scene-check_circle")  # chapter 1's briefs are written
        await user.should_see(marker="planner-scene-schedule")  # chapter 2's come just before it is written
        await user.should_not_see(marker="planner-scene-radio_button_unchecked")  # nothing looks missed
        user.find(marker="planner-guide-read").click()
        await user.should_see("GUIDE-MARKER Close third person, past tense.")
        await user.should_see(marker="planner-change-chapter")  # changes are asked for a chapter at a time

        # a brief is changed from where you read it: say what should change, and it is written again
        user.find(marker="planner-brief-1-2").click()
        await user.should_see("BRIEF-MARKER for scene 2 of chapter 1")
        user.find(marker="planner-brief-again").click()
        await user.should_see("Say what should change first")
        user.find(marker="planner-brief-note").type("CHANGE-MARKER she shouldn't cry yet")
        user.find(marker="planner-brief-again").click()
        await user.should_see(marker="planner-start-writing", retries=100)

        # every chapter's briefs up front, instead of just before each chapter is written
        user.find(marker="planner-all-briefs").click()
        await user.should_see("Write all the briefs now?")
        user.find(marker="planner-all-briefs-go").click()
        await user.should_see(marker="planner-start-writing", retries=100)
        await user.should_not_see(marker="planner-scene-schedule", retries=100)
        await user.should_not_see(marker="planner-all-briefs")

        # how long a brief is can be changed at any time; the briefs can then be planned again
        user.find(marker="planner-brief-length").click()
        await user.should_see("How long should a scene's brief be?")
        user.find(marker="planner-brief-length-words").elements.pop().value = 450
        user.find(marker="planner-brief-length-save").click()
        await user.should_see("New briefs will be about 450 words")
        await user.should_see(marker="planner-replan-briefs")

    simulate(steps)
    state = PlannerState.load(root)
    assert state.scene_words == 1000 and state.guide.startswith("## Voice and style")
    changed = state.chapters[0].scenes[1]
    assert changed.status == "ready" and not changed.note and not changed.earlier  # used, then cleared
    sent = [b["messages"][1]["content"] for b in app_env.chat_bodies() if "<brief_to_change>" in
            b["messages"][1]["content"]]
    assert sent and "CHANGE-MARKER" in sent[-1] and "BRIEF-MARKER for scene 2 of chapter 1" in sent[-1]
    assert not state.jit and all(s.status == "ready" for c in state.chapters for s in c.scenes)  # all up front
    assert state.brief_words == 450
    assert (root / "Decisions.md").is_file() and (root / "Writer guide.md").is_file()


def test_every_prompt_can_be_edited_for_every_story_or_one(app_env, tmp_path):
    from devils_workshop.project import instructions
    root = open_sample(tmp_path)

    async def steps(user):
        await user.open("/prompts")
        await user.should_see("The writer's prompts")
        await user.should_see("The planner's prompts")
        await user.should_see("Writing a scene brief")
        user.find(marker="prompt-editor-plan_scene").elements.pop().value = "MINE-MARKER briefs"
        user.find(marker="prompt-save-plan_scene").click()
        await user.should_see("saved for every story")

        await user.open("/story/files?tab=prompt")
        await user.should_see("The writer's prompts")
        user.find(marker="prompt-editor-write").elements.pop().value = "STORY-MARKER write"
        user.find(marker="prompt-save-write").click()
        await user.should_see("saved for this story")

    simulate(steps)
    assert instructions.app_copy("plan_scene").read_text(encoding="utf-8") == "MINE-MARKER briefs"
    assert instructions.source(root, "write") == instructions.STORY
    assert (root / "prompts" / "jobs" / "write.md").read_text(encoding="utf-8") == "STORY-MARKER write"


def test_the_writer_and_the_planner_have_their_own_recommended_options(app_env, tmp_path):
    from devils_workshop.settings import recommended
    open_sample(tmp_path)

    async def steps(user):
        await user.open("/settings")
        await user.should_see(marker="writer-options")
        await user.should_see(marker="planner-options")
        await user.should_see("These are the recommended values.")
        user.find(marker="planner-temperature").elements.pop().value = 0.3
        await user.should_see("Changed from the recommended values")
        click(user, "Save settings")
        await user.should_see("Settings saved")

    simulate(steps)
    settings = STATE.app_settings()
    assert settings.planner_options.temperature == 0.3 and settings.planner_options.effort == "medium"
    assert settings.writer_options == recommended("writer")


def test_add_a_venice_key_then_write_with_a_venice_model(app_env, tmp_path):
    from devils_workshop.ui.state import default_venice
    from tests.fakes import FakeVenice, venice_reply
    venice = FakeVenice().queue(venice_reply("VENICE-MARKER " + DRAFT))
    STATE.venice_factory = lambda key, own_prompt: venice.client(key, own_prompt)
    root = open_sample(tmp_path)

    async def steps(user):
        await user.open("/settings")
        await user.should_see("Venice AI")
        await user.should_see("No Venice key")
        user.find(marker="venice-key").type("venice-test-key-0123456789")
        user.find(marker="venice-save").click()
        await user.should_see("Venice key saved", retries=50)
        assert "venice:mistral-31-24b" in STATE.catalog.models
        STATE.app_settings().primary = "venice:mistral-31-24b"
        STATE.save_settings()

        await user.open("/story/chapter/3")
        click(user, "Write chapter 3")
        await user.should_see("What's included")
        user.find(marker="send").click()
        await user.should_see("Saved as version 1", retries=50)

    try:
        simulate(steps)
    finally:
        STATE.venice_factory = default_venice
    assert load_project(root).chapters[3].text.startswith("VENICE-MARKER")
    sent = venice.chat_bodies()[0]
    assert sent["model"] == "mistral-31-24b" and sent["venice_parameters"]["include_venice_system_prompt"] is False
    assert app_env.chat_bodies() == []  # nothing went to OpenRouter


def test_add_an_experiential_labs_key_choose_its_model_then_write_with_it(app_env, tmp_path):
    from devils_workshop.ui.state import default_explabs
    from tests.unit.test_experiential import MODEL
    from tests.fakes import sse, chunk, usage
    from tests.unit.test_experiential import KEY, FakeGateway
    gateway = FakeGateway()
    gateway.chat.append(sse(chunk("EXPLABS-MARKER " + DRAFT), chunk(finish="stop", usage=usage(cost=0.001))))
    STATE.explabs_factory = lambda key: gateway.client(key)
    root = open_sample(tmp_path)

    async def steps(user):
        await user.open("/settings")
        await user.should_see("No Experiential Labs key")
        user.find(marker="explabs-key").type("not-a-key")
        user.find(marker="explabs-save").click()
        await user.should_see("they start with xpl_")
        user.find(marker="explabs-key").clear().type(KEY)
        user.find(marker="explabs-save").click()
        await user.should_see("Its models are in the lists", retries=50)
        settings = STATE.app_settings()
        assert MODEL in STATE.catalog.models and settings.primary != MODEL  # offered, never chosen for you
        settings.primary = MODEL  # you choose it, as the Writer models list does
        STATE.save_settings()

        await user.open("/story/chapter/3")
        click(user, "Write chapter 3")
        await user.should_see("What's included")
        user.find(marker="send").click()
        await user.should_see("Saved as version 1", retries=50)

    try:
        simulate(steps)
    finally:
        STATE.explabs_factory = default_explabs
    assert load_project(root).chapters[3].text.startswith("EXPLABS-MARKER")
    assert app_env.chat_bodies() == []  # nothing went to OpenRouter


def test_start_with_windows_switch(app_env, monkeypatch):
    from devils_workshop import startup
    calls = []
    monkeypatch.setattr(startup, "supported", lambda: True)
    monkeypatch.setattr(startup, "enabled", lambda: False)
    monkeypatch.setattr(startup, "enable", lambda: calls.append("on"))

    async def steps(user):
        await user.open("/settings")
        await user.should_see("Start with Windows")
        await user.should_see("Phone access is off")
        user.find(marker="startup-switch").click()
        await user.should_see("start in the background when you sign in")

    simulate(steps)
    assert calls == ["on"]


def options_of(user, marker: str = "", label: str = "") -> dict:
    """The choices of a model list on screen: id -> what it shows."""
    found = user.find(marker=marker).elements if marker else user.find(kind=ui.select).elements
    return {k: v for element in found if not label or element.props.get("label") == label
            for k, v in element.options.items()}


def test_add_another_service_then_write_and_plan_with_its_model(app_env, tmp_path, credential_store):
    from devils_workshop.planner.output import create_planner_story
    from devils_workshop.planner import run as planner_run
    from devils_workshop.settings import ServiceSettings
    from devils_workshop.ui import planner as planner_screen
    from devils_workshop.ui.state import default_service
    from tests.fakes import FakeService, service_reply
    from tests.unit.test_planner_agent import FIRST
    llama = "svc-featherless:meta-llama/Llama-3.3-70B-Instruct"
    service = FakeService().queue(service_reply("SERVICE-MARKER " + DRAFT), service_reply(FIRST))
    STATE.service_factory = lambda s, key: service.client(s.name, s.slug, key, s.base_url)
    planner_run.RUNS.clear()
    planner_screen.CHATS.clear()
    planner_screen.PENDING.clear()
    root = open_sample(tmp_path)

    async def steps(user):
        await user.open("/settings")
        await user.should_see("Other services")
        await user.should_see("None added yet.")
        user.find(marker="service-name").type("Featherless")
        user.find(marker="service-url").type("https://api.featherless.ai/v1/")
        user.find(marker="service-key").type("fl-test-key-0123456789abcdef")
        user.find(marker="service-add").click()
        await user.should_see("Featherless added", retries=50)
        await user.should_see("4 models", retries=50)
        assert STATE.app_settings().services == [ServiceSettings("Featherless", "featherless",
                                                                 "https://api.featherless.ai/v1")]
        assert credential_store.passwords[("DevilsWorkshop", "service:featherless")] == "fl-test-key-0123456789abcdef"
        assert options_of(user, label="Fallback model")[llama].startswith("Featherless · meta-llama/Llama-3.3-70B")
        user.find(marker="service-check-featherless").click()
        await user.should_see("Connected. Featherless lists 4 models.", retries=50)
        STATE.app_settings().primary = STATE.app_settings().planner_primary = llama
        STATE.save_settings()

        await user.open("/story/chapter/3")
        click(user, "Write chapter 3")
        await user.should_see("What's included")
        user.find(marker="send").click()
        await user.should_see("Saved as version 1", retries=50)

        lamp = create_planner_story(tmp_path / "Lamp", "Lamp")
        STATE.open_story(lamp)
        await user.open("/story/planner")
        assert options_of(user, marker="planner-model")[llama] == "Featherless · meta-llama/Llama-3.3-70B-Instruct"
        user.find(marker="planner-message").type("A keeper's daughter; the lamp signals at 2 a.m.")
        user.find(marker="planner-send").click()
        await user.should_see("Give Maren a craft habit", retries=50)

        await user.open("/settings")
        user.find(marker="service-remove-featherless").click()
        await user.should_see("Featherless removed", retries=50)
        await user.should_see("None added yet.", retries=50)

    try:
        simulate(steps)
    finally:
        STATE.service_factory = default_service
    assert load_project(root).chapters[3].text.startswith("SERVICE-MARKER")
    written, planned = service.chat_bodies()
    assert written["model"] == planned["model"] == "meta-llama/Llama-3.3-70B-Instruct" and "provider" not in written
    assert service.keys[-1] == "fl-test-key-0123456789abcdef"
    assert app_env.chat_bodies() == []  # nothing went to OpenRouter
    assert STATE.app_settings().services == [] and ("DevilsWorkshop", "service:featherless") not in \
        credential_store.passwords


def test_choose_providers_for_a_model(app_env, tmp_path):
    app_env.endpoints["z-ai/glm-5.3"] = [
        {"provider_name": "DeepInfra", "tag": "deepinfra/fp4", "quantization": "fp4", "status": 0,
         "pricing": {"prompt": "0.0000004", "completion": "0.0000016"}, "context_length": 202752},
        {"provider_name": "Z.ai", "tag": "z-ai/fp8", "quantization": "fp8", "status": 0,
         "pricing": {"prompt": "0.0000006", "completion": "0.0000022"}, "context_length": 202752,
         "throughput_last_30m": {"p50": 74.0}},
    ]
    open_sample(tmp_path)
    app_env.queue(reply(DRAFT))

    async def steps(user):
        await user.open("/settings")
        await user.should_see("Providers: OpenRouter chooses")
        from nicegui.testing.user_interaction import UserInteraction
        main = min(user.find(marker="providers-open").elements, key=lambda e: e.id)  # the main model's (first)
        UserInteraction(user, {main}, None).click()
        await user.should_see("DeepInfra · fp4", retries=50)
        await user.should_see("74 tok/s (OpenRouter, last 30 min)")
        await user.should_see("speed: no data yet")
        await user.should_see("per 3,000-word chapter")
        user.find(marker="providers-mode").elements.pop().value = "mine"
        for _ in range(2):  # mark both rows Prefer (each mark redraws the rows)
            row = next(e for e in user.find(marker="providers-choice").elements if not e.value)
            row.value = "prefer"
        user.find(marker="providers-fallbacks").elements.pop().value = False
        user.find(marker="providers-save").click()
        await user.should_see("Providers saved")

        await user.open("/story/chapter/3")
        click(user, "Write chapter 3")
        await user.should_see("What's included")
        user.find(marker="send").click()
        await user.should_see("Saved as version 1", retries=50)

    simulate(steps)
    choices = STATE.app_settings().model("z-ai/glm-5.3").providers
    choice = choices.writer  # the first Providers… is the writer's main model's
    assert choice.mode == "mine" and sorted(choice.prefer) == ["deepinfra/fp4", "z-ai/fp8"]
    assert choices.planner == ProviderChoice()  # the planner's, for the same model, are its own
    provider = app_env.chat_bodies()[0]["provider"]
    assert sorted(provider["order"]) == ["deepinfra/fp4", "z-ai/fp8"] and provider["allow_fallbacks"] is False


def test_scenes_are_written_one_after_another_by_default(app_env, tmp_path):
    """Each scene is written after the one before, in the chapter's one chat, so it follows on from its text."""
    root = open_sample(tmp_path)
    assert STATE.app_settings().scenes_together is False
    one = "SCENE-ONE-TEXT " + "The archive window looked out on the black water of the harbour. " * 120
    two = "SCENE-TWO-TEXT " + "They left the archive together and went down to the water. " * 120
    app_env.queue(reply(one), reply(two))

    async def steps(user):
        await user.open("/story/chapter/2")
        click(user, "Write all scenes automatically")
        await user.should_see("Finished", retries=50)

    simulate(steps)
    scenes = load_project(root).chapters[2].scenes
    assert scenes[1].startswith("SCENE-ONE-TEXT") and scenes[2].startswith("SCENE-TWO-TEXT")
    second = app_env.chat_bodies()[1]["messages"]
    assert [m["role"] for m in second][-3:] == ["user", "assistant", "user"]  # scene 1's text, then scene 2's task
    assert "SCENE-ONE-TEXT" in second[-2]["content"]
    assert len(app_env.chat_bodies()) == 2  # no joins to smooth


def test_a_document_sent_twice_is_caught(app_env, tmp_path):
    from devils_workshop.project.own_files import OUTLINE
    from tests.unit.test_duplicates_targets import handoff_story

    fresh = handoff_story(tmp_path / "a", {})
    for name in ("Story settings.md", "story.yaml"):  # a folder not set up yet
        (fresh / name).unlink(missing_ok=True)
    both = handoff_story(tmp_path / "b", {"Handoff.md": OUTLINE, "Handoff.docx": OUTLINE})

    async def steps(user):
        await user.open(f"/setup?folder={fresh}")  # setting it up: the copy starts as Not sent
        await user.should_see(marker="copy-chip")
        await user.should_see("Copy of Handoff.md, so not sent")
        click(user, "Save and open")
        await user.should_see("Next step")

        STATE.open_story(both)  # an older story that sends both: a loud warning, and the fix
        await user.open("/story")
        await user.should_see(marker="duplicate-notice")
        await user.should_see("Two of your files are the same document")
        await user.open("/story/chapter/1")
        await user.should_see(marker="duplicate-notice")
        click(user, "Stop sending Handoff.docx")
        await user.should_see("Handoff.docx is no longer sent")

    simulate(steps)
    assert load_project(fresh).config.own_files["Handoff.docx"] == "not sent"
    assert load_project(fresh).config.own_files["Handoff.md"] == OUTLINE
    project = load_project(both)
    assert project.config.own_files["Handoff.docx"] == "not sent" and project.duplicates == []


def test_the_planner_edits_the_plan_takes_answers_and_finalizes(app_env, tmp_path):
    """The agentic brainstorm on screen: suggestion cards with Yes / No, the sections it changed, and
    approving in your own words, which finalizes the plan and offers Approve."""
    from devils_workshop.planner import run as planner_run
    from devils_workshop.planner.output import create_planner_story
    from devils_workshop.planner.state import PlannerState
    from devils_workshop.ui import planner as planner_screen
    from tests.unit.test_planner import SEEING
    from tests.unit.test_planner_agent import FIRST
    planner_run.RUNS.clear()
    planner_screen.CHATS.clear()
    planner_screen.PENDING.clear()
    STATE.catalog = SEEING
    root = create_planner_story(tmp_path / "Lamp", "Lamp")
    STATE.open_story(root)
    second = ("<edit section=\"Main characters\">\n- **Maren** (34), mends the lamp wicks.\n</edit>\n"
              "<reply>Added the wick-mending to Maren.</reply>")
    final = ("<edit section=\"Open questions\">\nNone: the person at sea is the father's brother (my choice).\n"
             "</edit>\n<ready/>\n<reply>Finalized.</reply>")
    app_env.queue(reply(FIRST), reply(second), reply(final))

    async def steps(user):
        await user.open("/story/planner")
        user.find(marker="planner-message").type("A keeper's daughter; the lamp signals at 2 a.m.")
        user.find(marker="planner-send").click()
        await user.should_see("Changed in the plan: Title, Main characters, Open questions", retries=50)
        await user.should_see("Give Maren a craft habit")
        await user.should_see(marker="planner-decisions")
        user.find(marker="suggestion-yes-1").click()
        await user.should_see("Send (1 answer)")
        user.find(marker="planner-send").click()  # nothing typed: the answer is the message
        await user.should_see("Added the wick-mending to Maren.", retries=50)
        await user.should_not_see(marker="planner-ready")
        user.find(marker="planner-message").type("Looks good, I approve.")
        user.find(marker="planner-send").click()
        await user.should_see(marker="planner-ready", retries=50)
        await user.should_see("The planner says the plan is complete")

    simulate(steps)
    state = PlannerState.load(root)
    sent = [b["messages"][-1]["content"] for b in app_env.chat_bodies()]
    assert "Yes to these suggestions" in sent[1] and "Give Maren a craft habit" in sent[1]
    assert "<author_approved/>" in sent[2]
    assert state.ready and "mends the lamp wicks" in state.plan and "father's brother" in state.plan
    assert state.suggestions[0].status == "accepted" and state.suggestions[0].told


# --- copying text ------------------------------------------------------------------------------------

def copies(user, marker: str) -> list[str]:
    """What each Copy button with this marker copies: its click handler, which runs in the page."""
    return [listener.js_handler for element in user.find(marker=marker).elements
            for listener in element._event_listeners.values() if listener.js_handler]


def test_copy_buttons_on_the_story_screens(app_env, tmp_path):
    """Chapter text, the reader, an error notice, and Activity's reply, request and error details."""
    import json
    from devils_workshop.ui.main import window_options
    from nicegui import app
    assert window_options(app)["text_select"] is True  # the app window lets you select (and so copy) text
    root = open_sample(tmp_path)
    app_env.queue(reply("I'm sorry, but I can't help with that."),
                  reply("As an AI, I must decline.", model="moonshotai/kimi-k3"))

    async def steps(user):
        await user.open("/story/chapter/1")
        await user.should_see(marker="copy-chapter")
        assert copies(user, "copy-chapter")[0].startswith("() => dwCopy(dwTextOf(")  # the text as shown
        await user.open("/story/read")
        await user.should_see(marker="copy-reader")
        assert copies(user, "copy-reader")[0].startswith("() => dwCopy(dwTextOf(")
        await user.open("/story/chapter/3")
        click(user, "Write chapter 3")
        await user.should_see("What's included")
        await user.should_see(marker="copy-request")  # the request, before it is sent
        assert "===== system =====" in copies(user, "copy-request")[0]
        user.find(marker="send").click()
        await user.should_see("Both models failed", retries=50)
        notices = [json.loads(h[len("() => dwCopy("):-1]) for h in copies(user, "copy-notice")]
        assert any("Both models failed" in n for n in notices)  # the whole message
        await user.open("/story/activity")
        user.find(marker="request-row").click()
        await user.should_see(marker="copy-failure")
        assert "refusal" in copies(user, "copy-failure")[0]
        assert "I'm sorry" in copies(user, "copy-reply")[0] or "decline" in copies(user, "copy-reply")[0]
        assert "===== system" in copies(user, "copy-sent")[0]

    simulate(steps)


def test_copy_buttons_in_the_planner(app_env, tmp_path):
    """Each message, the plan as it is in the editor, and the problem that stopped a planning run."""
    from devils_workshop.planner import run as planner_run
    from devils_workshop.planner.output import create_planner_story
    from devils_workshop.planner.state import PLANNER, USER, PlannerState, Turn
    from devils_workshop.ui import planner as planner_screen
    from tests.fakes import http_error
    from tests.unit.test_planner import SEEING
    planner_run.RUNS.clear()
    planner_screen.CHATS.clear()
    STATE.catalog = SEEING
    root = create_planner_story(tmp_path / "Lamp", "Lamp")
    STATE.open_story(root)
    state = PlannerState.load(root)
    state.turns = [Turn(USER, "IDEA-MARKER a lighthouse"), Turn(PLANNER, "REPLY-MARKER I wrote the plan.")]
    state.plan = "## Title\nLamp"
    state.save()

    async def steps(user):
        await user.open("/story/planner")
        await user.should_see("REPLY-MARKER")
        messages = copies(user, "copy-message")
        assert sorted(m.split("\"")[1].split()[0] for m in messages) == ["IDEA-MARKER", "REPLY-MARKER"]
        editor = next(iter(user.find(marker="planner-plan").elements))
        assert copies(user, "copy-plan") == [f"() => dwCopy(dwTextOf({editor.id}))"]  # your edits included

    simulate(steps)


def test_the_problem_that_stopped_planning_can_be_copied(app_env, tmp_path):
    from devils_workshop.planner import run as planner_run
    from devils_workshop.planner.output import create_planner_story
    from devils_workshop.planner.state import PlannerState
    from tests.fakes import http_error
    from tests.unit.test_planner import SEEING
    planner_run.RUNS.clear()
    STATE.catalog = SEEING
    root = create_planner_story(tmp_path / "Lamp", "Lamp")
    STATE.open_story(root)
    state = PlannerState.load(root)
    state.plan = "## Title\nLamp"
    planner_run.approve(state)
    app_env.queue(http_error(400, "REASON-MARKER bad request"))
    failed = planner_run.PlanRun(root, STATE.app_settings(), SEEING, app_env.client, sleep=lambda s: asyncio.sleep(0))
    asyncio.run(failed.run())
    planner_run.RUNS[str(root.resolve())] = failed

    async def stopped(user):
        await user.open("/story/planner")
        await user.should_see("REASON-MARKER")
        await user.should_see(marker="copy-planner-reason")
        reason = next(iter(user.find(content="REASON-MARKER").elements))
        assert copies(user, "copy-planner-reason") == [f"() => dwCopy(dwTextOf({reason.id}))"]

    simulate(stopped)
    planner_run.RUNS.clear()
