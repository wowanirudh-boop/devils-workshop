"""Command line for Devil's Workshop.

The desktop app (from milestone M5) uses the same engine functions; the
command line is mainly for testing, scripting and debugging.

    python -m devils_workshop init "My Story"            # blank project
    python -m devils_workshop init "Sample" --sample     # the sample story
    python -m devils_workshop plan-check "My Story"
    python -m devils_workshop preview 2 --scene 1 -p "My Story"
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from devils_workshop import APP_NAME, __version__
from devils_workshop.context.assemble import AssemblyError, Overrides, assemble, format_preview
from devils_workshop.context.size import DEFAULT_SIZE_LIMIT
from devils_workshop.project.issues import ERROR
from devils_workshop.project.newproject import ProjectExistsError, create_project
from devils_workshop.project.project import NotAProjectError, load_project
from devils_workshop.tools.plancheck import format_report, run_plan_check

EXIT_OK = 0
EXIT_PROBLEMS = 1  # the command ran but found errors, or couldn't do its job
EXIT_USAGE = 2     # wrong arguments (argparse also uses 2)


def main(argv: list[str] | None = None) -> int:
    _safe_console()
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_USAGE
    return args.handler(args)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="devils-workshop", description=f"{APP_NAME} command line")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="command")

    init = commands.add_parser("init", help="create a new story project folder")
    init.add_argument("folder", type=Path, help="folder to create (must be new or empty)")
    init.add_argument("--sample", action="store_true", help="copy the sample story instead of a blank project")
    init.add_argument("--title", help="story title (blank project only)")
    init.set_defaults(handler=_cmd_init)

    check = commands.add_parser("plan-check", help="check a project for problems (no API calls)")
    check.add_argument("project", type=Path, nargs="?", default=Path("."), help="project folder (default: here)")
    check.set_defaults(handler=_cmd_plan_check)

    preview = commands.add_parser("preview", help="show exactly what would be sent for a chapter (no API call)")
    preview.add_argument("chapter", type=int, help="chapter number")
    preview.add_argument("-p", "--project", type=Path, default=Path("."), help="project folder (default: here)")
    preview.add_argument("--scene", type=int, help="scene number (scene mode)")
    preview.add_argument("--mode", choices=["whole", "scenes"], help="override the chapter's mode")
    preview.add_argument("--notes", default="", help="notes for this request")
    preview.add_argument("--leave-out", action="append", default=[], metavar="NAME",
                         help="leave out a section, e.g. --leave-out \"Style guide\" (repeatable)")
    preview.add_argument("--add", action="append", default=[], metavar="NAME",
                         help="put back a left-out file, e.g. --add \"Tobias Reed\" (repeatable)")
    preview.add_argument("--add-file", action="append", default=[], metavar="PATH",
                         help="add any project file, e.g. --add-file notes/ideas.md (repeatable)")
    preview.add_argument("--limit", type=int, default=DEFAULT_SIZE_LIMIT, help="size limit in tokens")
    preview.add_argument("--summary", action="store_true", help="show the overview only, not the full text")
    preview.set_defaults(handler=_cmd_preview)

    models = commands.add_parser("models", help="list OpenRouter models with prices and supported settings")
    models.add_argument("--search", default="", help="only models whose name contains this, e.g. kimi")
    models.add_argument("--refresh", action="store_true", help="fetch a fresh list even if the saved one is recent")
    models.set_defaults(handler=_cmd_models)

    cost = commands.add_parser("cost", help="show what a project has cost so far")
    cost.add_argument("-p", "--project", type=Path, default=Path("."), help="project folder (default: here)")
    cost.add_argument("--chapter", type=int, help="only this chapter")
    cost.set_defaults(handler=_cmd_cost)

    settings = commands.add_parser("settings", help="show where the app settings file is and what it says")
    settings.set_defaults(handler=_cmd_settings)

    live = commands.add_parser("live-test", help="one real, low-cost OpenRouter check (needs your API key)")
    live.set_defaults(handler=_cmd_live_test)

    app = commands.add_parser("app", help="start the desktop app")
    app.add_argument("--browser", action="store_true", help="open in the web browser instead of an app window")
    app.add_argument("--port", type=int, help="port for the local server")
    app.add_argument("--background", action="store_true", help="run without a window (as when started with Windows)")
    app.add_argument("--after-close", action="store_true", help=argparse.SUPPRESS)  # see ui/main.py
    app.set_defaults(handler=_cmd_app)
    return parser


def _cmd_init(args: argparse.Namespace) -> int:
    if args.sample and args.title:
        print("--title can't be used with --sample", file=sys.stderr)
        return EXIT_USAGE
    try:
        folder = create_project(args.folder, sample=args.sample, title=args.title)
    except ProjectExistsError as exc:
        print(exc, file=sys.stderr)
        return EXIT_PROBLEMS
    kind = "sample story" if args.sample else "new story"
    print(f"Created {kind} in {folder}")
    print(f"Next: edit the files, then run: devils-workshop plan-check \"{folder}\"")
    return EXIT_OK


def _cmd_plan_check(args: argparse.Namespace) -> int:
    try:
        project = load_project(args.project)
    except NotAProjectError as exc:
        print(exc, file=sys.stderr)
        return EXIT_PROBLEMS
    report = run_plan_check(project)
    print(format_report(report))
    return EXIT_OK if report.ok else EXIT_PROBLEMS


def _cmd_preview(args: argparse.Namespace) -> int:
    try:
        project = load_project(args.project)
        first = assemble(project, args.chapter, args.scene, args.mode, args.notes, size_limit=args.limit)
        # Sections are chosen by their label (as shown in the preview) or their key.
        keys = {}
        for section in first.sections:
            keys[section.label.lower()] = section.key
            keys[section.key.lower()] = section.key
        overrides = Overrides(
            exclude={keys.get(name.lower(), name) for name in args.leave_out},
            include={keys.get(name.lower(), name) for name in args.add},
            extra_files=list(args.add_file),
        )
        request = assemble(project, args.chapter, args.scene, args.mode, args.notes, overrides,
                           size_limit=args.limit)
    except (NotAProjectError, AssemblyError) as exc:
        print(exc, file=sys.stderr)
        return EXIT_PROBLEMS
    errors = project.issues.of(ERROR)
    if errors:
        print(f"Note: plan-check finds {len(errors)} error(s) in this project; this preview may be incomplete.\n")
    print(format_preview(request, show_text=not args.summary))
    return EXIT_OK


def _cmd_models(args: argparse.Namespace) -> int:
    from devils_workshop.llm.catalog import load_catalog
    from devils_workshop.llm.openrouter import OpenRouterClient
    from devils_workshop.settings import get_api_key, settings_dir

    catalog, notes = asyncio.run(load_catalog(OpenRouterClient(get_api_key()), settings_dir(), args.refresh))
    for note in notes:
        print(note, file=sys.stderr)
    if not catalog.models:
        return EXIT_PROBLEMS
    found = catalog.search(args.search) if args.search else sorted(catalog.models.values(), key=lambda m: m.id)
    print(f"{len(found)} model(s)" + (" (saved list)" if catalog.from_cache else ""))
    for info in found:
        price_in, price_out = info.price_per_million()
        prices = "?" if price_in is None else f"${price_in:.2f}/${price_out:.2f} per M"
        context = f"{info.context_length:,}" if info.context_length else "?"
        print(f"  {info.id:<45} ctx {context:>11}  {prices}")
        if args.search:
            print(f"      settings: {', '.join(info.supported_parameters) or '(none listed)'}")
    return EXIT_OK


def _cmd_cost(args: argparse.Namespace) -> int:
    from devils_workshop.llm.costs import format_summary, read_costs, summarize

    try:
        project = load_project(args.project)
    except NotAProjectError as exc:
        print(exc, file=sys.stderr)
        return EXIT_PROBLEMS
    summary = summarize(read_costs(project.root / "logs"), args.chapter)
    title = f"Costs: {project.config.title}" + (f", chapter {args.chapter}" if args.chapter else "")
    print(format_summary(summary, title))
    return EXIT_OK


def _cmd_settings(args: argparse.Namespace) -> int:
    from devils_workshop.settings import SettingsError, get_api_key, load_settings, settings_path

    try:
        settings = load_settings()
    except SettingsError as exc:
        print(exc, file=sys.stderr)
        return EXIT_PROBLEMS
    print(f"Settings file: {settings_path()}")
    print(f"Writer's main model:      {settings.primary}")
    print(f"Writer's fallback model:  {settings.fallback or '(none)'}")
    print(f"Planner's main model:     {settings.planner_primary}")
    print(f"Planner's fallback model: {settings.planner_fallback or '(none)'}")
    print(f"API key: {'found' if get_api_key() else 'not set'} (never shown or stored in the settings file)")
    return EXIT_OK


def _cmd_live_test(args: argparse.Namespace) -> int:
    from devils_workshop.llm.livecheck import live_check
    from devils_workshop.llm.openrouter import OpenRouterClient
    from devils_workshop.settings import SettingsError, get_api_key, load_settings, settings_dir

    try:
        settings = load_settings()
    except SettingsError as exc:
        print(exc, file=sys.stderr)
        return EXIT_PROBLEMS
    key = get_api_key()
    report = asyncio.run(live_check(OpenRouterClient(key), settings, settings_dir() / "logs", key))
    print(report.text())
    print("Please paste this whole output back to Claude.")
    return EXIT_OK if report.ok else EXIT_PROBLEMS


def _cmd_app(args: argparse.Namespace) -> int:
    from devils_workshop.ui.main import main as app_main

    return app_main((["--browser"] if args.browser else []) + (["--background"] if args.background else [])
                    + (["--after-close"] if args.after_close else [])
                    + (["--port", str(args.port)] if args.port else []))


def _safe_console() -> None:
    """Don't crash on characters an older Windows console can't show."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
