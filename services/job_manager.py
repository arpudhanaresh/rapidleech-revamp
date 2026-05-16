from __future__ import annotations
import asyncio
from datetime import datetime, timezone
from typing import Optional

from models import Job, JobStatus
from services import db

# ── In-memory live store ──────────────────────────────────────────────────────
_live: dict[str, Job] = {}
_lock = asyncio.Lock()


def create_job(job_id: str, url: str, job_type: str = "http", ip: Optional[str] = None) -> Job:
    job = Job(job_id=job_id, url=url, job_type=job_type, ip_origin=ip)  # type: ignore[arg-type]
    _live[job_id] = job
    return job


def get_job(job_id: str) -> Optional[Job]:
    return _live.get(job_id)


def update_job(job_id: str, **kwargs) -> None:
    job = _live.get(job_id)
    if not job:
        return
    for k, v in kwargs.items():
        if hasattr(job, k):
            setattr(job, k, v)


def list_live_jobs() -> list[Job]:
    return list(_live.values())


def remove_live_job(job_id: str) -> None:
    _live.pop(job_id, None)


async def finish_job(job_id: str) -> None:
    """Persist completed/failed job to DB, update aggregate stats, and remove from live store."""
    job = _live.get(job_id)
    if not job:
        return
    job.finished_at = datetime.now(timezone.utc).isoformat()
    await db.insert_job(job)
    await db.increment_stats(job.size_bytes, job.status == "done")
    await db.insert_log(
        "done" if job.status == "done" else "error",
        f"Job {job_id[:8]} {'completed' if job.status == 'done' else 'failed'} — "
        f"{job.filename or job.url} ({job.size})",
    )
    remove_live_job(job_id)


async def cancel_job(job_id: str) -> None:
    job = _live.get(job_id)
    if not job:
        return
    job.status = "error"
    job.error = "Cancelled by user"
    await finish_job(job_id)
