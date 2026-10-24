"""Structural integrity of planning, writing, revision and export, on invented, neutral fixtures.

Each failure the pipeline must catch has a test (a chapter bound to another's text, too many scenes, an event told
early, knowledge before disclosure, a character back without returning, a fact changed, an event told twice, an
invented earlier meeting, a missing ending; the export of an old version is in test_revisions.py), and so does
each thing it must leave alone (flashbacks and foreshadowing you approve, memories and callbacks, a repetition
whose meaning changes, approved changes, a scene longer than planned).
"""

import asyncio
import json
import re

import pytest

from devils_workshop.chat.auto import FINISHED, SCENES, AutoOptions, AutoRun
from devils_workshop.context.assemble import assemble
from devils_workshop.context.guard import GuardError, check_scene_request
from devils_workshop.planner import canon, contracts, prompts
from devils_workshop.planner.audit import (
    ADVISORY,
    HARD,
    INCONCLUSIVE,
    SceneAudit,
    chapter_findings,
    evaluate,
    findings_of,
    prose_findings,
    read_audit,
)
from devils_workshop.planner.manuscript import check_manuscript, report_text
from devils_workshop.planner.output import create_planner_story, write_outputs
from devils_workshop.planner.registry import (
    ADAPTATION,
    ENDING,
    Event,
    Fact,
    Registry,
    SourceEvent,
    events_from,
    read_approvals,
    registry_text,
)
from devils_workshop.planner.run import FAILED_RUN, FINISHED as PLANNED, PlanRun, approve
from devils_workshop.planner.state import PlannerState
from devils_workshop.project.project import load_project
from devils_workshop.settings import AppSettings

from tests.fakes import FakeOpenRouter, reply
from tests.unit.test_planner import SEEING, audit_reply, planner_answers

PROSE = "The lamp turned over the black water and the gulls cried over the quay. " * 40


@pytest.fixture
def story(tmp_path):
    return create_planner_story(tmp_path / "Lamp", "The Lamp")


def nine_chapters(order=range(1, 10)) -> str:
    """A nine-chapter architecture whose first and last chapters couldn't be more different."""
    def block(n: int) -> str:
        body = {1: ("FIRST-CHAPTER-BODY a quiet arrival at the lighthouse", "The keeper unlocks the door"),
                9: ("FINAL-CHAPTER-BODY the storm and the rescue at sea", "The lifeboat brings the crew home")}
        purpose, beat = body.get(n, (f"middle chapter {n} moves it on", f"Something turns in chapter {n}"))
        return (f'<chapter number="{n}" title="Title {n}" scenes="2" words="1600">\nPurpose: {purpose}\n'
                f"Summary: chapter {n} happens.\nBeats:\n- {beat}\nEnding: chapter {n} ends\n</chapter>\n")
    return "".join(block(n) for n in order)


# --- A. chapters keep their own identity -------------------------------------------------------------------------

def test_a_chapter_keeps_its_own_body_whatever_order_the_reply_lists_them_in():
    shuffled = prompts.parse_list(nine_chapters([9, 1, 2, 3, 4, 5, 6, 7, 8]), "chapters")
    assert contracts.check_chapters(shuffled, 9).ok
    chapters = prompts.chapters_from(shuffled)
    assert [c.number for c in chapters] == list(range(1, 10))
    assert chapters[0].title == "Title 1" and "FIRST-CHAPTER-BODY" in chapters[0].purpose
    assert chapters[-1].title == "Title 9" and "FINAL-CHAPTER-BODY" in chapters[-1].purpose
    assert all("FINAL-CHAPTER-BODY" not in c.purpose for c in chapters[:-1])
    assert len({c.id for c in chapters}) == 9  # every chapter its own id


@pytest.mark.parametrize("reply_text, problem", [
    (nine_chapters([1, 2, 3, 4, 5, 6, 7, 8, 1]), "more than one chapter is numbered 1"),
    (nine_chapters([1, 2, 3, 4, 5, 6, 7, 8, 10]), "not 1 to 9"),
    (nine_chapters(range(1, 9)), "asks for 9 chapters, and the reply has 8"),
    (nine_chapters().replace('<chapter number="5" ', "<chapter "), "has no number"),
])
def test_chapters_that_cant_be_bound_to_their_numbers_are_never_used(reply_text, problem):
    found = contracts.check_chapters(prompts.parse_list(reply_text, "chapters"), 9)
    assert not found.ok and any(problem in p for p in found.problems)


def test_a_stray_chapter_heading_in_a_brief_never_moves_text_to_another_chapter(story):
    """The planner's outline is read strictly: a heading out of order stays text of the scene it is in."""
    state = PlannerState.load(story)
    state.chapters = prompts.chapters_from(prompts.parse_list(nine_chapters(), "chapters"))
    for chapter in state.chapters:
        chapter.scenes = prompts.scenes_from([{"title": "A", "purpose": f"c{chapter.number} a", "words": 800},
                                              {"title": "B", "purpose": f"c{chapter.number} b", "words": 800}], 1600)
        for scene in chapter.scenes:
            scene.brief, scene.status = f"BRIEF of chapter {chapter.number}, scene {scene.number}.", "ready"
    state.guide = "## Voice\nPlain."
    state.save()
    write_outputs(state)
    outline = story / "Chapter outline.md"
    text = outline.read_text(encoding="utf-8")
    # An unquoted line that reads as a heading, as a hand edit or an older version could leave it
    text = text.replace("BRIEF of chapter 1, scene 2.", "BRIEF of chapter 1, scene 2.\nChapter 9: The Storm\n"
                                                        "STRAY-TEXT that belongs to chapter 1")
    outline.write_text(text, encoding="utf-8")
    project = load_project(story)
    first, last = project.outline.chapter(1), project.outline.chapter(9)
    assert "STRAY-TEXT" in first.scene(2).text and "STRAY-TEXT" not in last.scene(2).text
    assert last.scene(1).text.startswith("BRIEF of chapter 9, scene 1")
    assert any("out of order" in i.message for i in project.issues)


def test_a_brief_that_isnt_the_one_planned_for_its_scene_is_never_sent(story):
    state = PlannerState.load(story)
    state.chapters = prompts.chapters_from(prompts.parse_list(nine_chapters(), "chapters"))
    state.chapters[0].scenes = prompts.scenes_from([{"title": "A", "purpose": "a"}], 800)
    state.chapters[0].scenes[0].brief = "The keeper unlocks the door and climbs to the lamp room."
    state.save()
    with pytest.raises(GuardError, match="isn't the one the planner wrote"):
        check_scene_request(story, 1, 1, "A", "The lifeboat brings the crew home through the storm.", [], "")
    check_scene_request(story, 1, 1, "A", "The keeper unlocks the door and climbs to the lamp room.", [], "")


# --- B. the scene list is checked before it becomes the plan ----------------------------------------------------

def four_scene_story(story, replies):
    """A planned story whose chapter 1 needs four scenes; `replies` answer its scene-list requests in turn."""
    state = PlannerState.load(story)
    state.plan = "## Title\nThe Lamp\n## Length\n2 chapters"
    state.guide = "## Voice\nPlain."
    state.jit = False
    approve(state)
    state = PlannerState.load(story)
    state.chapters = prompts.chapters_from(prompts.parse_list(nine_chapters(range(1, 3)).replace(
        'scenes="2"', 'scenes="4"'), "chapters"))
    state.registry.events = events_from(state.chapters)
    state.save()
    queue = list(replies)

    def answer(body):
        system = " ".join(body["messages"][0]["content"].split())
        if "Split the chapter" in system:
            return reply(queue.pop(0) if queue else _scenes(4))
        return planner_answers(body)
    return answer


WHAT = ["The keeper climbs the stair at dusk", "A ship's light appears beyond the reef", "The radio fails in the gale",
        "Dawn shows wreckage on the rocks", "A child is found asleep in the boathouse", "The inspector arrives by ferry"]


def _scenes(n: int) -> str:
    return "".join(f'<scene title="S{k}" words="400">{WHAT[k - 1]}</scene>\n' for k in range(1, n + 1))


def test_six_scenes_for_a_chapter_of_four_are_sent_back_once_then_the_step_fails(story):
    fake = FakeOpenRouter().respond(four_scene_story(story, [_scenes(6), _scenes(6)]))
    run = PlanRun(story, AppSettings(), SEEING, fake.client, only=("chapter", 1), sleep=lambda s: asyncio.sleep(0))
    asyncio.run(run.run())
    assert run.status == FAILED_RUN and "needs exactly 4 scenes, and the reply has 6" in run.reason
    retry = [b for b in fake.chat_bodies() if "can't be used as it is" in b["messages"][-1]["content"]]
    assert len(retry) == 1  # bounded: asked once more, with what was wrong
    assert PlannerState.load(story).chapter(1).scenes == []  # nothing from it became the plan


def test_a_corrected_scene_list_is_used(story):
    fake = FakeOpenRouter().respond(four_scene_story(story, [_scenes(6), _scenes(4)]))
    run = PlanRun(story, AppSettings(), SEEING, fake.client, only=("chapter", 1), sleep=lambda s: asyncio.sleep(0))
    asyncio.run(run.run())
    assert run.status == PLANNED, run.reason
    assert [s.title for s in PlannerState.load(story).chapter(1).scenes] == ["S1", "S2", "S3", "S4"]


def test_a_scene_that_does_a_later_chapters_work_is_out_of_scope():
    later = [(5, "The lifeboat brings the whole crew home through the storm")]
    items = [{"title": "Arrival", "purpose": "The keeper unlocks the door"},
             {"title": "Storm", "purpose": "The lifeboat brings the whole crew home through the storm"}]
    found = contracts.check_scenes(items, 2, 0, later, ["The keeper unlocks the door"])
    assert not found.ok and "does what chapter 5 is planned to do" in found.problems[0]
    same = [{"title": "A", "purpose": "The keeper unlocks the door at dusk"},
            {"title": "B", "purpose": "The keeper unlocks the door at dusk"}]
    assert "describe the same thing" in contracts.check_scenes(same, 2).problems[0]


# --- C, F, H. what was written, against the registry -------------------------------------------------------------

def registry() -> Registry:
    found = Registry()
    found.events = [Event("E-01-1", 1, "The keeper unlocks the door", scene=1),
                    Event("E-01-end", 1, "The lamp is lit", ENDING, scene=2),
                    Event("E-05-1", 5, "The crew is rescued at sea", scene=1),
                    Event("E-09-end", 9, "The keeper leaves the island", ENDING, scene=2)]
    found.facts = [Fact("F1", "The keeper has one daughter", "kinship", "the keeper"),
                   Fact("F2", "The keeper knows the logbook was forged", "knowledge", "the keeper", from_chapter=4,
                        disclosed_by="E-04-1")]
    return found


def report(scene: int = 1, **fields) -> SceneAudit:
    audit = SceneAudit(scene, read=True, exit="the keeper is in the lamp room; the door is locked")
    for key, value in fields.items():
        setattr(audit, key, value)
    return audit


def kinds(findings, severity=HARD):
    return [f.kind for f in findings if f.severity == severity]


def test_an_event_of_chapter_5_told_in_chapter_1_is_caught():
    assert kinds(evaluate(registry(), 1, report(3, events=[("E-05-1", "happens")]))) == ["early"]


def test_a_flashback_or_foreshadowing_you_approved_is_left_alone():
    approved = registry()
    approved.event("E-05-1").early_from = 1
    assert kinds(evaluate(approved, 1, report(3, events=[("E-05-1", "happens")]))) == []


def test_remembering_or_reinterpreting_an_event_is_not_telling_it_again():
    done = registry()
    done.event("E-01-1").status, done.event("E-01-1").written_in = "written", [1, 1]
    for how in ("recalled", "callback"):  # a callback: a repetition whose meaning changes
        assert kinds(evaluate(done, 2, report(events=[("E-01-1", how)]))) == []


def test_a_unique_event_told_twice_is_caught():
    done = registry()
    done.event("E-01-1").status, done.event("E-01-1").written_in = "written", [1, 1]
    assert kinds(evaluate(done, 1, report(3, events=[("E-01-1", "happens")]))) == ["repeat"]
    assert kinds(evaluate(done, 1, report(3, events=[("E-01-1", "repeated")]))) == ["repeat"]
    twice_in_one_check = evaluate(registry(), 1, report(3, events=[("E-01-1", "happens")]), {"E-01-1"})
    assert kinds(twice_in_one_check) == ["repeat"]


def test_an_event_of_the_scene_just_before_gone_over_at_the_join_is_worth_a_look_not_a_rewrite():
    done = registry()
    done.event("E-01-1").status, done.event("E-01-1").written_in = "written", [1, 1]
    joined = evaluate(done, 1, report(3, events=[("E-01-1", "happens")]), previous=(1, 1))  # scene 1 just before
    assert kinds(joined) == [] and kinds(joined, ADVISORY) == ["overlap"]
    across = evaluate(done, 2, report(3, events=[("E-01-1", "repeated")]), previous=(1, 1))  # a chapter's last scene
    assert kinds(across) == [] and kinds(across, ADVISORY) == ["overlap"]
    assert kinds(evaluate(done, 1, report(3, events=[("E-01-1", "happens")]), previous=(1, 2))) == ["repeat"]


def test_an_event_carried_on_from_the_scene_before_is_not_told_twice():
    begun = registry()
    begun.event("E-01-1").status, begun.event("E-01-1").written_in = "written", [1, 1]  # begun in scene 1
    assert kinds(evaluate(begun, 1, report(3, events=[("E-01-1", "continues")]))) == []  # its rest, in scene 3
    assert kinds(evaluate(begun, 2, report(3, events=[("E-01-1", "continues")]))) == ["repeat"]  # not a chapter on
    assert kinds(evaluate(registry(), 1, report(1, events=[("E-01-1", "continues")]))) == []  # nothing began it:
    #                                                                       it happens here (so it isn't missing)
    glimpsed = registry()
    glimpsed.event("E-01-end").written_in = [1, 1]  # scene 1 ended on a glimpse of what scene 2 is planned to tell
    for how in ("happens", "repeated"):
        assert kinds(evaluate(glimpsed, 1, report(2, events=[("E-01-end", how)]))) == []  # its own scene: no repeat
    assert kinds(evaluate(glimpsed, 1, report(3, events=[("E-01-end", "happens")]))) == ["repeat"]  # any other one


def test_a_secret_known_before_it_is_disclosed_is_caught_and_its_own_disclosure_is_not():
    assert kinds(evaluate(registry(), 2, report(early_knowledge=[("F2", "he quotes the forged page")]))) == \
        ["knowledge"]
    disclosing = registry()
    disclosing.events.append(Event("E-04-1", 4, "The keeper finds the forged page"))
    allowed = evaluate(disclosing, 4, report(events=[("E-04-1", "happens")],
                                             early_knowledge=[("F2", "he reads the forged page")]))
    assert kinds(allowed) == []


def test_a_character_back_without_returning_is_a_broken_entry_worth_a_look():
    found = evaluate(registry(), 1, report(3, entry_ok=False,
                                           entry_note="the keeper rowed away in scene 1 and is in the lamp room"))
    assert kinds(found) == [] and kinds(found, ADVISORY) == ["entry"]  # never rewritten for: the next brief follows


def test_a_fact_changed_between_the_brief_and_the_text_is_caught():
    found = evaluate(registry(), 1, report(3, contradictions=[("F1", "the keeper speaks of his two daughters")]))
    assert kinds(found) == ["contradiction"]
    not_yet = evaluate(registry(), 1, report(3, contradictions=[("F2", "he doesn't know about the logbook")]))
    assert kinds(not_yet) == []  # F2 isn't true until chapter 4


def test_an_invented_earlier_meeting_is_worth_a_look_and_one_you_approved_is_not():
    found = evaluate(registry(), 1, report(3, new_facts=[("meeting", "the keeper met the inspector years ago")]))
    assert kinds(found) == [] and kinds(found, ADVISORY) == ["invented"]  # never a rewrite on its own
    approved = registry()
    approved.facts.append(Fact("F3", "the keeper met the inspector years ago", "meeting", authority="author"))
    assert kinds(evaluate(approved, 1, report(3, new_facts=[("meeting", "the keeper met the inspector years ago")])),
                 ADVISORY) == []
    detail = evaluate(registry(), 1, report(3, new_facts=[("detail", "the keeper wears a blue scarf")]))
    assert kinds(detail) == [] and kinds(detail, ADVISORY) == ["invented"]


def test_what_the_check_couldnt_judge_is_inconclusive_never_passed():
    assert kinds(evaluate(registry(), 1, SceneAudit(1)), INCONCLUSIVE) == ["unsure"]  # unreadable reply
    unsure = evaluate(registry(), 1, report(unsure=["whether the lamp was already lit"]))
    assert kinds(unsure, INCONCLUSIVE) == ["unsure"]
    assert kinds(chapter_findings(registry(), 1, 9, True, all_read=False), INCONCLUSIVE) == ["unsure"]


def test_an_event_left_out_of_its_scene_and_a_missing_ending_are_caught():
    assert kinds(evaluate(registry(), 1, report(1))) == ["missing"]  # E-01-1 is planned for scene 1
    assert kinds(evaluate(registry(), 1, report(1, events=[("E-01-1", "happens")]))) == []
    done_earlier = registry()
    done_earlier.event("E-01-1").written_in = [1, 2]  # it happened in another scene: not missing here
    assert kinds(evaluate(done_earlier, 1, report(1))) == []
    unplaced = registry()
    for event in unplaced.events:
        event.scene = 0  # no scene was planned to carry them: checked once the chapter is
    assert kinds(chapter_findings(unplaced, 1, 9, True)) == ["missing", "missing"]
    assert kinds(chapter_findings(unplaced, 9, 9, True)) == ["ending"]
    assert kinds(chapter_findings(registry(), 1, 9, True)) == []  # each scene's own check found them


def test_a_scene_longer_than_planned_is_only_worth_a_look():
    found = prose_findings(1, 1, PROSE * 3, 800)  # legitimate expansion: never a hard problem
    assert kinds(found) == [] and "length" in kinds(found, ADVISORY)
    assert kinds(prose_findings(1, 1, "<think>planning</think> " + PROSE, 800)) == ["markup"]


def test_the_checks_reply_is_read_scene_by_scene():
    found = read_audit('<scene number="2"><event id="E-01-1" how="callback"/><entry ok="no">she was at sea</entry>'
                       '<exit>on the quay</exit></scene><scene number="3"><exit>at home</exit></scene>', [2, 3, 4])
    assert found[2].events == [("E-01-1", "callback")] and found[2].entry_ok is False and found[3].exit == "at home"
    assert not found[4].read


# --- the registry: your approvals ------------------------------------------------------------------------------

def test_approvals_in_story_registry_md_are_read_back():
    found = registry()
    found.sources = [SourceEvent("S1", 1, "The narrator meets the mentor")]
    text = registry_text(found) + "- E-05-1: early from chapter 2\n- E-01-1: moved to chapter 2\n- S1: left out\n" \
                                  "- F1: The keeper has two daughters\n- Name: Ana -> Mara\n"
    read_approvals(found, text)
    assert found.event("E-05-1").early_from == 2 and found.event("E-01-1").chapter == 2
    assert found.source("S1").left_out and found.changes == ["Name: Ana -> Mara"]
    assert found.fact("F1").statement == "The keeper has two daughters" and found.fact("F1").authority == "author"
    again = registry()
    again.sources = [SourceEvent("S1", 1, "The narrator meets the mentor")]
    read_approvals(again, registry_text(found))  # written out and read back: the same approvals
    assert again.event("E-05-1").early_from == 2 and again.source("S1").left_out


# --- D. the writer's guide as it stands at each chapter -----------------------------------------------------------

def test_what_only_becomes_true_later_never_reaches_an_earlier_scene(story):
    from tests.unit.test_reader_loop import answers, planned
    fake = planned(story)
    state = PlannerState.load(story)
    state.guide = ("## Characters\n- Mara: a lamp keeper, careful and proud of the old lens.\n"
                   "- FUTURE-BLOCK Mara has become the harbour master after the storm of chapter 2.\n## Voice\nPlain.")
    _, state.registry.guide_scope = canon.read_canon('<block n="3" from="2" because="E-02-1"/>', state.guide)
    state.registry.canon_mark = canon.canon_mark(state)
    state.save()
    write_outputs(state)
    project = load_project(story)
    first = assemble(project, 1, 1, mode="scenes").full_text()
    assert "careful and proud" in first and "FUTURE-BLOCK" not in first
    state = PlannerState.load(story)
    state.chapters[1].scenes[0].brief, state.chapters[1].scenes[0].status = "**Coming in:** the morning after.", \
        "ready"
    state.briefs_through = 2
    state.save()
    write_outputs(state)
    second = assemble(load_project(story), 2, 1, mode="scenes").full_text()
    assert "FUTURE-BLOCK" in second  # true from chapter 2 on
    assert fake is not None and answers  # (the planned story's fakes)


def test_only_the_later_sentence_of_a_characters_entry_is_held_back():
    guide = ("## Characters\n- **Mara** - a lamp keeper, careful and proud of the old lens. She becomes harbour "
             "master after the storm. She speaks in short sentences.\n## Motifs\nThe lamp, lit every dusk.")
    _, scope = canon.read_canon('<block n="2" from="3">She becomes harbour master after the storm.</block>', guide)
    first = canon.scoped_guide(guide, scope, 1)
    assert "careful and proud" in first and "short sentences" in first  # the writer still knows who she is
    assert "harbour master" not in first and "harbour master" in canon.scoped_guide(guide, scope, 3)
    assert canon.later_blocks(guide, scope, 1) == ["She becomes harbour master after the storm."]
    _, whole = canon.read_canon('<block n="2" from="3"/>', guide)  # no sentence quoted: the whole block, as before
    assert "careful and proud" not in canon.scoped_guide(guide, whole, 1)
    _, both = canon.read_canon('<block n="2" from="3"/><block n="2" from="3">She becomes harbour master after the '
                               "storm.</block>", guide)  # a part quoted as well: only that part
    assert "careful and proud" in canon.scoped_guide(guide, both, 1)
    entry = "## People\n- **Ewan** - pier-master at the harbour; wires the mainland when the boat is lost."
    _, clause = canon.read_canon('<block n="2" from="4">wires the mainland when the boat is lost</block>', entry)
    assert "pier-master" in canon.scoped_guide(entry, clause, 1) and "wires" not in canon.scoped_guide(entry, clause, 1)
    from devils_workshop.project.instructions import builtin_text
    assert "only the words that say it" in builtin_text("plan_canon")


def test_the_last_check_refuses_a_request_carrying_a_later_part_of_the_guide(story):
    state = PlannerState.load(story)
    state.guide = "## Characters\n- FUTURE-BLOCK Mara has become the harbour master after the storm."
    _, state.registry.guide_scope = canon.read_canon('<block n="2" from="3"/>', state.guide)
    state.save()
    with pytest.raises(GuardError, match="only true from a later chapter"):
        check_scene_request(story, 1, 1, "", "", [], "... - FUTURE-BLOCK Mara has become the harbour master after "
                                                     "the storm. ...")


# --- E. scenes in order: each checked as written, the next brief following what happened ------------------------

def written_story(story, extra_audits=None, writer=None):
    """A planned two-chapter story, written with each scene checked. extra_audits: (chapter, scene) -> the lines
    the check reports for that scene the first time it is checked."""
    from tests.unit.test_reader_loop import answers, planned
    fake = planned(story)
    pending = dict(extra_audits or {})
    seen = []

    def respond(body):
        system = " ".join(body["messages"][0]["content"].split())
        task = body["messages"][-1]["content"]
        if "You check scenes of a novel" in system:
            chapter = int(re.search(r"of chapter (\d+) as written", task).group(1))
            numbers = [int(n) for n in re.findall(r'<scene number="(\d+)"', task)]
            extra = {n: pending.pop((chapter, n)) for n in numbers if (chapter, n) in pending}
            seen.append((chapter, numbers, task))
            return reply(audit_reply(task, extra))
        if writer is not None and "<change_request>" not in task and "You are" not in system:
            text = writer(body)
            if text:
                return reply(text)
        return answers(body)

    fake.respond(respond)
    auto = AutoRun(story, AutoOptions([1], mode=SCENES, continue_short=False), settings=AppSettings(),
                   catalog=SEEING, client=fake.client, sleep=lambda s: asyncio.sleep(0))
    asyncio.run(auto.run())
    return auto, fake, seen


def test_a_scene_that_breaks_continuity_is_put_right_once_then_checked_again(story):
    auto, fake, seen = written_story(story, {(1, 2): ['<contradiction fact="F1">Mara is on the quay without '
                                                      "coming back</contradiction>"]})
    assert auto.status == FINISHED, auto.reason
    labels = [s.label for s in auto.steps]
    assert "Chapter 1, scene 2: put right what the check found" in labels
    assert "Chapter 1: checking scene 2 again" in labels
    change = next(b for b in fake.chat_bodies() if "<change_request>" in b["messages"][-1]["content"])
    assert "without coming back" in change["messages"][-1]["content"]  # the problem, named for the writer
    assert load_project(story).chapters[1].scenes[2].startswith("REVISED-PROSE")
    state = PlannerState.load(story)
    assert not [f for f in findings_of(state, 1) if f.severity == HARD]


def test_a_problem_still_there_after_one_more_try_is_kept_and_shown_and_the_chapter_is_finished(story):
    """A chapter is never left half-written: the scene is kept, the problem listed, and the next scene written."""
    always = ['<event id="E-01-end" how="repeated"/>']
    from tests.unit.test_reader_loop import answers, planned
    fake = planned(story)

    def respond(body):
        system = " ".join(body["messages"][0]["content"].split())
        task = body["messages"][-1]["content"]
        if "You check scenes of a novel" in system:
            numbers = [int(n) for n in re.findall(r'<scene number="(\d+)"', task)]
            return reply(audit_reply(task, {n: always for n in numbers if n == 1}))
        return answers(body)

    fake.respond(respond)
    auto = AutoRun(story, AutoOptions([1], mode=SCENES, continue_short=False), settings=AppSettings(),
                   catalog=SEEING, client=fake.client, sleep=lambda s: asyncio.sleep(0))
    asyncio.run(auto.run())
    assert auto.status == FINISHED, auto.reason
    assert "Chapter 1 has problems the checks couldn't put right" in auto.reason
    assert sorted(load_project(story).chapters[1].scenes) == [1, 2]  # the chapter went on to its end
    assert len([b for b in fake.chat_bodies() if "<change_request>" in b["messages"][-1]["content"]]) == 1
    assert any(f.kind == "repeat" for f in check_manuscript(story).of(HARD))  # and it is shown


def test_writing_a_chapter_first_finishes_the_chapters_before_it(story):
    from tests.unit.test_reader_loop import answers, planned
    fake = planned(story)
    first = AutoRun(story, AutoOptions([1], mode=SCENES, continue_short=False), settings=AppSettings(),
                    catalog=SEEING, client=fake.client, sleep=lambda s: asyncio.sleep(0))
    asyncio.run(first.run())
    path = story / "chapters" / "ch01.md"  # scene 2 missing, as a run that stopped part-way left it
    text = path.read_text(encoding="utf-8")
    path.write_text(text[:text.index("<!-- scene 2 -->")], encoding="utf-8")
    from devils_workshop.chat.auto import chapter_finished, unfinished_before
    assert not chapter_finished(load_project(story), 1) and unfinished_before(load_project(story), 2) == [1]
    second = AutoRun(story, AutoOptions([2], mode=SCENES, continue_short=False), settings=AppSettings(),
                     catalog=SEEING, client=fake.client, sleep=lambda s: asyncio.sleep(0))
    asyncio.run(second.run())
    assert second.status == FINISHED, second.reason
    assert "Chapter 1 wasn't finished, so it was finished first" in second.reason
    project = load_project(story)
    assert sorted(project.chapters[1].scenes) == [1, 2] and sorted(project.chapters[2].scenes) == [1, 2]
    labels = [s.label for s in second.steps]
    assert labels.index("Chapter 1, scene 2 of 2") < labels.index("Chapter 2, scene 1 of 2")


def test_without_a_canon_a_new_fact_is_only_worth_a_look():
    empty = registry()
    empty.facts = []
    found = evaluate(empty, 1, report(3, new_facts=[("meeting", "the keeper met the inspector years ago")]))
    assert kinds(found) == [] and kinds(found, ADVISORY) == ["invented"]
    from devils_workshop.project.instructions import builtin_text
    assert "What the brief gives is planned, never an addition" in builtin_text("plan_audit")


def test_the_next_brief_follows_how_the_scene_before_actually_ended(story):
    auto, fake, seen = written_story(story, {(1, 1): ["<departure>Mara kept the ledger instead of dropping it"
                                                      "</departure>"]})
    assert auto.status == FINISHED, auto.reason
    assert "Chapter 1, scene 2: its brief follows on from what was written" in [s.label for s in auto.steps]
    again = next(b for b in fake.chat_bodies() if "<brief_to_change>" in b["messages"][-1]["content"])
    content = again["messages"][-1]["content"]
    assert "Mara kept the ledger" in content and "EXIT-MARKER after chapter 1, scene 1" in content
    order = [s.label for s in auto.steps]  # scene 2 was written while scene 1 was checked, then written again
    assert order.index("Chapter 1: checking scene 1 as written") < order.index(
        "Chapter 1, scene 2: its brief follows on from what was written") < len(order) - 1 - order[::-1].index(
        "Chapter 1, scene 2 of 2")
    assert [s.note for s in auto.steps if s.label == "Chapter 1, scene 2 of 2"][0].startswith("written again")
    assert load_project(story).chapters[1].scenes[2]  # the second writing is the one kept


def test_an_event_written_early_is_planned_again_as_a_memory_in_its_own_chapter(story):
    from tests.unit.test_reader_loop import answers, planned
    fake = planned(story)
    state = PlannerState.load(story)
    state.registry.events.append(Event("E-02-1", 2, "Tobias confesses on the causeway", scene=1,
                                       early_from=1))  # you allowed it from chapter 1 (Story registry.md)
    state.save()
    write_outputs(state)
    reported = {"done": False}

    def respond(body):
        system = " ".join(body["messages"][0]["content"].split())
        task = body["messages"][-1]["content"]
        if "You check scenes of a novel" in system and "of chapter 1 as written" in task and not reported["done"]:
            reported["done"] = True  # and it happened there: chapter 2's event, in chapter 1, scene 1
            return reply(audit_reply(task, {1: ['<event id="E-02-1" how="happens"/>']}))
        return answers(body)

    fake.respond(respond)
    for chapter in (1, 2):
        auto = AutoRun(story, AutoOptions([chapter], mode=SCENES, continue_short=False), settings=AppSettings(),
                       catalog=SEEING, client=fake.client, sleep=lambda s: asyncio.sleep(0))
        asyncio.run(auto.run())
        assert auto.status == FINISHED, auto.reason
    assert "Chapter 2: its scenes planned again from what has happened" in [s.label for s in auto.steps]
    replan = next(b for b in fake.chat_bodies() if "Plan the scenes for this chapter" in b["messages"][-1]["content"]
                  and "already happened earlier" in b["messages"][-1]["content"])
    assert "E-02-1: Tobias confesses on the causeway (happened in chapter 1, scene 1)" in \
        replan["messages"][-1]["content"]


# --- G. an adaptation keeps its reference ------------------------------------------------------------------------

def adaptation() -> Registry:
    found = Registry(kind=ADAPTATION, reference="Reference.txt")
    found.sources = canon.read_source_events(
        '<source_event id="S1" required="yes" roles="the narrator meets the mentor">They meet</source_event>'
        '<source_event id="S2" required="yes" roles="the mentor" causes="S1">The mentor is betrayed</source_event>'
        '<source_event id="S3" required="no">A walk on the beach</source_event>')
    found.events = [Event("E-01-1", 1, "Ana meets Ilse"), Event("E-02-1", 2, "Ilse is betrayed")]
    return found


def test_every_required_reference_event_carried_in_order_with_its_causes():
    found = adaptation()
    canon.read_map('<map source="S1" event="E-01-1"/><map source="S2" event="E-02-1"/><map source="S3" '
                   'event="none"/>', found)
    assert canon.mapping_findings(found) == []  # S3 is optional
    assert found.event("E-02-1").prerequisites == ["E-01-1"]  # the reference's causal order
    early = evaluate(found, 2, report(events=[("E-02-1", "happens")]))
    assert kinds(early, ADVISORY) == ["order"]  # its cause hasn't happened yet: worth a look


def test_chapters_that_leave_out_a_required_reference_event_are_planned_once_more_to_carry_it(story):
    from tests.unit.test_planner import approved, planner_answers
    approved(story)
    state = PlannerState.load(story)
    state.registry.kind, state.registry.reference = ADAPTATION, "Reference.txt"
    state.registry.sources = canon.read_source_events(
        '<source_event id="S1" required="yes">SOURCE-MARKER the keeper reads the letters</source_event>')
    state.save()
    maps, architectures = [], []

    def answers(body):
        system, task = " ".join(body["messages"][0]["content"].split()), body["messages"][-1]["content"]
        if "how faithfully a novel's chapter architecture adapts its reference" in system:
            maps.append(task)
            return reply('<map source="S1" event="none"/>' if len(maps) == 1 else '<map source="S1" event="E-01-1"/>')
        if "design the novel's chapter architecture" in system:
            architectures.append(task)
        return planner_answers(body)

    fake = FakeOpenRouter().respond(answers)
    run = PlanRun(story, AppSettings(), SEEING, fake.client, sleep=lambda s: asyncio.sleep(0))
    asyncio.run(run.run())
    assert run.status == PLANNED, run.reason  # not stopped: the chapters were planned once more
    assert len(architectures) == 2 and "SOURCE-MARKER the keeper reads the letters" in architectures[1]
    assert "Plan them again as they were, changed only to carry these" in architectures[1]
    assert len(maps) == 2 and not canon.mapping_findings(PlannerState.load(story).registry)


def test_a_required_reference_event_left_out_is_caught_and_one_reordered_is_worth_a_look_unless_approved():
    found = adaptation()
    canon.read_map('<map source="S1" event="E-02-1"/><map source="S2" event="E-01-1"/>', found)
    assert [(f.kind, f.severity) for f in canon.mapping_findings(found)] == [("order", ADVISORY)]  # never a stop
    assert found.event("E-01-1").prerequisites == []  # its cause is carried later, so it isn't one yet
    found.event("E-01-1").early_from = 1  # a deliberate reordering you approved
    assert canon.mapping_findings(found) == []
    canon.read_map('<map source="S1" event="E-01-1"/>', found)
    assert [f.kind for f in canon.mapping_findings(found)] == ["missing"]
    found.source("S2").left_out = "yes"
    assert canon.mapping_findings(found) == []
    decided = adaptation()  # a source event the author's decisions replace is no departure
    canon.read_map('<map source="S1" event="E-01-1"/><map source="S2" event="none" replaced="yes">the betrayal is '
                   "replaced by a quarrel</map>", decided)
    assert canon.mapping_findings(decided) == [] and decided.source("S2").left_out


# --- K. the whole manuscript -------------------------------------------------------------------------------------

def test_the_manuscript_check_finds_a_missing_ending_and_never_passes_what_wasnt_checked(story):
    auto, fake, seen = written_story(story)
    assert auto.status == FINISHED, auto.reason
    found = check_manuscript(story)
    assert "chapter 2" in report_text(found).lower()
    assert any(f.kind == "missing" and f.chapter == 2 for f in found.of(HARD))  # chapter 2 isn't written yet
    state = PlannerState.load(story)
    state.registry.event("E-01-end").status, state.registry.event("E-01-end").written_in = "planned", []
    state.registry.event("E-01-end").scene = 0  # no scene carries it: the chapter's check finds it missing
    entry = state.audits["1"]
    entry["chapter_findings"] = [vars(f) for f in chapter_findings(state.registry, 1, 1, True)]
    state.save()
    assert any(f.kind in ("missing", "ending") and f.chapter == 1 for f in check_manuscript(story).of(HARD))

    path = story / "chapters" / "ch01.md"
    path.write_text(path.read_text(encoding="utf-8").replace("gulls", "terns"), encoding="utf-8")
    changed = check_manuscript(story)
    assert any(f.chapter == 1 and f.severity == INCONCLUSIVE for f in changed.findings)  # not passed
    assert changed.verdict != "no structural problems found"


# --- concurrency: two screens saving the same story ----------------------------------------------------------------

def test_two_copies_of_the_state_saved_one_after_the_other_keep_both_changes(story):
    state = PlannerState.load(story)
    state.chapters = prompts.chapters_from(prompts.parse_list(nine_chapters(range(1, 3)), "chapters"))
    state.save()
    run_copy, reader_copy = PlannerState.load(story), PlannerState.load(story)
    reader_copy.reviews["1"] = [{"role": "user", "text": "colder"}]  # the Reader, meanwhile
    reader_copy.chapters[1].note = "READER-NOTE"
    reader_copy.save()
    run_copy.chapters[0].note = "RUN-NOTE"  # the planning run, holding its copy all along
    run_copy.save()
    saved = json.loads((story / "planner" / "state.json").read_text(encoding="utf-8"))
    assert saved["reviews"]["1"][0]["text"] == "colder"
    assert [c["note"] for c in saved["chapters"]] == ["RUN-NOTE", "READER-NOTE"]
    assert run_copy.reviews["1"] and run_copy.chapters[1].note == "READER-NOTE"  # and the run's copy has them


# --- J. attention goes where the story's weight is, not where a word count puts it ---------------------------------

def test_the_built_in_prompts_ask_for_proportion_not_quotas():
    from devils_workshop.project.instructions import builtin_text
    brief = builtin_text("plan_scene")
    assert "(full)" in brief and "(brief)" in brief and "no fixed" in brief and "not a quota" in brief
    assert "usually 6 to 12 beats" not in brief and "the beats add up" not in brief  # no per-beat word quotas
    assert "<reference_events>" in brief  # in an adaptation the reference decides the substance
    assert "by what each carries" in builtin_text("plan_scenes")
    architecture = " ".join(builtin_text("plan_architecture").split())
    assert "not its scene count times a fixed size" in architecture
    # a continuous encounter, conversation or action is one scene: scene breaks come from the story, not a size
    assert "One continuous encounter, conversation or action is one scene however long it runs" in architecture
    assert "never inside a continuous encounter" in " ".join(builtin_text("plan_scenes").split())
    assert "where the words go" in builtin_text("plan_brainstorm")
    scene = " ".join(builtin_text("plan_scene").split())
    assert "a repetition builds, never restates" in scene and "what they now know or have decided" in scene
    assert "none left out" in builtin_text("write")
    write = " ".join(builtin_text("write").split())
    assert "Never fill it with filler or invented incident" in write and "prolong an action" not in write
    assert "an action that keeps changing as it repeats, are never padding" in write  # dwelling is never padding
    assert "prolong an action" not in " ".join(builtin_text("plan_scene").split())
    assert 'weight="major"' in builtin_text("plan_source_events")


def test_a_set_piece_is_staged_in_full_with_a_choreography_brief_and_the_chapters_carry_their_strands(story):
    from devils_workshop.planner.state import FULL
    from devils_workshop.project.instructions import builtin_text
    items = prompts.parse_list('<scene title="The Duel" kind="fight" words="3000" staging="full">The duel</scene>'
                               '<scene title="After" kind="aftermath" words="600" staging="normal">After</scene>',
                               "scenes")
    duel, after = prompts.scenes_from(items, 3600)
    state = PlannerState.load(story)
    assert (duel.staging, after.staging) == (FULL, "")
    assert state.brief_target(duel) == 2000 and state.brief_target(after) == 190  # full: about two thirds
    chapter = prompts.chapters_from(prompts.parse_list(
        '<chapter number="1" title="Steel" scenes="2" words="3600">\nPurpose: the duel\nStrands: STRAND-MARKER '
        'action 60%, character 40%; step: the first blood drawn\n</chapter>', "chapters"))[0]
    chapter.scenes = [duel, after]
    state.chapters = [chapter]
    assert "STRAND-MARKER" in prompts.scenes_messages(state, chapter)[1].content  # the scene list sizes by them
    task = prompts.scene_messages(state, chapter, duel)[1].content
    assert "Staging: full" in task and "about 2,000 words" in task
    brief = " ".join(builtin_text("plan_scene").split())
    assert "stage-by-stage choreography" in brief and "Shape it in waves" in brief
    plan = "## Title\nT\n## Chapters (proposed)\nCHAPTERS-MARKER\n## Escalation\nLADDER-MARKER\n## Characters\nC"
    assert "CHAPTERS-MARKER" not in prompts.plan_without_plot(plan)  # they tell the story: not sent past the plan
    assert "LADDER-MARKER" not in prompts.plan_without_plot(plan)


def test_the_planner_offers_lengths_and_proposes_the_chapters_before_the_plan_is_approved():
    from devils_workshop.project.instructions import builtin_text
    brainstorm = " ".join(builtin_text("plan_brainstorm").split())
    assert "never ask the author for a number" in brainstorm and "Offer two or three lengths" in brainstorm
    assert "new chapters that continue the story from its own consequences" in brainstorm
    assert "Chapters (proposed" in brainstorm and "Strands and curve" in brainstorm and "Escalation (" in brainstorm
    assert "Don't break the story into chapters" not in brainstorm
    architecture = " ".join(builtin_text("plan_architecture").split())
    assert "When the plan has Chapters (proposed), those are the chapters" in architecture
    assert "content limits" not in builtin_text("plan_guide")


def test_an_adaptations_brief_gets_only_its_own_scenes_reference_events_with_their_weight(story):
    state = PlannerState.load(story)
    state.chapters = prompts.chapters_from(prompts.parse_list(nine_chapters(range(1, 3)), "chapters"))
    chapter = state.chapters[0]
    chapter.scenes = prompts.scenes_from([{"title": "Door", "purpose": "The keeper unlocks the door"},
                                          {"title": "Lamp", "purpose": "The lamp is lit"}], 1600)
    state.registry = adaptation()
    state.registry.events = [Event("E-01-1", 1, "The keeper unlocks the door", scene=1),
                             Event("E-01-end", 1, "The lamp is lit", ENDING, scene=2)]
    state.registry.sources = canon.read_source_events(
        '<source_event id="S1" weight="passing">SOURCE-PASSING the narrator walks up the hill</source_event>'
        '<source_event id="S2" weight="major">SOURCE-MAJOR the lamp is lit for the first time</source_event>')
    canon.read_map('<map source="S1" event="E-01-1"/><map source="S2" event="E-01-end"/>', state.registry)
    first = prompts.scene_messages(state, chapter, chapter.scenes[0])[1].content
    assert "SOURCE-PASSING" in first and "(passing; carried by E-01-1)" in first
    assert "SOURCE-MAJOR" not in first  # the next scene's event isn't this brief's business
    scenes = prompts.scenes_messages(state, chapter)[1].content
    assert "SOURCE-PASSING" in scenes and "(major; carried by E-01-end)" in scenes
    assert "about 1,600 words in all: share them by what each scene carries" in scenes


def test_a_brief_is_written_from_the_guide_as_it_stands_at_its_chapter(story):
    state = PlannerState.load(story)
    state.chapters = prompts.chapters_from(prompts.parse_list(nine_chapters(range(1, 3)), "chapters"))
    state.chapters[0].scenes = prompts.scenes_from([{"title": "A", "purpose": "a"}], 800)
    state.guide = "## Characters\n- FUTURE-BLOCK Mara has become the harbour master after the storm."
    _, state.registry.guide_scope = canon.read_canon('<block n="2" from="2"/>', state.guide)
    sent = prompts.scene_messages(state, state.chapters[0], state.chapters[0].scenes[0])[1].content
    assert "FUTURE-BLOCK" not in sent and "## Characters" in sent


def test_the_writer_is_told_where_the_weight_is_and_that_word_counts_are_no_quota(story):
    from tests.unit.test_reader_loop import planned
    planned(story)
    task = assemble(load_project(story), 1, 1, mode="scenes").messages[-1].content
    assert "a (full) beat lived through at length, a (brief) one in a line or two" in task
    assert "rough guides, not quotas" in task and "Don't stretch a brief moment" in task
    assert "giving each about the words it says" not in task


def test_the_last_thing_the_writer_reads_is_where_the_scene_ends(story):
    from tests.unit.test_reader_loop import planned
    planned(story)
    state = PlannerState.load(story)
    state.chapters[0].scenes[0].brief = ("**What happens:**\n1. (full, about 300 words) Mara climbs the stair.\n"
                                         "**Ending:** On Mara at the lamp room door,\nher hand on the latch.\n"
                                         "**Keep in mind:** Tobias is still on the quay.")
    state.save()
    write_outputs(state)
    task = assemble(load_project(story), 1, 1, mode="scenes").messages[-1].content
    assert task.rstrip().endswith("End the scene exactly here, and write nothing after it: On Mara at the lamp room "
                                  "door, her hand on the latch.\n</task>")
    from devils_workshop.planner.audit import prose_findings
    copied = prose_findings(1, 1, "Mara climbed. (full, about 300 words) She stopped.", 10)
    assert "markup" in [f.kind for f in copied]  # a stage's share copied into the prose


def test_attention_in_the_wrong_place_is_worth_a_look_never_a_hard_problem():
    found = read_audit('<scene number="3"><padding>the walk to the boat takes half the scene; the confession is '
                       'one line</padding><exit>at sea</exit></scene>', [3])
    findings = evaluate(registry(), 1, found[3])
    assert kinds(findings) == [] and kinds(findings, ADVISORY) == ["padding"]


# --- consequences, natural behaviour and variety: what the story carries forward ----------------------------------

def test_the_check_records_how_characters_stand_what_stays_open_and_the_scene_in_a_line():
    from devils_workshop.planner.audit import commit
    from devils_workshop.planner.registry import forget_written
    found = registry()
    first = read_audit('<scene number="1"><character name="Mara Venn">on the quay, soaked, furious with Tobias'
                       '</character><consequence who="Mara, Tobias">they haven\'t spoken since the quarrel</consequence>'
                       '<summary kind="Argument">a shouted quarrel on the quay at night, Mara leading</summary>'
                       '<exit>on the quay</exit></scene>', [1])[1]
    commit(found, 1, first, "argument")
    assert found.character("Mara Venn").state.startswith("on the quay") and found.open_consequences()[0].id == "C1"
    assert found.summaries[0].kind == "argument"
    later = read_audit('<scene number="2"><absorbed id="C1"/><character name="Mara Venn">calmer, at home'
                       '</character><exit>at home</exit></scene>', [2])[2]
    commit(found, 1, later)
    assert not found.open_consequences() and found.character("Mara Venn").state == "calmer, at home"
    forget_written(found, 1, [2])  # scene 2 written again: what it absorbed is open again
    assert found.open_consequences()[0].id == "C1"
    assert [c.name for c in found.present("Mara walks home alone")] == ["Mara Venn"]  # by first name too


def test_behaviour_that_ignores_what_happened_is_worth_a_look():
    found = registry()
    from devils_workshop.planner.registry import Consequence
    found.consequences = [Consequence("C1", "Mara, Tobias", "they haven't spoken since the quarrel", 1, 1)]
    report_ = read_audit('<scene number="3"><ignored id="C1">Mara greets Tobias warmly</ignored>'
                         '<behaviour who="Tobias">apologises at once though he never backs down</behaviour>'
                         '<exit>x</exit></scene>', [3])[3]
    findings = evaluate(found, 1, report_)
    assert kinds(findings) == [] and kinds(findings, ADVISORY) == ["behaviour", "behaviour"]
    assert "they haven't spoken since the quarrel" in findings[0].text


def test_a_brief_carries_each_characters_state_what_is_still_open_and_the_earlier_scenes_of_its_kind(story):
    from devils_workshop.planner.audit import commit
    state = PlannerState.load(story)
    state.chapters = prompts.chapters_from(prompts.parse_list(nine_chapters(range(1, 4)), "chapters"))
    for chapter in state.chapters:
        chapter.scenes = prompts.scenes_from([{"title": "Quarrel", "purpose": "Mara and Tobias argue",
                                               "kind": "argument"}, {"title": "After", "purpose": "Ilse waits"}], 1600)
    state.registry = registry()
    commit(state.registry, 1, read_audit(
        '<scene number="1"><character name="Mara Venn">STATE-MARKER bruised wrist, won\'t look at Tobias'
        '</character><consequence who="Mara">OPEN-MARKER she owes Ilse the money</consequence>'
        '<summary kind="argument">SUMMARY-MARKER a quarrel on the quay at night</summary><exit>x</exit></scene>',
        [1])[1], "argument")
    commit(state.registry, 1, read_audit('<scene number="2"><character name="Ilse">waiting</character>'
                                         '<exit>y</exit></scene>', [2])[2])
    chapter = state.chapters[2]  # two chapters later: Mara hasn't been seen since
    sent = prompts.scene_messages(state, chapter, chapter.scenes[0])[1].content
    assert "STATE-MARKER" in sent and "(as of chapter 1, scene 1)" in sent  # her state, from her last scene
    assert "OPEN-MARKER" in sent and "SUMMARY-MARKER" in sent and '<earlier_scenes_like_this kind="argument">' in sent
    assert "Ilse (as of" not in sent  # not in this scene
    assert "<worn_phrases>" not in sent  # nothing written yet, so nothing worn out
    planned = prompts.scenes_messages(state, chapter)[1].content
    assert "<scenes_so_far>" in planned and "(argument) SUMMARY-MARKER" in planned
    # the first chapter's opening planned again after the story has gone past it: nothing from after it
    commit(state.registry, 3, read_audit(
        '<scene number="2"><character name="Mara Venn">LATER-MARKER at sea with the ledger</character>'
        '<consequence who="Mara">LATER-OPEN-MARKER the crew knows</consequence><absorbed id="C1"/>'
        '<exit>z</exit></scene>', [2])[2])
    again = prompts.scene_messages(state, state.chapters[0], state.chapters[0].scenes[0])[1].content
    assert "LATER-MARKER" not in again and "LATER-OPEN-MARKER" not in again and "STATE-MARKER" not in again
    sent = prompts.scene_messages(state, chapter, chapter.scenes[0])[1].content  # before it was absorbed
    assert "OPEN-MARKER she owes" in sent and "LATER-OPEN-MARKER" not in sent


def test_the_brief_prompt_reasons_out_behaviour_and_says_what_detail_means():
    from devils_workshop.project.instructions import builtin_text
    brief = builtin_text("plan_scene")
    assert "**How they'd behave:**" in brief and "**New this time:**" in brief and "How much detail" in brief
    assert "never in euphemism or summary" in brief and "movement-by-movement blocking" in brief
    assert 'kind="argument"' in builtin_text("plan_scenes")
    audit = builtin_text("plan_audit")
    assert "<character name=" in audit and "<consequence who=" in audit and '<summary kind="argument">' in audit


def test_what_one_chapter_leaves_open_reaches_the_next_chapters_briefs(story):
    auto, fake, seen = written_story(story, {(1, 2): [
        '<character name="Mara">STATE-MARKER soaked through, holding the ledger</character>',
        '<consequence who="Mara">OPEN-MARKER Tobias saw her take the ledger</consequence>']})
    assert auto.status == FINISHED, auto.reason
    before = len(fake.chat_bodies())
    second = AutoRun(story, AutoOptions([2], mode=SCENES, continue_short=False), settings=AppSettings(),
                     catalog=SEEING, client=fake.client, sleep=lambda s: asyncio.sleep(0))
    asyncio.run(second.run())
    assert second.status == FINISHED, second.reason
    brief = next(b for b in fake.chat_bodies()[before:]
                 if "Write the brief for scene 1 of chapter 2" in b["messages"][-1]["content"])
    content = brief["messages"][-1]["content"]
    assert "OPEN-MARKER" in content
    assert "OPEN-MARKER" in (story / "Story registry.md").read_text(encoding="utf-8")  # yours to read and edit


# --- the checks and the canon never hold the story up -------------------------------------------------------------

def _cut_off(body):
    """A reply that never comes: the stream ends part-way (as a gateway cutting a long think short would)."""
    from tests.fakes import sse
    return sse(done=False)


def test_a_canon_that_never_answers_lets_planning_go_on(story):
    from tests.unit.test_planner import approved
    approved(story)
    asked = []

    def respond(body):
        system = " ".join(body["messages"][0]["content"].split())
        if "You keep the canon of a novel" in system:
            asked.append(body)
            return _cut_off(body)
        return planner_answers(body)

    fake = FakeOpenRouter().respond(respond)
    run = PlanRun(story, AppSettings(), SEEING, fake.client, sleep=lambda s: asyncio.sleep(0))
    asyncio.run(run.run())
    assert run.status == PLANNED and "ready to write" in run.reason, run.reason
    assert len(asked) == 6  # cut off at once each time: retried as set (3 per model), then given up
    assert all(b["reasoning"] == {"effort": "medium"} for b in asked)  # thinks as the planner is set
    canon_step = next(s for s in run.steps if s.kind == "canon")
    assert "couldn't be worked out this time" in canon_step.note
    assert PlannerState.load(story).registry.canon_mark == ""  # tried again next time


def test_a_scene_check_that_never_answers_leaves_the_scene_not_judged_and_writing_goes_on(story):
    from tests.unit.test_reader_loop import answers, planned
    fake = planned(story)

    def respond(body):
        if "You check scenes of a novel" in " ".join(body["messages"][0]["content"].split()):
            return _cut_off(body)
        return answers(body)

    fake.respond(respond)
    auto = AutoRun(story, AutoOptions([1], mode=SCENES, continue_short=False), settings=AppSettings(),
                   catalog=SEEING, client=fake.client, sleep=lambda s: asyncio.sleep(0))
    asyncio.run(auto.run())
    assert auto.status == FINISHED, auto.reason
    assert sorted(load_project(story).chapters[1].scenes) == [1, 2]  # both scenes written all the same
    state = PlannerState.load(story)
    assert all(f.severity == INCONCLUSIVE for f in findings_of(state, 1))
    report_ = check_manuscript(story)
    assert any(f.chapter == 1 and f.severity == INCONCLUSIVE for f in report_.findings)  # never passed
    checks = [b for b in fake.chat_bodies() if "You check scenes of a novel" in b["messages"][0]["content"]]
    assert all(b["reasoning"] == {"effort": "medium"} for b in checks)


def test_a_reply_cut_off_at_the_same_point_twice_goes_to_the_fallback_instead_of_again():
    from devils_workshop.llm.generate import _same_cut
    from devils_workshop.llm.openrouter import StreamResult
    cut = StreamResult(error_kind="incomplete", elapsed_s=301.0)
    assert "time limit on the service's side" in _same_cut(cut, [("incomplete", 298.0)])
    assert _same_cut(cut, [("incomplete", 120.0)]) == ""  # cut at different points: it may get through
    assert _same_cut(StreamResult(error_kind="incomplete", elapsed_s=4.0), [("incomplete", 4.0)]) == ""
    silent = StreamResult(error_kind="timeout", elapsed_s=900.0)
    assert "silent" in _same_cut(silent, [("timeout", 900.0)]) and _same_cut(silent, []) == ""


def test_every_attempt_is_logged_with_its_reason_and_what_was_done_and_none_of_the_content(story):
    from devils_workshop.llm import diagnostics
    from tests.unit.test_planner import approved
    approved(story)

    def respond(body):
        if "You keep the canon of a novel" in " ".join(body["messages"][0]["content"].split()):
            return _cut_off(body)
        return planner_answers(body)

    asyncio.run(PlanRun(story, AppSettings(), SEEING, FakeOpenRouter().respond(respond).client,
                        sleep=lambda s: asyncio.sleep(0)).run())
    lines = [json.loads(line) for line in diagnostics.path().read_text(encoding="utf-8").splitlines()]
    canon = [e for e in lines if e["kind"] == "attempt" and e["job"] == "plan-canon"]
    assert [e["decision"] for e in canon][:3] == ["retry", "retry", "the fallback model (tried 3 times)"]
    assert canon[0]["error_kind"] == "incomplete" and canon[0]["effort"] == "medium" and canon[0]["max_tokens"]
    assert all(k in canon[0] for k in ("elapsed_s", "headers_s", "last_data_s", "keepalives", "reasoning_chars",
                                         "prompt_tokens_estimate", "http_status", "wait_s"))
    summary = [e for e in lines if e["kind"] == "request" and e["job"] == "plan-canon"]
    assert summary and summary[-1]["ok"] is False and summary[-1]["attempts"] == 6
    text = diagnostics.path().read_text(encoding="utf-8")
    assert "GUIDE-MARKER" not in text and "PLAN-MARKER" not in text and "BRIEF-MARKER" not in text  # no content
    assert "Lamp" not in text and str(story) not in text  # the story by a hash only


def test_the_planners_own_check_of_the_requirements_never_reaches_the_writer():
    reply = ("<brief>\n**What happens:**\n1. (full) Mara climbs the stair.\n</brief>\n<requirements>\nCHECK-MARKER "
             "her fear rising: beat 1\n</requirements>\n<continuity>\nMara at the top\n</continuity>")
    assert prompts.split_scene_reply(reply) == ("**What happens:**\n1. (full) Mara climbs the stair.", "Mara at the top")
    unclosed = reply.replace("</brief>\n", "")  # a brief left open still ends where the check begins
    assert "CHECK-MARKER" not in prompts.split_scene_reply(unclosed)[0]
    assert "CHECK-MARKER" not in prompts.split_scene_reply(reply.replace("<brief>\n", "").replace("</brief>\n", ""))[0]


def test_a_scene_longer_than_one_reply_is_written_in_parts_in_one_chat_and_kept_as_one_scene(story):
    from devils_workshop.context.assemble import scene_parts
    from tests.unit.test_reader_loop import planned, write
    stages = "\n".join(f"{n}. (full, about 500 words) Stage {n} of the climb." for n in range(1, 7))
    brief = (f"**What happens:**\n{stages}\n**Ending:** On Mara at the lamp room door, her hand on the latch.\n"
             "**Keep in mind:** the gale.")
    assert [(p.first, p.last, p.words) for p in scene_parts(brief, 3000)] == [(1, 3, 1500), (4, 6, 1500)]
    assert scene_parts(brief, 1800) == []  # one reply holds it
    fake = planned(story)
    state = PlannerState.load(story)
    state.chapters[0].scenes[0].brief, state.chapters[0].scenes[0].words = brief, 3000
    state.save()
    write_outputs(state)
    auto = write(story, fake, [1])
    assert auto.status == FINISHED, auto.reason
    sent = [b["messages"] for b in fake.chat_bodies() if "Stage 1 of the climb" in b["messages"][1]["content"]
            and "You check scenes" not in b["messages"][0]["content"]]
    assert "Write only the first part now: beats 1 to 3, about 1,500 words" in sent[0][-1]["content"]
    assert "End the scene exactly here" not in sent[0][-1]["content"]
    last = sent[1][-1]["content"]  # the same chat, the first part sent back
    assert sent[1][-2]["role"] == "assistant" and "beats 4 to 6 of the brief" in last
    assert "This is the last part: end the scene exactly here, and write nothing after it: On Mara at the lamp " \
           "room door, her hand on the latch." in last
    text = load_project(story).chapters[1].scenes[1]
    assert text.count("WRITTEN-PROSE") == 2 and "written in 2 parts" in " ".join(s.note for s in auto.steps)


def test_a_text_that_has_a_changing_state_otherwise_is_worth_a_look_never_a_rewrite():
    found = registry()
    found.facts += [Fact("F3", "The keeper's coat is off", "state", "the keeper", from_chapter=1),
                    Fact("F4", "The logbook is on the chart table", "object", "the logbook", from_chapter=1)]
    findings = evaluate(found, 1, report(3, contradictions=[("F3", "he still has it on"), ("F1", "two daughters"),
                                                            ("F4", "it is in his coat")]))
    assert [f.ref for f in findings if f.severity == HARD] == ["F1"]  # a real fact: put right
    assert kinds(findings, ADVISORY) == ["contradiction", "contradiction"]  # the coat, the logbook: briefs carry them
