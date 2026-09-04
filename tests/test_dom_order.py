"""Static check: no listener may be bound before its element exists.

WHY THIS TEST EXISTS
--------------------
Twice now, a listener was attached to an element whose markup appears later in
the document than the script. The element is null, the statement throws, and
every statement below it in that script silently never runs. The visible
symptom was "Cannot access \'EASY\' before initialization" — a variable in a
completely unrelated part of the file.

That failure is invisible in review and produced a blank page. This catches it.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAILURES = []

for page in sorted((ROOT / "site").glob("*.html")):
    html = page.read_text(encoding="utf-8")
    for m in re.finditer(r"\$\(\'([^\']+)\'\)\s*\.addEventListener", html):
        el = m.group(1)
        bind_line = html[: m.start()].count("\n") + 1
        marker = html.find(f'id="{el}"')
        if marker < 0:
            FAILURES.append(f"{page.name}: binds #{el} (line {bind_line}) — no such element")
            continue
        markup_line = html[:marker].count("\n") + 1
        if markup_line > bind_line:
            FAILURES.append(
                f"{page.name}: binds #{el} at line {bind_line} but its markup is "
                f"at line {markup_line} — it will be null")

print(f"  checked {len(list((ROOT / 'site').glob('*.html')))} pages")
if FAILURES:
    print("  FAIL")
    for f in FAILURES:
        print("   ", f)
    sys.exit(1)
print("  PASS  every listener binds after its element exists")
