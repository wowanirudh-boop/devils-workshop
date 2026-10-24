"""Bundle Clearpage, the author's web page extractor, for the app (devils_workshop/vendor/clearpage/).

    python tools/build_clearpage.py <a clone of https://github.com/wowanirudh-boop/webscraper>

The clone's dependencies must be installed first (npm ci --ignore-scripts, in the clone). The bundle is one
file with everything it needs, run by the app with Node: the copy shipped inside the exe, or Node on the PATH
when the app runs from source. Run this again (and commit the result) after changing the extractor.
"""

import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE.parent / "devils_workshop" / "vendor" / "clearpage"


def main(source: Path) -> None:
    if not (source / "src" / "extract.mjs").is_file():
        sys.exit(f"{source} isn't a clone of the webscraper repository (no src/extract.mjs)")
    esbuild = source / "node_modules" / ".bin" / ("esbuild.cmd" if sys.platform == "win32" else "esbuild")
    if not esbuild.exists():
        sys.exit(f"Install its dependencies first: cd {source} && npm ci --ignore-scripts")
    entry = source / "dw-cli.mjs"
    shutil.copyfile(HERE / "clearpage" / "cli.mjs", entry)
    TARGET.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run([str(esbuild), entry.name, "--bundle", "--platform=node", "--format=cjs", "--target=node20",
                        "--legal-comments=eof", f"--outfile={TARGET / 'clearpage.cjs'}"], cwd=source, check=True)
    finally:
        entry.unlink(missing_ok=True)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=source, capture_output=True, text=True).stdout.strip()
    (TARGET / "SOURCE.txt").write_text(
        "Built by tools/build_clearpage.py from https://github.com/wowanirudh-boop/webscraper\n"
        f"commit {commit or '(unknown)'}\n", encoding="utf-8")
    print(f"bundled into {TARGET / 'clearpage.cjs'} (commit {commit[:7] or '?'})")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(Path(sys.argv[1]).resolve())
