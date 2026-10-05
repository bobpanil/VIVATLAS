"""The capture queue: a link someone adds is written down before the answer goes back.

Saves from the browser extension, the phone's share sheet and the MCP add_to_library
tool used to go straight into a background task. SQLite lets one writer in at a time,
and those tasks kept the database locked while they waited on the AI, so saves that
arrived close together failed with "database is locked" after the caller had already
been told "processing". Nothing said so. A restart lost whatever was in flight the
same way.

Now a save is a row in capture_jobs first (models.CaptureJob). One worker turns the
rows into cards in order, one at a time, and holds no lock while it waits on a web
page or the AI. A row that fails is tried again later; after MAX_ATTEMPTS it is
marked failed with the reason, and its link and text stay in the table. Whatever was
waiting, or half done, when the server stopped is picked up when it starts again.

If the database stays locked for longer than the caller can wait, enqueue raises
QueueBusy, and the caller is told that nothing was saved, so it can try again. Either
the save is in the table, or the caller hears that it isn't.
"""

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from vivatlas.db import session_scope
from vivatlas.models import CAPTURE_STATUSES, CaptureJob

log = logging.getLogger(__name__)

URL_MAX = 4096
TITLE_MAX = 500
# The extension sends at most 8,000 characters of a page, and an assistant may send a
# transcript or notes with a link. This is plenty to describe a card, and it keeps one
# request from filling the table.
TEXT_MAX = 20_000
MAX_ATTEMPTS = 5
# Seconds before the 2nd, 3rd, 4th and 5th try: long enough for a quota or a busy
# database to clear.
RETRY_DELAYS = (60, 300, 1800, 3600)
IDLE_POLL_SECONDS = 30
# Bookkeeping after a job (done, or try again later) waits this long for the
# database rather than leave the job marked running.
_BOOKKEEPING_PATIENCE = 600.0

BUSY_MESSAGE = "The library is busy right now, so nothing was saved. Try again in a minute."


class QueueBusy(RuntimeError):
    """The database stayed locked for longer than the caller could wait. Nothing was saved."""


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(dt: datetime | None) -> datetime | None:
    """SQLite hands datetimes back without a zone. Ours are all UTC."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _iso(dt: datetime | None) -> str:
    dt = _aware(dt)
    return dt.isoformat() if dt else ""


def is_locked(exc: BaseException) -> bool:
    if not isinstance(exc, OperationalError):
        return False
    text = str(getattr(exc, "orig", exc)).lower()
    return "locked" in text or "busy" in text


async def run_db(fn, *args, patience: float = 30.0):
    """Run a short database step in a worker thread, trying again while SQLite is locked.

    In a thread, because the lock is usually held by a coroutine on this same event
    loop: a scan waiting on the network with a write open. Waiting for the lock on the
    loop itself stops that coroutine from ever reaching its commit, so the wait always
    runs out. From a thread, the loop keeps going, the holder finishes, and the step
    goes through."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + patience
    delay = 0.25
    while True:
        try:
            return await asyncio.to_thread(fn, *args)
        except OperationalError as exc:
            if not is_locked(exc) or loop.time() + delay > deadline:
                raise
            log.info("capture queue: the database is busy, trying again in %.2fs", delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 4.0)


# --- adding ------------------------------------------------------------------


def _insert(fields: dict) -> tuple[int, int]:
    with session_scope() as session:
        job = CaptureJob(**fields)
        session.add(job)
        session.flush()
        ahead = session.scalar(
            select(func.count())
            .select_from(CaptureJob)
            .where(CaptureJob.status.in_(("pending", "running")), CaptureJob.id < job.id)
        )
        return job.id, int(ahead or 0)


async def enqueue(
    *,
    url: str,
    title: str = "",
    text: str = "",
    user_id: int | None,
    shared: bool = False,
    text_kind: str = "page",
    via: str = "",
    patience: float = 30.0,
    comments: list[dict] | None = None,
) -> dict:
    """Write a capture down and wake the worker. Returns the job's id and how many
    are ahead of it. Raises QueueBusy if the database stayed locked for `patience`
    seconds: then nothing was saved, and the caller must say so. `comments` are the
    post's first comments, already cleaned (comments.clean)."""
    from vivatlas.comments import dumps

    fields = {
        "url": (url or "").strip()[:URL_MAX],
        "title": (title or "").strip()[:TITLE_MAX],
        "text": (text or "")[:TEXT_MAX],
        "text_kind": "note" if text_kind == "note" else "page",
        "user_id": user_id,
        "shared": bool(shared),
        "via": (via or "")[:16],
        "comments_json": dumps(comments or []),
        "status": "pending",
    }
    try:
        job_id, ahead = await run_db(_insert, fields, patience=patience)
    except OperationalError as exc:
        if is_locked(exc):
            log.warning(
                "capture queue: the database stayed locked for %.0fs, refused %s",
                patience,
                fields["url"],
            )
            raise QueueBusy(BUSY_MESSAGE) from exc
        raise
    log.info(
        "capture queue: #%d queued via %s, %d ahead: %s", job_id, via or "?", ahead, fields["url"]
    )
    kick()
    return {"job_id": job_id, "queued_ahead": ahead}


# --- the worker ----------------------------------------------------------------

_wake: asyncio.Event | None = None


def kick() -> None:
    """Tell the worker there is something new, so it doesn't wait for its next look."""
    ev = _wake
    if ev is None:
        return
    with contextlib.suppress(RuntimeError):  # an Event left over from a closed loop
        ev.set()


def _as_job(job: CaptureJob) -> dict:
    return {
        "id": job.id,
        "url": job.url or "",
        "title": job.title or "",
        "text": job.text or "",
        "text_kind": job.text_kind or "page",
        "comments_json": job.comments_json or "",
        "user_id": job.user_id,
        "shared": bool(job.shared),
        "via": job.via or "",
        "attempts": job.attempts or 0,
    }


def _claim_next() -> dict | None:
    """The oldest job that is due, marked running. The WHERE on status makes the claim
    safe with a second server on the same database file: only one of them wins."""
    now = _now()
    with session_scope() as session:
        job = session.scalar(
            select(CaptureJob)
            .where(
                CaptureJob.status == "pending",
                or_(CaptureJob.next_try_at.is_(None), CaptureJob.next_try_at <= now),
            )
            .order_by(CaptureJob.id)
            .limit(1)
        )
        if job is None:
            return None
        claimed = _as_job(job)
        won = session.execute(
            update(CaptureJob)
            .where(CaptureJob.id == job.id, CaptureJob.status == "pending")
            .values(
                status="running",
                attempts=CaptureJob.attempts + 1,
                started_at=now,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        ).rowcount
        if not won:
            return None
        claimed["attempts"] += 1
        return claimed


def _seconds_until_due() -> float | None:
    """How long until the next waiting job is due. None if nothing is waiting."""
    with session_scope() as session:
        row = session.execute(
            select(CaptureJob.next_try_at)
            .where(CaptureJob.status == "pending")
            .order_by(CaptureJob.next_try_at.asc().nulls_first())
            .limit(1)
        ).first()
    if row is None:
        return None
    when = _aware(row[0])
    if when is None:
        return 0.0
    return max(0.0, (when - _now()).total_seconds())


def _finish(job_id: int, artifact_id: int | None) -> None:
    with session_scope() as session:
        job = session.get(CaptureJob, job_id)
        if job is None:
            return
        job.status = "done"
        job.artifact_id = artifact_id
        job.finished_at = _now()
        job.next_try_at = None
        job.error = ""


def _fail_or_retry(job_id: int, attempts: int, error: str) -> str:
    with session_scope() as session:
        job = session.get(CaptureJob, job_id)
        if job is None:
            return "gone"
        job.error = (error or "")[:1000]
        if attempts >= MAX_ATTEMPTS:
            job.status = "failed"
            job.finished_at = _now()
            job.next_try_at = None
        else:
            job.status = "pending"
            delay = RETRY_DELAYS[min(attempts, len(RETRY_DELAYS)) - 1]
            job.next_try_at = _now() + timedelta(seconds=delay)
        return job.status


def _put_back(job_id: int) -> None:
    """The server is stopping in the middle of a job: it waits for the next start,
    and this try doesn't count against it."""
    with session_scope() as session:
        session.execute(
            update(CaptureJob)
            .where(CaptureJob.id == job_id, CaptureJob.status == "running")
            .values(status="pending", attempts=CaptureJob.attempts - 1, next_try_at=None)
            .execution_options(synchronize_session=False)
        )


def recover_interrupted() -> int:
    """Jobs a stopped server left running go back in line. One that has already had
    MAX_ATTEMPTS tries is marked failed instead, so a link that brings the server
    down can't do it forever."""
    with session_scope() as session:
        rows = session.scalars(select(CaptureJob).where(CaptureJob.status == "running")).all()
        for job in rows:
            if (job.attempts or 0) >= MAX_ATTEMPTS:
                job.status = "failed"
                job.finished_at = _now()
                job.error = job.error or (
                    "The server stopped while this was being added, too many times."
                )
            else:
                job.status = "pending"
                job.next_try_at = None
        return len(rows)


async def process_next() -> bool:
    """Take the next job that is due and turn it into a card. False when none is due."""
    job = await run_db(_claim_next)
    if job is None:
        return False
    from vivatlas.web import run_capture  # web imports this module

    try:
        artifact_id = await run_capture(job)
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            _put_back(job["id"])
        raise
    except Exception as exc:  # noqa: BLE001 - kept with the job and tried again later
        status = await run_db(
            _fail_or_retry,
            job["id"],
            job["attempts"],
            f"{type(exc).__name__}: {exc}",
            patience=_BOOKKEEPING_PATIENCE,
        )
        log.warning(
            "capture queue: #%d try %d failed (%s): %s: %s",
            job["id"],
            job["attempts"],
            "will try again" if status == "pending" else status,
            job["url"],
            exc,
        )
        return True
    await run_db(_finish, job["id"], artifact_id, patience=_BOOKKEEPING_PATIENCE)
    log.info("capture queue: #%d done, card %s", job["id"], artifact_id)
    return True


async def drain() -> int:
    """Process every job that is due now, one at a time. Returns how many."""
    done = 0
    while await process_next():
        done += 1
    return done


async def worker_loop() -> None:
    """The one worker, started with the server. It takes the jobs in order, and
    between them waits for a kick (a new save) or its next look, whichever is first."""
    global _wake
    wake = _wake = asyncio.Event()
    try:
        try:
            back = await run_db(recover_interrupted, patience=_BOOKKEEPING_PATIENCE)
            if back:
                log.info("capture queue: %d interrupted capture(s) back in line", back)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("capture queue: could not recover interrupted captures")
        while True:
            # Cleared before looking, so a save that arrives during the look still
            # wakes us.
            wake.clear()
            try:
                if await process_next():
                    continue
                wait = await run_db(_seconds_until_due)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("capture queue: worker pass failed")
                wait = IDLE_POLL_SECONDS
            timeout = (
                IDLE_POLL_SECONDS if wait is None else max(0.5, min(wait, IDLE_POLL_SECONDS))
            )
            with contextlib.suppress(asyncio.TimeoutError, TimeoutError):
                await asyncio.wait_for(wake.wait(), timeout=timeout)
    finally:
        if _wake is wake:
            _wake = None


# --- looking ---------------------------------------------------------------------


def summary_for(session: Session, user_id: int, status: str = "", limit: int = 50) -> dict:
    """A user's captures, newest first, with counts by status."""
    counts = dict(
        session.execute(
            select(CaptureJob.status, func.count())
            .where(CaptureJob.user_id == user_id)
            .group_by(CaptureJob.status)
        ).all()
    )
    query = select(CaptureJob).where(CaptureJob.user_id == user_id)
    if status:
        query = query.where(CaptureJob.status == status)
    rows = session.scalars(query.order_by(CaptureJob.id.desc()).limit(limit)).all()
    return {
        "counts": {s: int(counts.get(s, 0)) for s in CAPTURE_STATUSES},
        "items": [
            {
                "job_id": j.id,
                "url": j.url,
                "title": j.title,
                "status": j.status,
                "attempts": j.attempts or 0,
                "card_id": j.artifact_id,
                "error": j.error or "",
                "via": j.via or "",
                "queued_at": _iso(j.created_at),
                "finished_at": _iso(j.finished_at),
                "next_try_at": _iso(j.next_try_at) if j.status == "pending" else "",
            }
            for j in rows
        ],
    }
