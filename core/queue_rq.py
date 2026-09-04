"""
RQ backend — the same job interface, backed by Redis.

WHY THIS IS OPT-IN AND NOT THE DEFAULT
--------------------------------------
The in-process queue in core/tasks.py is not a placeholder. At one concurrent
uploader with a 20-60 second job it does everything RQ does, with no Redis to
run, no second process to supervise, and no deploy story. Switching to RQ
before you need it buys operational burden and nothing else.

TURN IT ON WHEN ANY OF THESE IS TRUE
------------------------------------
1. Two uploads regularly overlap. One worker becomes a real queue, and users
   start waiting behind each other.
2. Jobs must survive a restart. An in-process job dies with the process; a
   deploy mid-analysis loses it silently.
3. You add scheduled work — nightly re-analysis, cache warming on a timer.

USAGE
-----
    pip install rq redis
    QUEUE_BACKEND=rq REDIS_URL=redis://localhost:6379/0 python run.py
    rq worker analysis --url redis://localhost:6379/0     # in a second shell

THE INTERFACE IS THE POINT
--------------------------
`create`, `get`, and the progress protocol are identical to JobStore, so
api/main.py and every line of frontend JavaScript are untouched by the switch.
Job state lives in Redis rather than a dict; the SSE stream polls it the same
way. If this file ever needs the API layer to change, the abstraction has
failed and should be fixed here rather than there.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

from .tasks import PIPELINE, Job, JobState, Step, StepState

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
QUEUE_NAME = os.environ.get("RQ_QUEUE", "analysis")
JOB_TTL = 60 * 60          # how long a finished job stays readable
JOB_TIMEOUT = 60 * 15      # kill a job that hangs rather than leaking a worker


def backend_name() -> str:
    return os.environ.get("QUEUE_BACKEND", "memory").strip().lower()


def enabled() -> bool:
    return backend_name() == "rq"


# --------------------------------------------------------------------------
# Serialisation
# --------------------------------------------------------------------------

def job_to_dict(job: Job) -> dict:
    return {
        "id": job.id,
        "state": job.state.value,
        "error": job.error,
        "created_at": job.created_at,
        "ended_at": job.ended_at,
        "version": job._version,
        "steps": [
            {"key": s.key, "label": s.label, "state": s.state.value,
             "detail": s.detail, "started_at": s.started_at, "ended_at": s.ended_at}
            for s in job.steps
        ],
        "result": job.result,
    }


def job_from_dict(data: dict) -> Job:
    job = Job(id=data["id"])
    job.state = JobState(data["state"])
    job.error = data.get("error", "")
    job.created_at = data.get("created_at", time.time())
    job.ended_at = data.get("ended_at")
    job._version = data.get("version", 0)
    job.result = data.get("result")
    job.steps = [
        Step(key=s["key"], label=s["label"], state=StepState(s["state"]),
             detail=s.get("detail", ""), started_at=s.get("started_at"),
             ended_at=s.get("ended_at"))
        for s in data.get("steps", [])
    ] or [Step(k, l) for k, l in PIPELINE]
    return job


# --------------------------------------------------------------------------
# Store
# --------------------------------------------------------------------------

class RedisJobStore:
    """Drop-in replacement for JobStore.

    Every mutation writes the whole job back to Redis. That is more traffic
    than a delta protocol, but a job document is a couple of kilobytes and
    correctness under two processes matters more than bytes on the wire —
    partial updates are where distributed state goes wrong.
    """

    def __init__(self, url: str | None = None) -> None:
        import redis  # imported lazily so the app runs without it installed

        self._r = redis.Redis.from_url(url or REDIS_URL, decode_responses=True)

    def _key(self, job_id: str) -> str:
        return f"ri:job:{job_id}"

    def create(self) -> Job:
        job = Job(id=uuid.uuid4().hex[:16])
        self.save(job)
        return job

    def get(self, job_id: str) -> Job | None:
        raw = self._r.get(self._key(job_id))
        if not raw:
            return None
        try:
            return job_from_dict(json.loads(raw))
        except Exception:  # noqa: BLE001
            return None

    def save(self, job: Job) -> None:
        self._r.set(self._key(job.id), json.dumps(job_to_dict(job)), ex=JOB_TTL)

    def ping(self) -> bool:
        try:
            return bool(self._r.ping())
        except Exception:  # noqa: BLE001
            return False


class TrackedJob(Job):
    """A Job that persists itself on every progress call.

    The worker and the web process are different processes, so an in-memory
    mutation is invisible to the SSE stream. Overriding the five progress
    methods is enough because nothing else mutates job state.
    """

    def __init__(self, job_id: str, store: RedisJobStore) -> None:
        super().__init__(id=job_id)
        self._store = store

    def _flush(self) -> None:
        self._store.save(self)

    def start(self, key: str, detail: str = "") -> None:
        super().start(key, detail); self._flush()

    def update(self, key: str, detail: str) -> None:
        super().update(key, detail); self._flush()

    def finish(self, key: str, detail: str = "") -> None:
        super().finish(key, detail); self._flush()

    def skip(self, key: str, detail: str) -> None:
        super().skip(key, detail); self._flush()

    def fail(self, key: str, detail: str) -> None:
        super().fail(key, detail); self._flush()


# --------------------------------------------------------------------------
# Worker entry point
# --------------------------------------------------------------------------

def run_analysis_task(job_id: str, payload: dict[str, Any]) -> dict:
    """Executed inside the RQ worker.

    Takes plain data rather than a closure because RQ pickles its arguments and
    a closure over an UploadFile will not survive the trip.
    """
    import asyncio

    from .analyze import analyze_async
    from .ingest_v2 import ingest_csv

    store = RedisJobStore()
    job = TrackedJob(job_id, store)
    job.state = JobState.RUNNING
    store.save(job)

    async def work() -> dict:
        from .narration import narrate_report
        from .llm_provider import get_provider

        job.start("ingest")
        report_in = ingest_csv(payload["csv_text"])
        job.finish("ingest", f"{report_in.rows_accepted}/{report_in.rows_total} rows")

        job.start("resume")
        job.finish("resume", f"{len(payload['resume_text'].split())} words")

        job.start("jobs")
        result = await analyze_async(
            report_in, payload["resume_text"],
            provider=get_provider().provider,
            mode=os.environ.get("JD_EXTRACT", "auto"),
            job=job,
        )

        job.start("fit"); job.finish("fit", f"{len(result.applications)} scored")
        job.start("stats"); job.finish("stats", f"{len(result.result.findings)} tested")

        job.start("narrate")
        narrated = await narrate_report(
            result.result, result.result.findings, result.profile)
        job.finish("narrate", f"{narrated.provider} · {narrated.calls_made} calls")

        from .analyze import to_json
        out = to_json(result)
        out["ok"] = True
        return out

    try:
        job.result = asyncio.run(work())
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
        store.save(job)

    return job.result or {}


def enqueue(payload: dict[str, Any]) -> str:
    """Queue a job and return its id."""
    import redis
    from rq import Queue

    store = RedisJobStore()
    job = store.create()

    q = Queue(QUEUE_NAME, connection=redis.Redis.from_url(REDIS_URL))
    q.enqueue(run_analysis_task, job.id, payload,
              job_timeout=JOB_TIMEOUT, result_ttl=JOB_TTL)
    return job.id


def health() -> dict:
    """Reported by /api/health so a misconfigured queue is visible immediately
    rather than presenting as jobs that queue and never start."""
    if not enabled():
        return {"backend": "memory", "ok": True}
    try:
        store = RedisJobStore()
        return {"backend": "rq", "ok": store.ping(), "redis": REDIS_URL,
                "queue": QUEUE_NAME}
    except ImportError:
        return {"backend": "rq", "ok": False,
                "error": "QUEUE_BACKEND=rq but rq/redis aren't installed. "
                         "Run: pip install rq redis"}
    except Exception as exc:  # noqa: BLE001
        return {"backend": "rq", "ok": False, "error": str(exc)}
