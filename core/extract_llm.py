"""
Job description extraction — hybrid LLM + deterministic, with a cache.

REPLACES THE REGEX PATH
-----------------------
core/analyze.py currently matches JDs against a hand-written 15-skill
vocabulary. Real postings mention hundreds of skills in prose the vocabulary
will never contain. This is the largest quality gap in the product.

THREE THINGS MAKE THIS SAFE
---------------------------
1. A Pydantic schema the model must fill. Free-form JSON drifts; a schema
   with validators does not.

2. Skill canonicalisation AFTER extraction. The model may return "Postgres",
   "PostgreSQL" or "psql"; all three collapse to one canonical skill before
   anything is counted. Without this the same skill splits into three
   under-powered hypotheses and none of them survives FDR correction.

3. A content-hash cache. Two users applying to the same Stripe req should
   cost one extraction, forever. This is the difference between a per-report
   cost that grows with users and one that grows with distinct job postings.

WHAT THE LLM IS STILL NOT ALLOWED TO DO
---------------------------------------
Score anything. It reads a posting and returns structured facts about the
posting. Fit scores, rates and intervals stay in deterministic code.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field, field_validator

from .enums import Seniority

EXTRACT_VERSION = "0.2.0"


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------

class ExtractedSkill(BaseModel):
    name: str = Field(description="The skill, tool or technology, e.g. 'SQL', 'Docker'")
    required: bool = Field(description="True if required, False if preferred/nice-to-have")
    evidence: str = Field(default="", description="Short verbatim span from the posting")

    @field_validator("name")
    @classmethod
    def _tidy(cls, v: str) -> str:
        v = re.sub(r"\s+", " ", v).strip(" .,;:")
        if not v:
            raise ValueError("empty skill name")
        return v[:60]

    @field_validator("evidence")
    @classmethod
    def _trim(cls, v: str) -> str:
        return re.sub(r"\s+", " ", v).strip()[:200]


class ExtractedJob(BaseModel):
    """What we allow a model to assert about a job posting."""
    hard_skills: list[ExtractedSkill] = Field(default_factory=list, max_length=30)
    soft_skills: list[str] = Field(default_factory=list, max_length=12)
    seniority: str = Field(default="mid")
    min_years_experience: int | None = Field(default=None, ge=0, le=40)
    max_years_experience: int | None = Field(default=None, ge=0, le=40)
    industry: str | None = Field(default=None, max_length=60)
    function: str | None = Field(default=None, max_length=60)
    remote_policy: str | None = Field(default=None, max_length=20)
    requires_degree: bool = False
    management_scope: bool = Field(default=False, description="Does the role manage people?")

    @field_validator("seniority")
    @classmethod
    def _known(cls, v: str) -> str:
        v = (v or "mid").strip().lower().replace(" ", "_").replace("+", "_plus")
        valid = {s.value for s in Seniority}
        return v if v in valid else "mid"

    @field_validator("soft_skills")
    @classmethod
    def _tidy_soft(cls, v: list[str]) -> list[str]:
        out, seen = [], set()
        for s in v:
            s = re.sub(r"\s+", " ", str(s)).strip(" .,;:")[:50]
            k = s.lower()
            if s and k not in seen:
                seen.add(k)
                out.append(s)
        return out

    def model_post_init(self, _ctx) -> None:
        # A model that returns min=8, max=3 has misread the posting; drop the
        # pair rather than propagate a nonsense range into the fit score.
        if (self.min_years_experience is not None
                and self.max_years_experience is not None
                and self.min_years_experience > self.max_years_experience):
            object.__setattr__(self, "max_years_experience", None)


SYSTEM = """You extract structured facts from job postings. You are a parser, not an evaluator.

RULES:
1. Only report what the posting states. Never infer a skill because it is "usually" \
needed for this kind of role.
2. required=true only when the posting frames it as a requirement or minimum. \
Anything under "nice to have", "bonus", "preferred" or "a plus" is required=false.
3. Use the shortest common name for a skill: "SQL" not "SQL query writing", \
"Docker" not "containerisation with Docker".
4. evidence must be copied verbatim from the posting, under 20 words.
5. seniority must be one of: intern, junior, mid, senior, staff, principal, \
director, vp_plus. Judge from the title and the stated years, not from tone.
6. If a field is not stated, leave it null. Do not guess."""


# --------------------------------------------------------------------------
# Canonicalisation
# --------------------------------------------------------------------------

CANONICAL: dict[str, list[str]] = {
    "SQL": ["sql", "postgresql", "postgres", "mysql", "t-sql", "psql", "sql queries",
            "relational databases", "rdbms"],
    "Python": ["python", "python3", "py"],
    "JavaScript": ["javascript", "js", "es6", "ecmascript"],
    "TypeScript": ["typescript", "ts"],
    "React": ["react", "react.js", "reactjs"],
    "Docker": ["docker", "containers", "containerisation", "containerization"],
    "Kubernetes": ["kubernetes", "k8s", "eks", "gke"],
    "AWS": ["aws", "amazon web services", "ec2", "s3", "lambda"],
    "A/B Testing": ["a/b testing", "ab testing", "split testing", "experimentation",
                    "experiment design", "controlled experiments"],
    "Roadmapping": ["roadmap", "roadmapping", "product roadmap", "roadmap planning"],
    "Figma": ["figma"],
    "Jira": ["jira", "atlassian"],
    "Tableau": ["tableau"],
    "Looker": ["looker", "lookml"],
    "Amplitude": ["amplitude"],
    "Mixpanel": ["mixpanel"],
    "Excel": ["excel", "spreadsheets", "google sheets"],
    "Machine Learning": ["machine learning", "ml", "deep learning", "modelling",
                         "predictive modeling", "predictive modelling"],
    "Agile": ["agile", "scrum", "kanban", "agile methodologies", "sprint planning"],
    "API Design": ["api design", "rest api", "restful", "graphql", "api development"],
    "Stakeholder Management": ["stakeholder management", "stakeholder", "cross-functional",
                               "cross functional collaboration"],
    "Data Analysis": ["data analysis", "analytics", "data analytics", "quantitative analysis"],
    "Git": ["git", "github", "gitlab", "version control"],
    "CI/CD": ["ci/cd", "cicd", "continuous integration", "continuous deployment", "jenkins"],
}

_LOOKUP: dict[str, str] = {}
for canon, variants in CANONICAL.items():
    _LOOKUP[canon.lower()] = canon
    for v in variants:
        _LOOKUP[v.lower()] = canon


def canonicalize(name: str) -> str:
    """Collapse a variant onto its canonical skill.

    Unknown skills pass through title-cased rather than being dropped — the
    vocabulary should grow from what real postings actually say, and silently
    discarding them would hide that signal.

    >>> canonicalize("PostgreSQL"), canonicalize("k8s"), canonicalize("Rust")
    ('SQL', 'Kubernetes', 'Rust')
    """
    n = re.sub(r"\s+", " ", (name or "")).strip().lower().strip(" .,;:")
    if n in _LOOKUP:
        return _LOOKUP[n]
    n2 = re.sub(r"\s*\([^)]*\)", "", n).strip()
    if n2 in _LOOKUP:
        return _LOOKUP[n2]
    return name.strip()[:60]


# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------

def jd_hash(text: str) -> str:
    """Stable hash over normalised JD text.

    Normalised first so that whitespace differences and a changed application
    deadline don't produce a cache miss on an otherwise identical posting.
    """
    n = re.sub(r"\s+", " ", (text or "").lower()).strip()
    n = re.sub(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", "", n)
    return hashlib.sha256(n.encode()).hexdigest()[:32]


class ExtractionCache:
    """In-memory reference implementation.

    In production this is a SELECT on jobs.jd_hash — see BLUEPRINT.md. The
    interface is deliberately tiny so the swap is a one-line change.
    """

    def __init__(self) -> None:
        self._store: dict[str, ExtractedJob] = {}
        self.hits = 0
        self.misses = 0

    def get(self, h: str) -> ExtractedJob | None:
        v = self._store.get(h)
        if v is not None:
            self.hits += 1
        else:
            self.misses += 1
        return v

    def put(self, h: str, v: ExtractedJob) -> None:
        self._store[h] = v

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------

@dataclass
class ExtractionResult:
    extracted: dict[str, ExtractedJob] = field(default_factory=dict)   # hash -> job
    from_cache: int = 0
    from_llm: int = 0
    failed: int = 0
    skipped: int = 0          # over budget, deferred to a later run
    errors: list[str] = field(default_factory=list)


def to_pydantic_schema() -> dict:
    """JSON schema for the provider layer.

    Pydantic generates this, so the prompt contract and the validation contract
    can never drift apart — a class you can't drift from is worth more than a
    prompt you have to remember to update.
    """
    schema = ExtractedJob.model_json_schema()
    schema.pop("$defs", None)
    return schema


async def extract_jobs(
    jds: list[tuple[str, str]],          # (job_title, jd_text)
    provider=None,
    cache: ExtractionCache | None = None,
    concurrency: int | None = None,
    budget: int | None = None,
) -> ExtractionResult:
    """Extract many postings, deduplicated by content hash.

    Identical postings are extracted once even within a single upload — a user
    who applied to three roles at the same company often pasted the same JD.
    """
    from .llm_provider import RateLimited, complete_with_backoff

    cache = cache if cache is not None else ExtractionCache()
    result = ExtractionResult()

    # Deduplicate before spending anything.
    unique: dict[str, tuple[str, str]] = {}
    for title, text in jds:
        if not (text or "").strip():
            continue
        unique.setdefault(jd_hash(text), (title, text))

    pending: list[tuple[str, str, str]] = []
    for h, (title, text) in unique.items():
        cached = cache.get(h)
        if cached is not None:
            result.extracted[h] = cached
            result.from_cache += 1
        else:
            pending.append((h, title, text))

    if provider is None or not pending:
        return result

    # Hard call ceiling. Without this a cold cache on an 88-application upload
    # fires 88 requests against a 20-per-day free tier and exhausts the quota
    # on a single report. Whatever we skip stays pending for the next run.
    if budget is not None:
        result.skipped = max(0, len(pending) - budget)
        pending = pending[:budget]

    limit = concurrency or provider.info.max_concurrency
    sem = asyncio.Semaphore(limit)
    schema = to_pydantic_schema()

    async def one(h: str, title: str, text: str):
        async with sem:
            user = (f"JOB TITLE: {title}\n\nPOSTING:\n{text[:6000]}")
            try:
                raw = await complete_with_backoff(provider, SYSTEM, user, schema)
            except RateLimited:
                result.failed += 1
                result.errors.append(f"{title}: rate limited")
                return
            except Exception as exc:  # noqa: BLE001
                result.failed += 1
                result.errors.append(f"{title}: {type(exc).__name__}")
                return
            try:
                job = ExtractedJob.model_validate(raw)
            except Exception as exc:  # noqa: BLE001
                # Schema violation is a failure, not something to paper over.
                result.failed += 1
                result.errors.append(f"{title}: schema rejected ({type(exc).__name__})")
                return
            for s in job.hard_skills:
                s.name = canonicalize(s.name)
            try:
                cache.put(h, job, provider.info.model)
            except TypeError:
                cache.put(h, job)          # in-memory cache takes two args
            result.extracted[h] = job
            result.from_llm += 1

    await asyncio.gather(*[one(h, t, x) for h, t, x in pending])
    return result


def to_requirements(job: ExtractedJob):
    """Bridge into the existing deterministic fit scorer."""
    from .fit_score import JobRequirements

    req = sorted({s.name for s in job.hard_skills if s.required})
    pref = sorted({s.name for s in job.hard_skills if not s.required} - set(req))
    return JobRequirements(
        required_skills=req,
        preferred_skills=pref,
        min_years=job.min_years_experience,
        seniority=Seniority(job.seniority),
        industry=job.industry,
    )
