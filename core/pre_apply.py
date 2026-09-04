"""
Pre-apply analysis — one resume against one job description.

THE DIVISION OF LABOUR, AND WHY IT MATTERS HERE
-----------------------------------------------
The obvious design asks the model for everything: read both documents, return a
match percentage, the gaps and the timeline. It works in a demo and it breaks
the product.

`compute_fit()` already scores a resume against a job. It runs on every
application in every report. If a model produces a second percentage, the same
resume and the same advert score 72 on the pre-apply screen and 68 in the
report, and nobody can say which is wrong — including us. A number the system
cannot check is a number the system cannot defend, and every guard in this
codebase exists to avoid exactly that.

So:

    LLM      reads unstructured JD prose into structured requirements.
             This is the part only a model can do — adverts are written in
             paragraphs, in any order, with skills buried in sentences.

    Python   computes the match, the gaps and the timeline, using the same
             functions the rest of the product uses.

The model never sees a percentage and never produces one.

WHAT THE MODEL IS FORBIDDEN FROM DOING
--------------------------------------
Inventing a requirement. Asked to list what a job needs, a model will happily
add "strong communication skills" and "attention to detail" to a posting that
mentions neither, because those belong in its idea of a job advert. Every
returned skill must be traceable to a literal span of the input, and
`_verify_extraction` drops any that isn't.
"""

from __future__ import annotations

import asyncio
import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

PRE_APPLY_VERSION = "0.1.0"

TIMEOUT_SECONDS = 20


# ==========================================================================
# What the model is allowed to return
# ==========================================================================

class ExtractedRequirement(BaseModel):
    """One requirement, with the text it came from.

    `evidence` is the point of this model. Without it a hallucinated skill is
    indistinguishable from a real one; with it, verification is a substring
    check against the source.
    """
    skill: str = Field(..., max_length=60)
    necessity: Literal["required", "preferred"] = "required"
    evidence: str = Field(..., max_length=300,
                          description="Verbatim span from the job description")

    @field_validator("skill")
    @classmethod
    def _clean(cls, v: str) -> str:
        v = v.strip().strip(".,;:")
        if not v:
            raise ValueError("empty skill")
        return v


class JDExtraction(BaseModel):
    """The model's entire output. No scores, no advice, no timeline."""
    requirements: list[ExtractedRequirement] = Field(default_factory=list)
    years_required: int | None = Field(None, ge=0, le=40)
    seniority: Literal["junior", "mid", "senior", "staff", "principal",
                       "unknown"] = "unknown"


# ==========================================================================
# What the endpoint returns
# ==========================================================================

class MissingSkill(BaseModel):
    skill_name: str
    necessity: Literal["required", "preferred"]
    estimated_weeks_to_learn: int = Field(..., ge=0, le=52)
    weeks_range: str = Field(..., description="e.g. '4-8 weeks'")
    milestone: str = Field(..., description="What 'learned' means for this skill")
    evidence: str = Field(..., description="Where the advert asked for it")


class PreApplyAnalysis(BaseModel):
    """The response. Every number here was computed in Python."""
    overall_match_percentage: int = Field(..., ge=0, le=100)
    verdict: str
    band: Literal["strong", "stretch", "weak"]

    matched_skills: list[str] = Field(default_factory=list)
    listed_but_unevidenced: list[str] = Field(
        default_factory=list,
        description="On the resume, but only in a list — not inside an achievement")
    missing_skills: list[MissingSkill] = Field(default_factory=list)

    critical_bottleneck: str | None = Field(
        None, description="One sentence, only when a required skill is absent")

    total_weeks_to_close_gaps: int = 0
    years_required: int | None = None
    years_on_resume: float | None = None

    extraction_method: Literal["llm", "vocabulary"] = "vocabulary"
    note: str = ""
    version: str = PRE_APPLY_VERSION


# ==========================================================================
# The prompt
# ==========================================================================

EXTRACTION_PROMPT = """You are a technical recruiter reading one job advert.

Your only task is to list what this advert actually asks for. You are not
scoring anyone, not giving advice, and not comparing anything to a resume.

FOR EVERY SKILL YOU RETURN:
- `skill`     the technology or competency, named as the industry names it.
              "Kubernetes", not "container orchestration platform". "SQL",
              not "database query language".
- `necessity` "required" if the advert states it as a requirement, must-have
              or minimum. "preferred" for nice-to-have, bonus, a plus,
              desirable, or ideally.
- `evidence`  the exact words from the advert that state it. Copy them
              verbatim. Do not paraphrase, do not tidy the grammar, do not
              merge two sentences.

RULES, IN ORDER OF IMPORTANCE:

1. NEVER return a skill the advert does not name. This is the one that
   matters. Adverts are full of generic filler, and it is tempting to add
   "communication", "teamwork", "attention to detail" or "problem solving"
   because they belong in your idea of a job advert. If the words are not
   there, the skill is not there.

2. Every `evidence` string must appear literally in the input. If you cannot
   quote it, do not return the skill.

3. Do not split one skill into several. "Spring Boot" is one skill, not
   "Spring" and "Boot". "CI/CD" is one skill.

4. Do not merge distinct skills. "AWS and Kubernetes" is two entries.

5. Ignore the company description, the benefits, the equal-opportunity
   statement and the application instructions. Only the role.

6. `years_required` is the minimum years of experience the advert states, as
   an integer, or null if it does not state one. "3-5 years" means 3.

7. `seniority` from the job title and level language, or "unknown".

Return JSON only, matching the schema. No commentary."""


# ==========================================================================
# Verification — the guard
# ==========================================================================

def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def verify_extraction(extraction: JDExtraction, jd_text: str) -> tuple[JDExtraction, list[str]]:
    """Drop any requirement whose evidence isn't in the advert.

    The same principle as the verbatim guard on rejection evidence: a claim
    about what a document says has to be checkable against that document. A
    model that paraphrases its own evidence has produced an inference and
    labelled it a quotation.
    """
    haystack = _normalise(jd_text)
    kept: list[ExtractedRequirement] = []
    dropped: list[str] = []

    for req in extraction.requirements:
        needle = _normalise(req.evidence)
        if len(needle) < 4:
            dropped.append(f"{req.skill} (evidence too short to verify)")
            continue
        if needle in haystack:
            kept.append(req)
            continue
        # Allow a shortened quote: the first clause is often enough and models
        # trim trailing punctuation. Anything below this is a paraphrase.
        head = needle[:max(20, len(needle) // 2)]
        if head in haystack:
            kept.append(req)
        else:
            dropped.append(f"{req.skill} (evidence not found in the advert)")

    extraction.requirements = kept
    return extraction, dropped


# ==========================================================================
# The analysis — every number below is computed here, not returned by a model
# ==========================================================================

async def extract_requirements(jd_text: str, provider) -> tuple[JDExtraction, str, list[str]]:
    """Ask the model to read the advert. Falls back to the vocabulary.

    A timeout or a bad response degrades to deterministic extraction rather
    than failing: a slightly worse answer beats an error page, and the caller
    is told which path ran so the difference is never silent.
    """
    from .deterministic import default_vocab

    if provider is not None:
        try:
            raw = await asyncio.wait_for(
                provider.complete(EXTRACTION_PROMPT, jd_text,
                                  JDExtraction.model_json_schema()),
                timeout=TIMEOUT_SECONDS,
            )
            extraction = JDExtraction.model_validate(raw)
            extraction, dropped = verify_extraction(extraction, jd_text)
            if extraction.requirements:
                return extraction, "llm", dropped
        except asyncio.TimeoutError:
            pass
        except Exception:  # noqa: BLE001
            pass

    # Vocabulary path. Weaker — it only finds skills it already knows — but
    # it never invents one, so the guarantee is the same.
    vocab = default_vocab()

    # Split the advert at the nice-to-have boundary before matching. Without
    # this every skill comes back "required", and the bottleneck warning then
    # fires on a preferred skill — which trains people to ignore it.
    lowered = jd_text.lower()
    cut = len(jd_text)
    for marker in ("nice to have", "nice-to-have", "bonus", "a plus",
                   "desirable", "preferred:", "ideally", "would be great"):
        at = lowered.find(marker)
        if at != -1:
            cut = min(cut, at)

    required_text, preferred_text = jd_text[:cut], jd_text[cut:]
    req_names = set(vocab.find(required_text))
    pref_names = set(vocab.find(preferred_text)) - req_names

    reqs = [
        ExtractedRequirement(skill=name, necessity="required",
                             evidence=f"mentions {name}")
        for name in sorted(req_names)
    ] + [
        ExtractedRequirement(skill=name, necessity="preferred",
                             evidence=f"mentions {name}")
        for name in sorted(pref_names)
    ]
    years = None
    m = re.search(r"(\d+)\s*\+?\s*years?", jd_text, re.I)
    if m:
        years = min(40, int(m.group(1)))
    return JDExtraction(requirements=reqs, years_required=years), "vocabulary", []


def analyse(resume_text: str, jd_text: str, extraction: JDExtraction,
            method: str = "vocabulary") -> PreApplyAnalysis:
    """Score the pair, name the gaps, and cost the learning.

    Uses the same compute_fit() that scores every application in a report, so
    a job checked here and the same job in a report can never disagree.
    """
    from datetime import date

    from .analyze import parse_resume as build_profile
    from .deterministic import default_vocab
    from .enums import Seniority
    from .fit_score import JobRequirements, compute_fit
    from .insights import DEFAULT_LEARNING, LEARNING_WEEKS

    vocab = default_vocab()
    profile = build_profile(resume_text, vocab, today=date.today())

    required = [r.skill for r in extraction.requirements if r.necessity == "required"]
    preferred = [r.skill for r in extraction.requirements if r.necessity == "preferred"]

    # seniority is an enum on JobRequirements, not the plain string the model
    # returns. compute_fit requires one — passing None crashes in
    # _seniority_fit — so an unknown level falls back to MID, which is what
    # infer_seniority() does everywhere else in the product. Matching that
    # matters: a different default here would make the pre-apply score
    # disagree with the report for the same advert.
    try:
        seniority = (Seniority(extraction.seniority)
                     if extraction.seniority != "unknown" else Seniority.MID)
    except ValueError:
        seniority = Seniority.MID

    reqs = JobRequirements(
        required_skills=required,
        preferred_skills=preferred,
        min_years=extraction.years_required,
        seniority=seniority,
    )
    fit = compute_fit(profile, reqs)

    detail = fit.breakdown.get("skills_required", {})
    matched = detail.get("matched", [])
    weak = detail.get("weak", [])
    missing_names = detail.get("missing", [])

    evidence_for = {r.skill: r.evidence for r in extraction.requirements}
    necessity_for = {r.skill: r.necessity for r in extraction.requirements}

    missing: list[MissingSkill] = []
    for name in missing_names:
        lo, hi, milestone = LEARNING_WEEKS.get(name, DEFAULT_LEARNING)
        missing.append(MissingSkill(
            skill_name=name,
            necessity=necessity_for.get(name, "required"),
            # The midpoint, because a single integer has to be one number and
            # the low end reads as a promise. The range is carried alongside.
            estimated_weeks_to_learn=round((lo + hi) / 2),
            weeks_range=f"{lo}-{hi} weeks",
            milestone=milestone,
            evidence=evidence_for.get(name, ""),
        ))

    # Longest path, not the sum: nobody learns four things strictly in series,
    # and summing produces a discouraging number that isn't true.
    total_weeks = max((m.estimated_weeks_to_learn for m in missing), default=0)

    score = round(fit.overall)
    if score >= 80:
        band, verdict = "strong", "Strong match — worth a tailored application."
    elif score >= 60:
        band, verdict = "stretch", ("Stretch role. Close the gaps below before "
                                    "you send it, or expect silence.")
    else:
        band, verdict = "weak", ("Weak match. On your own history, applications "
                                 "like this rarely land.")

    # A bottleneck is a REQUIRED skill that is absent — not merely unevidenced,
    # and not a preferred one. Anything looser and the warning fires on every
    # application, which teaches people to ignore it.
    critical = None
    hard_gaps = [m for m in missing if m.necessity == "required"]
    if hard_gaps:
        worst = max(hard_gaps, key=lambda m: m.estimated_weeks_to_learn)
        critical = (
            f"{worst.skill_name} is listed as a requirement and isn't on your "
            f"resume. That's typically {worst.weeks_range} of work — decide "
            f"whether this role is worth it before you spend an hour tailoring."
        )

    note = ("Requirements were read from the advert by the model and each one "
            "checked against its own text." if method == "llm" else
            "Requirements were matched with the built-in vocabulary, which only "
            "recognises skills it already knows. Some of this advert may not "
            "have been read.")

    return PreApplyAnalysis(
        overall_match_percentage=score,
        verdict=verdict,
        band=band,
        matched_skills=matched,
        listed_but_unevidenced=weak,
        missing_skills=missing,
        critical_bottleneck=critical,
        total_weeks_to_close_gaps=total_weeks,
        years_required=extraction.years_required,
        years_on_resume=round(profile.total_experience_months / 12, 1),
        extraction_method=method,
        note=note,
    )
