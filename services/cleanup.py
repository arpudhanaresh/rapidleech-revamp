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


def _should_delete(
    entry,
    active: set[str],
    now: datetime,
    expiries: dict[str, str],
    fallback_threshold: datetime,
) -> bool:
    if entry.name in active:
        return False
    if entry.name.endswith(".aria2") or entry.name == ".ziptmp":
        return False
    if entry.name in expiries:
        try:
            exp = datetime.fromisoformat(expiries[entry.name])
            return now > exp
        except ValueError:
            pass
    # Fallback for files that predate the expiry table: use mtime
    mtime = datetime.fromtimestamp(entry.stat().st_mtime, tz=timezone.utc)
    return mtime < fallback_threshold


def _delete_entry(entry) -> None:
    if entry.is_dir():
        shutil.rmtree(entry.path)
    else:
        os.remove(entry.path)


async def run_cleanup() -> None:
    if settings.FILE_TTL_DEFAULT_HOURS == 0:
        return

    now = datetime.now(timezone.utc)
    fallback_threshold = now - timedelta(hours=settings.FILE_TTL_DEFAULT_HOURS)
    active = _active_names()
    expiries = await db.get_all_expiries()
    deleted = 0

    try:
        for entry in os.scandir(settings.DOWNLOAD_DIR):
            if not _should_delete(entry, active, now, expiries, fallback_threshold):
                continue
            try:
                _delete_entry(entry)
                await db.delete_file_expiry(entry.name)
                deleted += 1
                await db.insert_log("info", f"Auto-deleted {entry.name} (expired)")
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
