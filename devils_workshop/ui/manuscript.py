"""The manuscript check on the Read screen: what must be put right, what couldn't be judged, what is worth a look
(planner/manuscript.py), with a way to have the chapters not checked yet checked against the plan."""

from __future__ import annotations

from nicegui import ui

from devils_workshop.planner.audit import ADVISORY, HARD, INCONCLUSIVE
from devils_workshop.planner.manuscript import check_manuscript, write_report
from devils_workshop.ui.components import card, primary, secondary, smooth_reload, toast
from devils_workshop.ui.state import STATE

SHOWN = 8  # findings of each kind shown here; the saved report has them all


def manuscript_card(project) -> None:
    from devils_workshop.planner.run import current_run
    from devils_workshop.planner.state import is_planner_story

    report = check_manuscript(project.root)
    planner = is_planner_story(project.root)
    with card("Manuscript check", "Structure, continuity and the plan, for the whole story. It doesn't judge how "
                                  "good the prose is.") as box:
        box.mark("manuscript-check")
        counts = (f"{len(report.of(HARD))} to put right · {len(report.of(INCONCLUSIVE))} not judged · "
                  f"{len(report.of(ADVISORY))} worth a look")
        ui.label(f"{report.verdict[0].upper()}{report.verdict[1:]}: {counts}").classes("dw-body") \
            .mark("manuscript-verdict")
        for severity, heading in ((HARD, "Must be put right"), (INCONCLUSIVE, "Couldn't be judged"),
                                  (ADVISORY, "Worth a look")):
            found = report.of(severity)
            if not found:
                continue
            ui.label(heading).classes("dw-small").style("font-weight: 600; margin-top: 6px")
            for finding in found[:SHOWN]:
                ui.label("• " + (finding.line() if finding.chapter else finding.text)).classes("dw-small")
            if len(found) > SHOWN:
                ui.label(f"… and {len(found) - SHOWN} more in the saved report").classes("dw-small")
        status = ui.label().classes("dw-small")
        with ui.row().classes("gap-2"):
            def save() -> None:
                toast(f"Saved {write_report(project.root, check_manuscript(project.root))}")

            secondary("Save the report", save, "description").mark("manuscript-save")
            unchecked = [c for c in report.chapters if c.words and c.checked != "yes"]
            if planner and unchecked:
                def check_now() -> None:
                    from devils_workshop.ui.planner import launch
                    if not STATE.has_a_key():
                        toast("Add a key in Settings first", "warning")
                        return
                    launch(project.root, only=("manuscript",))
                    button.disable()
                    status.text = "Checking…"

                button = primary(f"Check {len(unchecked)} chapter{'s' if len(unchecked) != 1 else ''} against the "
                                 "plan", check_now, "fact_check").mark("manuscript-run") \
                    .tooltip("One request to the planner's model per chapter")

                def follow() -> None:
                    run = current_run(project.root)
                    if run is None or run.only != ("manuscript",):
                        return
                    if run.running:
                        now = run.steps[-1].label if run.steps else "Starting…"
                        status.text = f"Checking… {now}"
                        return
                    timer.cancel()
                    write_report(project.root, check_manuscript(project.root))
                    smooth_reload()

                timer = ui.timer(1.0, follow)
