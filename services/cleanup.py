from __future__ import annotations
import os
import shutil
from datetime import datetime, timedelta, timezone

from config import settings
from services import db


def _active_names() -> set[str]:
    """Names of files/dirs currently being downloaded — must not be deleted."""
    from services import job_manager
    names: set[str] = set()
    for j in job_manager.list_live_jobs():
        if j.filename:
            names.add(j.filename)
        for f in (j.files or []):
            names.add(f.path.replace("\\", "/").split("/")[0])
    return names


def _should_delete(entry, active: set[str], threshold: datetime) -> bool:
    if entry.name in active:
        return False
    if entry.name.endswith(".aria2") or entry.name == ".ziptmp":
        return False
    mtime = datetime.fromtimestamp(entry.stat().st_mtime, tz=timezone.utc)
    return mtime < threshold


def _delete_entry(entry) -> None:
    if entry.is_dir():
        shutil.rmtree(entry.path)
    else:
        os.remove(entry.path)


async def run_cleanup() -> None:
    if settings.FILE_TTL_HOURS == 0:
        return

    threshold = datetime.now(timezone.utc) - timedelta(hours=settings.FILE_TTL_HOURS)
    active = _active_names()
    deleted = 0

    try:
        for entry in os.scandir(settings.DOWNLOAD_DIR):
            if not _should_delete(entry, active, threshold):
                continue
            try:
                _delete_entry(entry)
                deleted += 1
                await db.insert_log("info", f"Auto-deleted {entry.name} (older than {settings.FILE_TTL_HOURS}h)")
            except OSError as e:
                await db.insert_log("warn", f"Could not delete {entry.name}: {e}")
    except FileNotFoundError:
        pass

    from services.file_service import cleanup_stale_zip_jobs
    stale = cleanup_stale_zip_jobs(max_age_seconds=7200)
    if stale:
        await db.insert_log("info", f"Evicted {stale} stale ZIP job(s)")

    if deleted:
        await db.insert_log("info", f"Cleanup complete — {deleted} item(s) removed")


def schedule_cleanup(scheduler) -> None:
    scheduler.add_job(
        run_cleanup,
        trigger="interval",
        minutes=5,
        id="auto_cleanup",
        replace_existing=True,
    )
