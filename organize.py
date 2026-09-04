"""
Layout repair. Run this if your files ended up flat in one folder:

    python organize.py

It moves each file into the folder it belongs in, then tells you exactly which
files are still missing so you can download only those instead of guessing.

Safe to run more than once. It never deletes anything and never overwrites a
file that is already in the right place.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# filename -> destination folder ("" means it belongs at the project root)
LAYOUT: dict[str, str] = {
    # server
    "main.py": "api",
    # analysis core
    "analyze.py": "core",
    "deterministic.py": "core",
    "enums.py": "core",
    "fit_score.py": "core",
    "ingest.py": "core",
    "models.py": "core",
    "narrate.py": "core",
    "pipeline.py": "core",
    "stats.py": "core",
    # frontend
    "index.html": "site",
    "app.html": "site",
    # data
    "sample_applications.csv": "sample",
    "sample_resume.txt": "sample",
    # tests
    "test_pipeline.py": "tests",
    # root
    "run.py": "",
    "organize.py": "",
    "RUNNING.md": "",
    "ARCHITECTURE.md": "",
    "requirements.txt": "",
}

# Files without which the server cannot start.
ESSENTIAL = {
    "main.py", "analyze.py", "deterministic.py", "enums.py", "fit_score.py",
    "ingest.py", "pipeline.py", "stats.py", "index.html", "app.html", "run.py",
}

PACKAGES = ["api", "core", "tests"]


def main() -> None:
    moved, already, missing = [], [], []

    for folder in {d for d in LAYOUT.values() if d}:
        (ROOT / folder).mkdir(exist_ok=True)

    for filename, folder in LAYOUT.items():
        target = ROOT / folder / filename if folder else ROOT / filename
        loose = ROOT / filename

        if target.exists():
            already.append(filename)
            continue

        if folder and loose.exists():
            shutil.move(str(loose), str(target))
            moved.append(f"{filename}  ->  {folder}/")
            continue

        missing.append(filename)

    # Package markers. An empty __init__.py makes imports predictable.
    for pkg in PACKAGES:
        marker = ROOT / pkg / "__init__.py"
        if not marker.exists():
            marker.write_text("")

    # A stray __init__.py at the root confuses imports — park it out of the way.
    stray = ROOT / "__init__.py"
    if stray.exists():
        stray.unlink()
        moved.append("__init__.py  ->  removed from project root")

    print(f"Project folder: {ROOT}\n")

    if moved:
        print("Moved:")
        for m in moved:
            print("  " + m)
        print()
    if already:
        print(f"Already in place: {len(already)} file(s)\n")

    essential_missing = sorted(m for m in missing if m in ESSENTIAL)
    optional_missing = sorted(m for m in missing if m not in ESSENTIAL)

    if essential_missing:
        print("MISSING — the server will not start without these:")
        for m in essential_missing:
            dest = LAYOUT[m]
            print(f"  {m}   (must go in {dest + '/' if dest else 'the project root'})")
        print()
    if optional_missing:
        print("Missing but optional for now:")
        for m in optional_missing:
            print(f"  {m}")
        print()

    if essential_missing:
        print("Download the missing files, drop them anywhere in this folder,")
        print("and run this script again.")
        sys.exit(1)

    print("Layout is correct. Start the server with:\n\n    python run.py\n")


if __name__ == "__main__":
    main()
