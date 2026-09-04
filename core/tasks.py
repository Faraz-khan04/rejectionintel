"""
Background jobs with progress events.

WHY NOT CELERY OR RQ YET
------------------------
Both need Redis, a second process to supervise, and a deploy story. At your
current scale — one uploader at a time, a job that takes 20-60 seconds — that
is infrastructure you would maintain without benefit.

So this is an in-process queue behind an interface that RQ can implement
verbatim. `submit()`, `get()`, and the progress protocol are the whole surface.
When a single worker genuinely becomes the bottleneck, swap the executor for
`rq.Queue.enqueue` and the API layer does not change. The migration point is
documented in BLUEPRINT.md.

The real reason this file exists is not concurrency — it is that a 60-second
HTTP request times out behind most proxies and gives the user a blank screen
while it does. Decoupling buys the progress stepper.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum


class JobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class StepState(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass
class Step:
    key: str
    label: str
    state: StepState = StepState.PENDING
    detail: str = ""
    started_at: float | None = None
    ended_at: float | None = None

    @property
    def seconds(self) -> float | None:
        if self.started_at is None:
            return None
        return round((self.ended_at or time.time()) - self.started_at, 2)

    def as_dict(self) -> dict:
        return {"key": self.key, "label": self.label, "state": self.state.value,
                "detail": self.detail, "seconds": self.seconds}


# The pipeline, named for the user rather than for the code. These strings are
# the only place the stepper's copy lives.
PIPELINE = [
    ("ingest",   "Reading your CSV"),
    ("resume",   "Parsing your resume"),
    ("jobs",     "Extracting job requirements"),
    ("fit",      "Scoring every application"),
    ("stats",    "Testing for patterns"),
    ("narrate",  "Writing up the findings"),
]


@dataclass
class Job:
    id: str
    state: JobState = JobState.QUEUED
    steps: list[Step] = field(default_factory=lambda: [Step(k, l) for k, l in PIPELINE])
    result: dict | None = None
    error: str = ""
    created_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    _version: int = 0                       # bumped on every mutation; SSE polls this

    def step(self, key: str) -> Step:
        return next(s for s in self.steps if s.key == key)

    # --- progress protocol -------------------------------------------------
    def start(self, key: str, detail: str = "") -> None:
        s = self.step(key)
        s.state, s.detail, s.started_at = StepState.ACTIVE, detail, time.time()
        self._version += 1

    def update(self, key: str, detail: str) -> None:
        self.step(key).detail = detail
        self._version += 1

    def finish(self, key: str, detail: str = "") -> None:
        s = self.step(key)
        s.state, s.ended_at = StepState.DONE, time.time()
        if detail:
            s.detail = detail
        self._version += 1

    def skip(self, key: str, detail: str) -> None:
        s = self.step(key)
        s.state, s.detail, s.ended_at = StepState.SKIPPED, detail, time.time()
        self._version += 1

    def fail(self, key: str, detail: str) -> None:
        s = self.step(key)
        s.state, s.detail, s.ended_at = StepState.FAILED, detail, time.time()
        self._version += 1

    @property
    def elapsed(self) -> float:
        return round((self.ended_at or time.time()) - self.created_at, 2)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "state": self.state.value,
            "steps": [s.as_dict() for s in self.steps],
            "elapsed": self.elapsed,
            "error": self.error,
            "version": self._version,
            "result": self.result if self.state is JobState.DONE else None,
        }


class JobStore:
    """In-memory job registry with TTL eviction.

    Without eviction this is a memory leak that only shows up in production,
    a week after launch, on the box you can't easily restart.
    """

    def __init__(self, ttl_seconds: int = 1800, max_jobs: int = 500) -> None:
        self._jobs: dict[str, Job] = {}
        self.ttl = ttl_seconds
        self.max_jobs = max_jobs

    def create(self) -> Job:
        self._evict()
        job = Job(id=uuid.uuid4().hex[:16])
        self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def _evict(self) -> None:
        now = time.time()
        stale = [k for k, j in self._jobs.items()
                 if j.ended_at and now - j.ended_at > self.ttl]
        for k in stale:
            del self._jobs[k]
        if len(self._jobs) > self.max_jobs:
            for k, _ in sorted(self._jobs.items(),
                               key=lambda kv: kv[1].created_at)[:len(self._jobs) - self.max_jobs]:
                del self._jobs[k]


STORE = JobStore()


async def run_job(job: Job, work: Callable[[Job], Awaitable[dict]]) -> None:
    """Execute `work`, keeping job state truthful whatever happens.

    A crashed job must end in FAILED with a readable message. A job stuck in
    RUNNING forever is worse than one that failed, because the UI spins and
    the user never learns anything.
    """
    job.state = JobState.RUNNING
    job._version += 1
    try:
        job.result = await work(job)
        job.state = JobState.DONE
    except Exception as exc:  # noqa: BLE001
        job.state = JobState.FAILED
        job.error = f"{type(exc).__name__}: {exc}"
        for s in job.steps:
            if s.state is StepState.ACTIVE:
                s.state, s.ended_at = StepState.FAILED, time.time()
    finally:
        job.ended_at = time.time()
        job._version += 1


def active_store():
    """Whichever store the jobs are actually being written to.

    THE LANDMINE THIS DEFUSES
    -------------------------
    With QUEUE_BACKEND=rq the worker executing a job and the web process
    serving its SSE stream are DIFFERENT PROCESSES. An in-memory dict in the
    web process will never see a single update from the worker — so the
    stepper would sit at "queued" forever while the job completed fine.

    The failure is invisible in development, because with one process and the
    default backend both halves are the same object. It only appears the first
    time RQ is switched on, in production, looking like a hung analysis.
    """
    try:
        from . import queue_rq
        if queue_rq.enabled():
            return queue_rq.RedisJobStore()
    except Exception:  # noqa: BLE001
        pass          # redis missing or unreachable — fall back to in-process
    return STORE


async def stream_job(job_id: str, poll: float = 0.35, timeout: float = 300):
    """Async generator of Server-Sent Events for one job.

    Emits only when the version changes, so an idle job costs one comparison
    per tick rather than a repeated payload. Always emits a terminal event so
    the client can close cleanly instead of waiting for a socket timeout.
    """
    started = time.time()
    last_version = -1
    store = active_store()
    while True:
        job = store.get(job_id)
        if job is None:
            yield _sse("error", {"error": "job not found or expired"})
            return

        if job._version != last_version:
            last_version = job._version
            yield _sse("progress", job.as_dict())

        if job.state in (JobState.DONE, JobState.FAILED):
            yield _sse("done", job.as_dict())
            return

        if time.time() - started > timeout:
            yield _sse("error", {"error": "timed out waiting for the job"})
            return

        await asyncio.sleep(poll)


def _sse(event: str, data: dict) -> str:
    import json
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"
