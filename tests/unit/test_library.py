"""The library: your stories in progress and completed, with their details, sorted and searched; versions."""

import asyncio
import time

from devils_workshop.library import library, new_version, opened, plan_details, set_completed, shelf, story_meta
from devils_workshop.planner.state import PlannerState
from devils_workshop.project.newproject import create_project
from devils_workshop.project.project import load_project
from devils_workshop.project.story_settings import read_settings, update_settings

from tests.conftest import write


def make(tmp_path, name, **details):
    root = create_project(tmp_path / name, title=name)
    if details:
        update_settings(root, **details)
    return str(root)


def test_a_storys_details_for_the_library(tmp_path):
    sample = create_project(tmp_path / "Sample", sample=True)
    meta = story_meta(str(sample))
    assert (meta.title, meta.genre) == ("The Saltmarsh Clock", "Cosy mystery")
    assert meta.characters[:2] == ["Mara Venn", "Tobias Reed"] and meta.chapters_planned == 4
    assert meta.chapters_written == 1 and meta.words > 100 and meta.status == "in progress"
    blank = story_meta(make(tmp_path, "Blank"))
    assert blank.genre == "" and blank.chapters_written == 0


def test_the_details_are_read_again_only_when_the_story_changes(tmp_path):
    path = make(tmp_path, "Story", genre="Gothic")
    assert story_meta(path).genre == "Gothic"
    time.sleep(0.01)
    update_settings(tmp_path / "Story", genre="Folk horror")
    assert story_meta(path).genre == "Folk horror"


def test_in_progress_and_completed_sorted_and_searched(tmp_path):
    paths = [make(tmp_path, "Bravo", genre="Gothic"), make(tmp_path, "Alpha", genre="Space opera",
                                                           characters=["Ilsa Varga"]),
             make(tmp_path, "Charlie")]
    for path in (paths[2], paths[0], paths[1]):  # opened in this order: Alpha last
        opened(path)
        time.sleep(0.01)
    set_completed(paths[0], True)
    items = library(paths)
    assert [m.title for m in shelf(items, False)] == ["Alpha", "Charlie"]  # newest first
    assert [m.title for m in shelf(items, False, "title")] == ["Alpha", "Charlie"]
    assert [m.title for m in shelf(items, True)] == ["Bravo"] and shelf(items, True)[0].completed_on
    assert [m.title for m in shelf(items, False, search="ilsa")] == ["Alpha"]  # a character's name
    assert [m.title for m in shelf(items, False, search="space opera")] == ["Alpha"]
    set_completed(paths[0], False)
    assert [m.title for m in shelf(library(paths), True)] == []


def test_a_new_version_keeps_the_foundation_and_leaves_the_chapters(tmp_path):
    sample = create_project(tmp_path / "Sample", sample=True)
    set_completed(str(sample), True)
    second = new_version(str(sample))
    assert second.name == "Sample (version 2)" and second.parent == sample.parent
    project = load_project(second)
    assert project.chapters == {} and len(project.outline.chapters) == 4 and project.entries
    data = read_settings(second)
    assert data["version"] == 2 and data["status"] == "in progress" and "completed_on" not in data
    items = library([str(sample), str(second)])
    assert [(m.title, m.version) for m in shelf(items, False)] == [("The Saltmarsh Clock", 2)]
    assert [(m.title, m.version) for m in shelf(items, True)] == [("The Saltmarsh Clock", 1)]  # still completed
    third = new_version(str(second))
    assert third.name == "Sample (version 3)" and read_settings(third)["version"] == 3
    everything = library([str(sample), str(second), str(third)])
    assert len({m.family for m in everything}) == 1  # one story
    assert [m.version for m in shelf(everything, False)] == [3]  # its latest version in progress


def test_a_planned_storys_new_version_writes_its_later_briefs_again(story_from_planner):
    root = story_from_planner
    second = new_version(str(root))
    state = PlannerState.load(second)
    assert state.guide and all(s.brief for s in state.chapters[0].scenes)
    assert all(not s.brief for s in state.chapters[1].scenes) and state.jit and state.briefs_through == 1
    assert state.reviews == {} and not (second / "chapters").exists()


def test_genre_and_characters_from_a_plan():
    plan = ("## Title\nLamp\n## Genre, tone and audience\nLiterary mystery for adults (my choice)\n\n"
            "## Main characters\n- **Maren** (15), the keeper's daughter\n- **Tobias**, the harbourmaster\n")
    assert plan_details(plan) == ("Literary mystery for adults", ["Maren", "Tobias"])


import pytest  # noqa: E402


@pytest.fixture
def story_from_planner(tmp_path):
    from devils_workshop.planner.run import PlanRun
    from devils_workshop.settings import AppSettings
    from tests.fakes import FakeOpenRouter
    from tests.unit.test_planner import SEEING, approved, planner_answers
    from devils_workshop.planner.output import create_planner_story
    root = create_planner_story(tmp_path / "Salt", "Salt and Iron")
    approved(root, jit=False)
    fake = FakeOpenRouter().respond(planner_answers)
    asyncio.run(PlanRun(root, AppSettings(), SEEING, fake.client, sleep=lambda s: asyncio.sleep(0)).run())
    write(root, "chapters/ch01.md", "# Chapter 1\n\nText.\n")
    return root


def test_a_storys_cost_is_every_version_together_and_a_new_version_starts_at_nothing(story_from_planner):
    import json
    from devils_workshop.library import family_of, story_cost

    def spend(folder, *costs):
        (folder / "logs").mkdir(exist_ok=True)
        with (folder / "logs" / "costs.jsonl").open("a", encoding="utf-8") as log:
            for cost in costs:
                log.write(json.dumps({"job": "write", "cost": cost}) + "\n")

    root = story_from_planner
    state = PlannerState.load(root)
    state.spent = 0.4
    state.save()
    planned = story_meta(str(root)).cost  # its planning, as logged
    spend(root, 0.25, 0.5)
    assert round(story_meta(str(root)).cost - planned, 2) == 0.75
    second = new_version(str(root))
    assert PlannerState.load(second).spent == 0.0  # its own planning: the first version's isn't copied
    assert story_meta(str(second)).cost == 0.0  # its logs start empty
    time.sleep(0.01)
    spend(second, 0.1)
    items = library([str(root), str(second)])
    latest = shelf(items, False)[0]
    assert latest.version == 2 and latest.cost == 0.1  # refreshed after it spent
    assert round(story_cost(items, latest) - planned, 2) == 0.85  # the card's number: the whole story
    assert [m.version for m in family_of(str(second))] == [1, 2]
