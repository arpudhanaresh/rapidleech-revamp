from __future__ import annotations
import asyncio
import mimetypes
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path

mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("font/woff", ".woff")

from config import settings
from middleware.rate_limiter import setup_rate_limiter
from middleware.security_headers import add_security_headers
from middleware.abuse_detector import AbuseDetectorMiddleware
from services import db, disk_monitor, cleanup
from routers import fetch, files, torrent, stats, health, events, pages, ytdlp

from apscheduler.schedulers.asyncio import AsyncIOScheduler

scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(settings.DOWNLOAD_DIR, exist_ok=True)
    os.makedirs("data", exist_ok=True)
    await db.init_db()
    await _recover_interrupted()
    cleanup.schedule_cleanup(scheduler)
    scheduler.start()
    _disk_poll_task = asyncio.create_task(disk_monitor.poll())
    await db.insert_log("info", "RapidLeech-Py started")
    yield
    scheduler.shutdown(wait=False)
    await db.close_db()


async def _recover_interrupted() -> None:
    import re
    from services import job_manager
    from services.downloader import dispatch

    # ── Wipe orphaned .partN chunk files from crashed HTTP downloads ──────────
    try:
        for entry in os.scandir(settings.DOWNLOAD_DIR):
            if entry.is_file() and re.search(r'\.part\d+$', entry.name):
                os.remove(entry.path)
    except FileNotFoundError:
        pass

    # ── Re-queue downloads that were in-flight before the restart ────────────
    pending = await db.get_all_pending()
    if not pending:
        return
    await db.insert_log("warn", f"Recovering {len(pending)} interrupted download(s)")
    _recovery_tasks = []
    for p in pending:
        job_manager.create_job(p["job_id"], p["url"])
        job_manager.update_job(p["job_id"], status="queued")
        _recovery_tasks.append(asyncio.create_task(dispatch(p["job_id"], p["url"], p["max_conn"])))


app = FastAPI(title="RapidLeech-Py", docs_url="/api/docs", lifespan=lifespan)

# ── Static files ─────────────────────────────────────────────────────────────
_STATIC = Path(__file__).resolve().parent / "static"
if _STATIC.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")

# ── API routers ───────────────────────────────────────────────────────────────
for router in [fetch.router, files.router, torrent.router, stats.router,
               health.router, events.router, ytdlp.router]:
    app.include_router(router, prefix="/api")

# ── Page routers (HTML) ───────────────────────────────────────────────────────
app.include_router(pages.router)

# ── Middleware ────────────────────────────────────────────────────────────────
setup_rate_limiter(app)
add_security_headers(app)
app.add_middleware(AbuseDetectorMiddleware)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=False,
    )
