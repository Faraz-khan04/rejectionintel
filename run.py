"""
Launcher. Run this instead of the uvicorn command if the CLI gives you trouble:

    python run.py

Why this exists:

1. `uvicorn` on your PATH may belong to a DIFFERENT Python installation than the
   one `pip` installed your packages into. That is the single most common cause
   of "it installed fine but won't start" on Windows, where two or three Pythons
   often coexist. Running `python run.py` guarantees the server and the packages
   are the same interpreter.

2. It pins the working directory and sys.path to this file's location, so
   `api.main` resolves no matter which folder you launched from.

3. It fails with a readable message instead of a traceback if the project
   folders are missing.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

REQUIRED = {
    "api/main.py": "the FastAPI server",
    "core/pipeline.py": "the analysis pipeline",
    "core/stats.py": "the statistics layer",
    "site/index.html": "the landing page",
    "site/app.html": "the upload and report page",
}


def preflight() -> None:
    missing = [f"  {p}  ({why})" for p, why in REQUIRED.items() if not (ROOT / p).exists()]
    if missing:
        print(f"Project files are missing under {ROOT}\n")
        print("\n".join(missing))
        print(
            "\nExpected layout:\n"
            "  rejectionintel/\n"
            "    run.py\n"
            "    api/main.py\n"
            "    core/*.py\n"
            "    site/index.html\n"
            "    site/app.html\n"
            "    sample/sample_applications.csv\n"
            "\nMake sure you unzipped the whole folder, not just individual files."
        )
        sys.exit(1)

    try:
        import scipy  # noqa: F401
    except ImportError:
        print(
            f"scipy is not installed for this interpreter:\n  {sys.executable}\n\n"
            "Install it into THIS Python with:\n"
            f'  "{sys.executable}" -m pip install fastapi "uvicorn[standard]" '
            "python-multipart scipy\n"
        )
        sys.exit(1)


def load_env() -> None:
    """Read .env into the environment. Deliberately hand-rolled so the app has
    one fewer dependency, and so a malformed line is skipped rather than fatal."""
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def main() -> None:
    preflight()
    load_env()
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))

    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    print(f"Python:  {sys.executable}")
    print(f"Project: {ROOT}")
    sys.path.insert(0, str(ROOT))
    from core.llm_provider import get_provider
    st = get_provider()
    if st.provider:
        i = st.provider.info
        print(f"Narration: {i.name} / {i.model} "
              f"({i.call_budget} calls per report, {i.max_concurrency} concurrent)")
        if i.notes:
            print(f"           {i.notes}")
    else:
        print(f"Narration: templates — {st.reason}")
    print(f"Open:    http://127.0.0.1:{port}\n")

    uvicorn.run("api.main:app", host="127.0.0.1", port=port, reload=True)


if __name__ == "__main__":
    main()
