"""
Offline JD extraction harness — no server, no database, no network unless asked.

WHY THIS EXISTS
---------------
Every skill gap, every fit score and every "you were missing X" sentence in the
product is downstream of one question: did the extractor actually see what the
advert asked for? When it does not, nothing crashes. The report is simply
computed against a fraction of the posting and states its conclusions with the
same confidence it would have had if it were right. That is the failure mode
described in HANDOVER 9.4, and it is invisible from inside the app.

This script makes it visible. Point it at a folder of raw job adverts and it
reports, per advert and across the corpus:

  * coverage   — how many requirement lines contained at least one recognised
                 skill, and which adverts parsed to nothing at all
  * unmapped   — terms that look like requirements but match no alias, ranked
                 by how many adverts they appear in (the alias backlog)
  * over-firing— canonicals that hit in nearly every advert, which is what
                 "hiring" did before it was removed from Recruiting
  * dead       — aliases that never fired once, which is vocabulary weight
                 carrying no signal

With --llm it additionally runs the real extract_llm.py path and diffs the
model's skill list against the cold-cache vocabulary. That diff is the highest
value output in the file: it is a list of things a real posting asks for that a
user's FIRST report cannot see.

The harness computes; it never judges. No score is produced here that the
product does not already produce for itself.

USAGE
-----
    python -m tools.jd_harness --dir sample/jds
    python -m tools.jd_harness --dir sample/jds --json out/coverage.json
    python -m tools.jd_harness --dir sample/jds --llm --budget 10
    python -m tools.jd_harness --dir sample/jds --min-coverage 0.60   # CI gate

Reads *.txt / *.md / *.jd. The filename (minus extension, underscores to
spaces) is used as the job title, which is what extract_jobs() expects.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path

# Run either as `python -m tools.jd_harness` or `python tools/jd_harness.py`.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.deterministic import (  # noqa: E402
    default_vocab, extract_min_years_required, infer_seniority,
)
from core.extract_llm import canonicalize, jd_hash  # noqa: E402
from core.vocabulary import all_entries, stats as vocab_stats  # noqa: E402

SUFFIXES = {".txt", ".md", ".jd"}

# A line is treated as a requirement line if it is a bullet or contains one of
# these framings. Deliberately broad: a false positive costs one noisy candidate
# term, a false negative hides a real gap.
_REQ_MARKERS = re.compile(
    r"(must have|required|requirements|you have|you'll need|you will need|"
    r"we're looking for|we are looking for|experience (with|in|of)|"
    r"proficien|working knowledge|familiar(ity)? with|strong (background|grasp)|"
    r"hands[- ]on|expertise|skills?:|qualifications|nice to have|bonus|"
    r"preferred|a plus)",
    re.I,
)
_BULLET = re.compile(r"^\s*([-*\u2022\u2023\u25cf\u25aa>+]|\d+[.)])\s+")

# Terms that survive the shape filters below but carry no skill signal. Kept
# short on purpose — a long stoplist hides the very terms this is meant to find.
_NOISE = {
    "the", "and", "you", "we", "our", "your", "this", "that", "with", "for",
    "will", "must", "have", "years", "year", "experience", "role", "team",
    "work", "working", "strong", "excellent", "good", "ability", "able",
    "knowledge", "understanding", "skills", "skill", "plus", "bonus", "nice",
    "preferred", "required", "requirements", "qualifications", "responsibilities",
    "about", "us", "job", "position", "candidate", "candidates", "applicants",
    "company", "please", "apply", "salary", "benefits", "location", "remote",
    "hybrid", "onsite", "full", "time", "part", "contract", "permanent",
    "monday", "friday", "equal", "opportunity", "employer", "diversity",
    "degree", "bachelor", "bachelors", "master", "masters", "phd", "university",
    "minimum", "maximum", "least", "including", "etc", "eg", "ie", "such",
    "environment", "business", "product", "products", "customer", "customers",
    "client", "clients", "stakeholders", "projects", "project", "development",
    "developer", "engineer", "engineering", "manager", "management", "senior",
    "junior", "lead", "principal", "staff", "director", "head", "new", "world",
    "best", "great", "high", "quality", "value", "values", "mission", "culture",
    "familiarity", "familiar", "proficiency", "proficient", "exposure", "grasp",
    "expertise", "background", "hands", "on", "solid", "deep", "proven",
    "comfortable", "someone", "successful", "valid", "wider", "own", "ideally",
    "what", "web", "core", "vitals", "employee", "day", "one", "two", "three",
}

# Shapes that make a token worth surfacing as a possible missing alias.
_SHAPE_VERSIONED = re.compile(r"^[a-z]+[.+#]?[a-z0-9]*\d")          # es6, k8s, vue3
_SHAPE_DOTTED = re.compile(r"^[a-z]+(\.[a-z]{2,4})+$", re.I)         # node.js, vue.js
_SHAPE_SYMBOL = re.compile(r"^[a-z]{1,10}(\+\+|#|/[a-z]{1,10})$", re.I)  # c++, c#, ci/cd
_SHAPE_ACRONYM = re.compile(r"^[A-Z]{2,6}$")                         # GCP, SRE, OTEL
_SHAPE_PROPER = re.compile(r"^[A-Z][a-zA-Z]{2,}$")                   # Kafka, Datadog


# --------------------------------------------------------------------------
# Per-document result
# --------------------------------------------------------------------------

@dataclass
class DocReport:
    name: str
    chars: int
    jd_hash: str
    title: str
    seniority: str
    min_years: int | None
    req_lines: int
    req_lines_covered: int
    skills: dict[str, int] = field(default_factory=dict)
    candidates: list[str] = field(default_factory=list)
    llm_skills: list[str] = field(default_factory=list)
    llm_unseen_by_vocab: list[str] = field(default_factory=list)
    llm_evidence_failures: list[str] = field(default_factory=list)
    llm_error: str = ""

    @property
    def coverage(self) -> float:
        return self.req_lines_covered / self.req_lines if self.req_lines else 0.0

    @property
    def is_silent_failure(self) -> bool:
        """Parsed to nothing on a document that clearly states requirements."""
        return self.req_lines >= 3 and not self.skills


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------

def load_docs(folder: Path) -> list[tuple[str, str]]:
    """(title, text) for every advert in the folder. UTF-8 then latin-1."""
    out: list[tuple[str, str]] = []
    for p in sorted(folder.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in SUFFIXES:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = p.read_text(encoding="latin-1")
        if text.strip():
            out.append((p.stem.replace("_", " ").replace("-", " "), text))
    return out


def requirement_lines(text: str) -> list[str]:
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if len(line) < 8:
            continue
        if _BULLET.match(raw) or _REQ_MARKERS.search(line):
            lines.append(line)
    return lines


# --------------------------------------------------------------------------
# Unmapped-term mining
# --------------------------------------------------------------------------

def _known_forms() -> set[str]:
    forms: set[str] = set()
    for canonical, aliases in all_entries():
        forms.add(canonical.lower())
        forms.update(a.lower().strip() for a in aliases)
    return forms


_FORM_CACHE: list[str] = []


def _forms_longest_first(vocab) -> list[str]:
    global _FORM_CACHE
    if not _FORM_CACHE:
        every = {f for forms in vocab.entries.values() for f in forms}
        _FORM_CACHE = sorted(every, key=len, reverse=True)
    return _FORM_CACHE


def mask_known(line: str, vocab) -> str:
    """Blank out every span the vocabulary already matched.

    Without this, "Spring Boot" is recognised and then "Boot" is reported as an
    unmapped term, which sends someone to add an alias that already exists. The
    candidate list is only useful if it is the residue.
    """
    masked = line
    # Longest form first: replacing "spring" before "spring boot" leaves an
    # orphaned "Boot" and sends someone to add an alias that already exists.
    for form in _forms_longest_first(vocab):
        pattern = rf"(?<![a-z0-9]){re.escape(form)}(?![a-z0-9+#])"
        masked = re.sub(pattern, " ", masked, flags=re.I)
    return masked


def candidate_terms(lines: list[str], known: set[str], vocab,
                    title: str = "") -> Counter:
    """Tokens in requirement lines that look like a named skill but match nothing.

    Shape-based, not model-based: a word is a candidate because of how it is
    written, never because something guessed it was important.
    """
    # Words from the job title are not missing aliases, they are the job title.
    title_words = {w.lower() for w in re.split(r"[^A-Za-z0-9]+", title) if w}
    found: Counter = Counter()
    for raw_line in lines:
        line = mask_known(raw_line, vocab)
        # Split on everything except the punctuation that lives inside skill
        # names: . + # / -
        for tok in re.split(r"[^A-Za-z0-9.+#/\-]+", line):
            tok = tok.strip(".-/")
            if not (2 <= len(tok) <= 24):
                continue
            low = tok.lower()
            if low in known or low in _NOISE or low in title_words or low.isdigit():
                continue
            if canonicalize(tok).lower() in known:
                continue
            shaped = (
                _SHAPE_VERSIONED.match(low)
                or _SHAPE_DOTTED.match(tok)
                or _SHAPE_SYMBOL.match(tok)
                or _SHAPE_ACRONYM.match(tok)
                or _SHAPE_PROPER.match(tok)
            )
            if shaped:
                found[tok] += 1
    return found


# --------------------------------------------------------------------------
# Deterministic pass
# --------------------------------------------------------------------------

def analyse_doc(title: str, text: str, vocab, known: set[str]) -> DocReport:
    lines = requirement_lines(text)
    hits = vocab.find(text)

    covered = 0
    for line in lines:
        if vocab.find(line):
            covered += 1

    cands = candidate_terms(lines, known, vocab, title)
    return DocReport(
        name=title,
        chars=len(text),
        jd_hash=jd_hash(text),
        title=title,
        seniority=infer_seniority(title).value,
        min_years=extract_min_years_required(text),
        req_lines=len(lines),
        req_lines_covered=covered,
        skills=dict(sorted(hits.items(), key=lambda kv: -kv[1])),
        candidates=[t for t, _ in cands.most_common(15)],
    )


def dead_aliases(docs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """(canonical, alias) pairs that never matched anything in the corpus."""
    corpus = "\n".join(t for _, t in docs).lower()
    dead: list[tuple[str, str]] = []
    for canonical, aliases in all_entries():
        for alias in aliases:
            pattern = rf"(?<![a-z0-9]){re.escape(alias.lower())}(?![a-z0-9+#])"
            if not re.search(pattern, corpus):
                dead.append((canonical, alias))
    return dead


# --------------------------------------------------------------------------
# LLM pass (opt-in) — the same code path the app uses, nothing bespoke
# --------------------------------------------------------------------------

def _normalise(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


async def run_llm(docs: list[tuple[str, str]], reports: dict[str, DocReport],
                  budget: int) -> str:
    from core.extract_llm import extract_jobs
    from core.llm_provider import get_provider

    status = get_provider()
    if status.provider is None:
        return status.reason or "no provider configured"

    from core.extract_llm import ExtractionResult  # noqa: F401  (typing only)
    result = await extract_jobs(docs, provider=status.provider, budget=budget)

    by_hash = {jd_hash(text): (title, text) for title, text in docs}
    known = _known_forms()

    for h, job in result.extracted.items():
        title, text = by_hash.get(h, ("", ""))
        rep = reports.get(title)
        if rep is None:
            continue
        haystack = _normalise(text)
        for skill in job.hard_skills:
            rep.llm_skills.append(skill.name)
            if skill.name.lower() not in known:
                # The model saw a requirement the cold-cache vocabulary is blind
                # to. This is the alias backlog, sourced from real postings.
                rep.llm_unseen_by_vocab.append(skill.name)
            needle = _normalise(skill.evidence)
            if len(needle) < 4 or needle not in haystack:
                rep.llm_evidence_failures.append(skill.name)

    for err in result.errors:
        name = err.split(":", 1)[0]
        if name in reports:
            reports[name].llm_error = err

    return (f"provider={status.provider.info.name} "
            f"model={status.provider.info.model} "
            f"llm={result.from_llm} cache={result.from_cache} "
            f"failed={result.failed} skipped={result.skipped}")


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

def bar(fraction: float, width: int = 20) -> str:
    filled = int(round(fraction * width))
    return "#" * filled + "." * (width - filled)


def print_report(reports: list[DocReport], docs: list[tuple[str, str]],
                 llm_note: str, show_dead: bool) -> None:
    n = len(reports)
    vs = vocab_stats()
    print()
    print("=" * 78)
    print(f"JD EXTRACTION HARNESS — {n} advert(s)")
    print(f"vocabulary: {vs['skills']} skills · {vs['aliases']} aliases · "
          f"{vs['domains']} domains")
    if llm_note:
        print(f"llm pass:   {llm_note}")
    print("=" * 78)

    print("\nPER ADVERT")
    print(f"  {'advert':<32} {'req':>4} {'cov':>6}  {'skills':>6}  bar")
    for r in sorted(reports, key=lambda x: x.coverage):
        flag = "  <-- parsed to nothing" if r.is_silent_failure else ""
        print(f"  {r.name[:32]:<32} {r.req_lines:>4} {r.coverage:>5.0%}  "
              f"{len(r.skills):>6}  {bar(r.coverage)}{flag}")

    silent = [r for r in reports if r.is_silent_failure]
    total_req = sum(r.req_lines for r in reports)
    total_cov = sum(r.req_lines_covered for r in reports)
    overall = total_cov / total_req if total_req else 0.0

    print(f"\n  overall requirement-line coverage: {overall:.0%} "
          f"({total_cov}/{total_req})")
    if silent:
        print(f"  adverts that parsed to nothing:    {len(silent)} "
              f"({', '.join(r.name[:24] for r in silent[:5])})")

    # Over-firing: the 'hiring' trap. A term in nearly every advert is a
    # property of job adverts, not of the role.
    df: Counter = Counter()
    for r in reports:
        df.update(r.skills.keys())
    over = [(s, c) for s, c in df.most_common() if n >= 5 and c / n >= 0.90]
    if over:
        print("\nOVER-FIRING CANONICALS (present in >=90% of adverts)")
        for s, c in over:
            print(f"  {s:<28} {c}/{n}")
        print("  Check the aliases: a term this common describes the document,"
              " not the job.")

    # The alias backlog.
    cand_df: Counter = Counter()
    for r in reports:
        cand_df.update(set(r.candidates))
    if cand_df:
        print("\nUNMAPPED TERMS IN REQUIREMENT LINES (by advert count)")
        for term, c in cand_df.most_common(25):
            print(f"  {term:<28} {c}/{n}")

    llm_backlog: Counter = Counter()
    ev_fail: Counter = Counter()
    for r in reports:
        llm_backlog.update(set(r.llm_unseen_by_vocab))
        ev_fail.update(set(r.llm_evidence_failures))
    if llm_backlog:
        print("\nMODEL SAW, VOCABULARY CANNOT (highest-value backlog)")
        for term, c in llm_backlog.most_common(30):
            print(f"  {term:<28} {c}/{n}")
    if ev_fail:
        print("\nEVIDENCE NOT FOUND VERBATIM IN THE ADVERT")
        for term, c in ev_fail.most_common(20):
            print(f"  {term:<28} {c}")
        print("  These would be dropped by the verbatim guard in production.")

    if show_dead:
        dead = dead_aliases(docs)
        print(f"\nALIASES THAT NEVER FIRED: {len(dead)}")
        grouped: dict[str, list[str]] = defaultdict(list)
        for canonical, alias in dead:
            grouped[canonical].append(alias)
        for canonical, aliases in sorted(grouped.items())[:30]:
            print(f"  {canonical:<28} {', '.join(aliases[:6])}")

    print()


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Offline JD extraction coverage harness")
    ap.add_argument("--dir", required=True, help="folder of raw advert .txt files")
    ap.add_argument("--json", help="write the full result to this path")
    ap.add_argument("--llm", action="store_true",
                    help="also run the real extract_llm.py path (costs API calls)")
    ap.add_argument("--budget", type=int, default=10,
                    help="max LLM calls (default 10)")
    ap.add_argument("--dead-aliases", action="store_true",
                    help="list vocabulary aliases that never matched")
    ap.add_argument("--min-coverage", type=float, default=None,
                    help="exit 1 if overall requirement-line coverage is below this")
    args = ap.parse_args(argv)

    folder = Path(args.dir)
    if not folder.is_dir():
        print(f"not a folder: {folder}", file=sys.stderr)
        return 2

    docs = load_docs(folder)
    if not docs:
        print(f"no .txt/.md/.jd adverts found in {folder}", file=sys.stderr)
        return 2

    vocab = default_vocab()
    known = _known_forms()
    reports = {title: analyse_doc(title, text, vocab, known) for title, text in docs}

    llm_note = ""
    if args.llm:
        llm_note = asyncio.run(run_llm(docs, reports, args.budget))

    ordered = list(reports.values())
    print_report(ordered, docs, llm_note, args.dead_aliases)

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "adverts": len(ordered),
            "vocabulary": vocab_stats(),
            "overall_coverage": (
                sum(r.req_lines_covered for r in ordered)
                / max(1, sum(r.req_lines for r in ordered))
            ),
            "documents": [
                {**asdict(r), "coverage": r.coverage,
                 "silent_failure": r.is_silent_failure}
                for r in ordered
            ],
        }
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"wrote {out}")

    if args.min_coverage is not None:
        total_req = sum(r.req_lines for r in ordered)
        overall = sum(r.req_lines_covered for r in ordered) / max(1, total_req)
        if overall < args.min_coverage:
            print(f"FAIL: coverage {overall:.0%} < required "
                  f"{args.min_coverage:.0%}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
