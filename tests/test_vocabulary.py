"""
Vocabulary taxonomy checks — the rules that keep the parser deterministic.

WHY THIS IS A TEST AND NOT A CONVENTION
---------------------------------------
The vocabulary is the one file where a careless addition produces a wrong
number rather than an error. Two examples that have already happened:

  * "hiring" was added as an alias of Recruiting and fired on 96 of 96 job
    descriptions (HANDOVER 9.4).
  * "Astro" matched "astro-project" in ordinary prose during the second
    expansion.

Neither raised anything. Both changed scores. So the invariants get a test
that runs with the rest of the suite, and the test is the thing that has to
pass before an entry ships.

    python -m tests.test_vocabulary
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.deterministic import SkillVocab, default_vocab  # noqa: E402
from core.vocabulary import DOMAINS, all_entries, stats  # noqa: E402

FAILURES: list[str] = []
CHECKS = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    if ok:
        print(f"  ok    {name}")
    else:
        FAILURES.append(name)
        print(f"  FAIL  {name}")
        if detail:
            for line in detail.splitlines()[:14]:
                print(f"        {line}")


# --------------------------------------------------------------------------
# Rule 2 — an alias belongs to exactly one canonical
# --------------------------------------------------------------------------

def test_alias_uniqueness() -> None:
    """Two canonicals sharing an alias both fire on one mention.

    Nothing errors. The skill is counted twice, the requirement list grows by
    one, and the fit score moves. It is the most expensive kind of mistake
    this file can contain because it is invisible in the output.
    """
    owner: dict[str, list[str]] = defaultdict(list)
    for canonical, aliases in all_entries():
        for form in [canonical] + list(aliases):
            owner[form.strip().lower()].append(canonical)

    clashes = {f: cs for f, cs in owner.items() if len(set(cs)) > 1}
    check("every alias belongs to exactly one skill", not clashes,
          "\n".join(f"{f!r} claimed by {sorted(set(cs))}"
                    for f, cs in sorted(clashes.items())))


def test_canonical_uniqueness() -> None:
    seen: dict[str, int] = defaultdict(int)
    for canonical, _ in all_entries():
        seen[canonical.lower()] += 1
    dupes = {c: n for c, n in seen.items() if n > 1}
    check("no canonical is declared twice", not dupes, str(sorted(dupes)))


# --------------------------------------------------------------------------
# Rule 1 — nothing fires on ordinary English
# --------------------------------------------------------------------------

PROSE = """
We are a fast growing team and we celebrate wins. You will segment the market,
pull the right lever, and work in a greenhouse of ideas. The lake by the office
is lovely and the vault in the basement stays locked. Go beyond what is asked.
We have a notion that celery is a vegetable, that the helm of a ship is not a
chart, and that an iceberg is mostly underwater. Please bring drive, passion
and a can-do attitude. Our people are our greatest asset. We salt the pasta
water. The chef prepares lunch on Fridays. Take pride in your craft and expect
to wear many hats. This role reports into the wider group.
"""


def test_no_prose_false_positives() -> None:
    """A skill that fires on a sentence about lunch is not a skill."""
    hits = default_vocab().find(PROSE)
    check("no skill fires on ordinary prose", not hits,
          "\n".join(f"{k} fired {v}x" for k, v in sorted(hits.items())))


# Length was the wrong test. "AWS", "SQL", "dbt" and "EC2" are three
# characters and carry no risk; "Go", "Salt" and "Chef" are the danger, and
# "Chef" is four. What matters is whether the form is a word someone would
# write in a normal sentence.
ENGLISH = {
    "go", "r", "it", "at", "on", "in", "is", "be", "do", "by", "or", "as", "an",
    "no", "so", "up", "we", "us", "my", "me", "he", "she", "the", "and", "for",
    "you", "our", "can", "may", "new", "own", "use", "get", "see", "how", "why",
    "who", "all", "any", "one", "two", "out", "top", "end", "run", "set", "let",
    "salt", "chef", "puppet", "helm", "lever", "segment", "notion", "celery",
    "iceberg", "astro", "flink", "framer", "greenhouse", "swift", "rust", "kong",
    "envoy", "spark", "hive", "pig", "storm", "mesos", "beam", "arrow", "flow",
    "sage", "atlas", "nomad", "drone", "vault", "lake", "prime", "grid", "shift",
}


def test_no_dictionary_words_unguarded() -> None:
    """Rule 1, stated as the thing it is actually about.

    A form that appears in ordinary sentences must match only through an
    explicit alias. "Chef" the configuration tool cannot fire on a sentence
    about the person who cooks lunch.
    """
    guarded = SkillVocab.AMBIGUOUS_CANONICALS | SkillVocab.ENGLISH_WORDS
    loose = []
    for canonical, aliases in all_entries():
        for form in [canonical] + list(aliases):
            f = form.strip().lower()
            if " " in f or re.search(r"[.+#/]", f):
                continue                      # multi-word and punctuated are safe
            if f in ENGLISH and f not in guarded \
                    and canonical not in SkillVocab.CASE_SENSITIVE:
                loose.append(f"{form!r} (of {canonical})")
    check("no dictionary word matches unguarded or uncased", not loose,
          "\n".join(sorted(set(loose))))


# --------------------------------------------------------------------------
# Rule 4 — over-firing, measured against the corpus
# --------------------------------------------------------------------------

def test_no_over_firing() -> None:
    """A term present in nearly every posting describes postings, not roles."""
    folder = Path(__file__).resolve().parent.parent / "sample" / "jds"
    docs = [p.read_text(encoding="utf-8", errors="replace")
            for p in folder.glob("*.txt")]
    if len(docs) < 5:
        check("over-firing check has a corpus", False,
              f"only {len(docs)} job descriptions in sample/jds")
        return

    v = default_vocab()
    df: dict[str, int] = defaultdict(int)
    for d in docs:
        for skill in v.find(d):
            df[skill] += 1

    over = {s: c for s, c in df.items() if c / len(docs) >= 0.90}
    check(f"nothing fires on 90% of {len(docs)} job descriptions", not over,
          "\n".join(f"{s}: {c}/{len(docs)}" for s, c in sorted(over.items())))


# --------------------------------------------------------------------------
# Rule 3 — schema
# --------------------------------------------------------------------------

def test_schema() -> None:
    bad = []
    for domain, entries in DOMAINS.items():
        for entry in entries:
            if not (isinstance(entry, tuple) and len(entry) == 2):
                bad.append(f"{domain}: {entry!r} is not (canonical, aliases)")
                continue
            canonical, aliases = entry
            if not isinstance(canonical, str) or not canonical.strip():
                bad.append(f"{domain}: empty canonical")
            if not isinstance(aliases, list) or not all(isinstance(a, str) for a in aliases):
                bad.append(f"{domain}/{canonical}: aliases must be a list of strings")
            if any(a != a.strip().lower() for a in aliases):
                bad.append(f"{domain}/{canonical}: aliases must be lowercase and trimmed")
    check("every entry matches the schema", not bad, "\n".join(bad))


def test_no_alias_is_a_guarded_word() -> None:
    """An alias spelled like a guarded word defeats the guard.

    add() drops the canonical for a guarded name, then adds every alias — so
    listing the guarded word itself as an alias put it straight back. "Triage"
    was excluded as a canonical and re-added as the alias "triage", and fired
    on "triage your inbox". add() now filters these, and this check stops one
    being written in the first place.
    """
    words = SkillVocab.ENGLISH_WORDS
    bad = []
    for canonical, aliases in all_entries():
        for a in aliases:
            if a.strip().lower() in words:
                bad.append(f"{a!r} on {canonical}")
    check("no alias is an ordinary English word", not bad, "\n".join(sorted(bad)))


def test_aliases_cover_guarded_canonicals() -> None:
    """A guarded canonical with no usable alias can never match anything.

    Excluding "Chef" from bare matching is correct. Leaving it with no alias
    like "chef cookbook" means the skill silently stops existing.
    """
    guarded = SkillVocab.AMBIGUOUS_CANONICALS | SkillVocab.ENGLISH_WORDS
    orphans = []
    for canonical, aliases in all_entries():
        if canonical.lower() in guarded:
            usable = [a for a in aliases if a.strip().lower() not in guarded]
            if not usable:
                orphans.append(canonical)
    check("guarded skills still have a way to match", not orphans,
          ", ".join(sorted(orphans)))


def main() -> int:
    print("=" * 62)
    print("VOCABULARY TAXONOMY")
    s = stats()
    print(f"{s['skills']} skills · {s['aliases']} aliases · {s['domains']} domains")
    print("=" * 62)

    test_schema()
    test_canonical_uniqueness()
    test_alias_uniqueness()
    test_no_prose_false_positives()
    test_no_dictionary_words_unguarded()
    test_no_alias_is_a_guarded_word()
    test_aliases_cover_guarded_canonicals()
    test_no_over_firing()

    print()
    print("=" * 62)
    if FAILURES:
        print(f"{len(FAILURES)} FAILED of {CHECKS}: {', '.join(FAILURES)}")
        print("=" * 62)
        return 1
    print(f"ALL {CHECKS} TAXONOMY CHECKS PASSED")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
