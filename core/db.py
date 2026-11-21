"""
Database layer.

SQLITE FIRST, POSTGRES LATER — DELIBERATELY
-------------------------------------------
The blueprint says Postgres, and production should be Postgres. But installing
and supervising it on a Windows dev box is a real cost paid today for a benefit
that only arrives at deploy time. SQLAlchemy makes the difference a connection
string, and every model here is written to work on both:

  - JSON, not JSONB
  - no server-side defaults beyond now()
  - no array or range columns

Set DATABASE_URL to a postgres:// URL and nothing else changes.

WHAT PERSISTENCE ACTUALLY BUYS
------------------------------
Not "uploads survive a refresh" — that's the least of it. The real wins:

1. The JD extraction cache stops resetting. On the free tier a report needs
   88 extractions against a 20/day quota; without persistence the warming
   loop restarts every time the server does, and full coverage is never
   reached. With it, warming completes over a few days and stays done.

2. Re-uploading a superset CSV becomes idempotent, which is the normal way
   people use a spreadsheet.

3. Fit scores only recompute when the algorithm version changes.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_URL = f"sqlite:///{ROOT / 'rejectionintel.db'}"


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_URL)


def _make_engine():
    url = database_url()
    if url.startswith("sqlite"):
        eng = create_engine(
            url,
            # FastAPI runs handlers on a threadpool; the default SQLite check
            # rejects a connection reused across threads.
            connect_args={"check_same_thread": False},
            future=True,
        )

        @event.listens_for(eng, "connect")
        def _pragmas(conn, _rec):
            cur = conn.cursor()
            # WAL lets a read proceed while a write is in flight. Without it a
            # dashboard poll during an upload raises "database is locked".
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.close()

        return eng
    # Bounded on purpose. SQLAlchemy's default is 5 + 10 overflow PER PROCESS;
    # four web workers and two RQ workers therefore demand up to 90 connections
    # from a database that typically allows 100, and the failure presents as
    # random timeouts rather than "out of connections".
    #
    # pool_recycle matters just as much: managed Postgres and PgBouncer both
    # close idle connections, and without recycling we hand out a socket the
    # server already dropped.
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=int(os.environ.get("DB_POOL_SIZE", "5")),
        max_overflow=int(os.environ.get("DB_MAX_OVERFLOW", "5")),
        pool_recycle=int(os.environ.get("DB_POOL_RECYCLE", "1800")),
        pool_timeout=int(os.environ.get("DB_POOL_TIMEOUT", "30")),
        future=True,
        connect_args={
            "connect_timeout": int(os.environ.get("DB_CONNECT_TIMEOUT", "10")),
            "application_name": "rejectionintel",
        },
    )


ENGINE = _make_engine()
SessionLocal = sessionmaker(bind=ENGINE, expire_on_commit=False, future=True)


def dispose_pool() -> None:
    """Drop connections inherited from a parent process.

    THE FAILURE THIS PREVENTS DOES NOT RAISE
    ----------------------------------------
    RQ forks a child per job and gunicorn forks its workers. A fork copies the
    file descriptor of every pooled socket, so parent and child both believe
    they own that connection and both write to it. Postgres then returns one
    session's result set to the other — another account's rows, delivered
    without an error anywhere.

    close=False is deliberate: the child must abandon the sockets, not close
    them, because closing would tear down the connections the parent is still
    using.
    """
    ENGINE.dispose(close=False)


# POSIX only. Registered here rather than in each worker's hook so that no
# future fork site can forget it.
if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=dispose_pool)


def _add_missing_columns() -> list[str]:
    """Add columns the models declare but the database lacks.

    THE TRAP THIS EXISTS FOR
    ------------------------
    `create_all()` creates missing TABLES. It never adds a missing COLUMN to a
    table that already exists. So adding a field to a model and restarting
    appears to work — until the first query touches it and dies with
    "no such column", by which point the app is live and the database has
    real data in it that you cannot simply delete.

    This handles the additive case only, which covers almost every schema
    change in a young product. Renames, type changes and drops need Alembic;
    `alembic revision --autogenerate` reads this same metadata, so there is no
    rework when you get there.
    """
    from sqlalchemy import inspect, text

    added: list[str] = []
    insp = inspect(ENGINE)
    existing_tables = set(insp.get_table_names())

    with ENGINE.begin() as conn:
        for table_name, table in Base.metadata.tables.items():
            if table_name not in existing_tables:
                continue
            have = {c["name"] for c in insp.get_columns(table_name)}
            for col in table.columns:
                if col.name in have:
                    continue
                # A NOT NULL column can't be added to a populated table without
                # a default, so only nullable additions are safe here.
                if not col.nullable and col.default is None and col.server_default is None:
                    continue
                col_type = col.type.compile(ENGINE.dialect)
                conn.execute(text(
                    f'ALTER TABLE "{table_name}" ADD COLUMN "{col.name}" {col_type}'))
                added.append(f"{table_name}.{col.name}")
    return added


def init_db() -> list[str]:
    """Create missing tables, then add missing columns. Returns table names.

    CONCURRENT STARTUP
    ------------------
    `create_all(checkfirst=True)` reads the table list, then issues CREATE for
    whatever is missing. Those are two steps, and between them another process
    can create the same table. With one process that never happens; with an app
    and several RQ workers starting together on boot it happens immediately.

    Measured with six processes starting at once against SQLite: five crashed
    with "table users already exists" and one survived. Postgres survived all
    six, because its DDL is transactional — but the race is real on both and
    only shows up in the deployment you were about to move to.

    So: serialise with a file lock, and treat "already exists" as success
    rather than an error. Losing a creation race means someone else did the
    work, which is the outcome we wanted.
    """
    # On Postgres, take a lock the whole cluster can see. The file lock below
    # only serialises processes that share a filesystem — the moment the web
    # app and the workers run in separate containers it protects nothing, which
    # is precisely the deployment it was written for.
    if not database_url().startswith("sqlite"):
        from sqlalchemy import text as _text
        with ENGINE.begin() as conn:
            conn.execute(_text("SELECT pg_advisory_lock(198472013)"))
            try:
                try:
                    Base.metadata.create_all(ENGINE)
                except (OperationalError, ProgrammingError, IntegrityError) as exc:
                    if "already exists" not in str(exc).lower():
                        raise
                added = _add_missing_columns()
            finally:
                conn.execute(_text("SELECT pg_advisory_unlock(198472013)"))
        if added:
            print(f"  schema updated: added {', '.join(added)}")
        return sorted(Base.metadata.tables)

    lock_path = Path(tempfile.gettempdir()) / "rejectionintel-initdb.lock"
    lock = open(lock_path, "w")
    try:
        try:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX)      # POSIX
        except ImportError:                        # Windows
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)

        try:
            Base.metadata.create_all(ENGINE)
        except (OperationalError, ProgrammingError, IntegrityError) as exc:
            # Another process won the race. Anything else is a real failure.
            #
            # SQLite raises OperationalError; Postgres raises ProgrammingError
            # (DuplicateTable), and a concurrent index build raises
            # IntegrityError. Catching only the SQLite shape means this guard
            # silently stops working on the database it exists for.
            if "already exists" not in str(exc).lower():
                raise
        added = _add_missing_columns()
    finally:
        try:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_UN)
        except Exception:  # noqa: BLE001
            pass
        lock.close()
    if added:
        print(f"  schema updated: added {', '.join(added)}")
    return sorted(Base.metadata.tables)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transaction boundary. Commits on success, rolls back on any exception.

    A half-written analysis is worse than no analysis: it would be read back
    as complete on the next run and silently skew the statistics.
    """
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def db_info() -> dict:
    url = database_url()
    kind = "sqlite" if url.startswith("sqlite") else "postgres"
    path = url.replace("sqlite:///", "") if kind == "sqlite" else "(remote)"
    size = None
    if kind == "sqlite" and Path(path).exists():
        size = round(Path(path).stat().st_size / 1024, 1)
    return {"backend": kind, "path": path, "size_kb": size}
