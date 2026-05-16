from __future__ import annotations
import os
from datetime import datetime, timedelta, timezone

from config import settings
from services import db


async def run_cleanup() -> None:
    if settings.FILE_TTL_HOURS == 0:
        return
    threshold = datetime.now(timezone.utc) - timedelta(hours=settings.FILE_TTL_HOURS)
    deleted = 0
    try:
        for entry in os.scandir(settings.DOWNLOAD_DIR):
            if not entry.is_file():
                continue
            mtime = datetime.fromtimestamp(entry.stat().st_mtime, tz=timezone.utc)
            if mtime < threshold:
                try:
                    os.remove(entry.path)
                    deleted += 1
                    await db.insert_log("info", f"Auto-deleted {entry.name} (older than {settings.FILE_TTL_HOURS}h)")
                except OSError as e:
                    await db.insert_log("warn", f"Could not delete {entry.name}: {e}")
    except FileNotFoundError:
        pass
    if deleted:
        await db.insert_log("info", f"Cleanup complete — {deleted} file(s) removed")


def schedule_cleanup(scheduler) -> None:
    scheduler.add_job(
        run_cleanup,
        trigger="interval",
        minutes=5,
        id="auto_cleanup",
        replace_existing=True,
    )
