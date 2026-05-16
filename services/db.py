from __future__ import annotations
import os
from datetime import date
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from config import settings

# Resolve absolute path regardless of cwd — works on both Windows and Linux
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_DEFAULT_SQLITE = f"sqlite+aiosqlite:///{_DATA_DIR / 'rapidleech.db'}".replace("\\", "/")
DATABASE_URL = settings.DATABASE_URL or _DEFAULT_SQLITE

# SQLite needs check_same_thread=False passed via connect_args
_connect_args: dict = {}
if DATABASE_URL.startswith("sqlite"):
    _connect_args = {"check_same_thread": False}

engine = create_async_engine(DATABASE_URL, connect_args=_connect_args, echo=False)
AsyncSessionLocal: sessionmaker = sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)

# ── DDL ──────────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id       TEXT PRIMARY KEY,
    url          TEXT NOT NULL,
    job_type     TEXT NOT NULL DEFAULT 'http',
    status       TEXT NOT NULL,
    filename     TEXT,
    size_bytes   INTEGER DEFAULT 0,
    scan_result  TEXT,
    sha256       TEXT,
    error        TEXT,
    ip_origin    TEXT,
    created_at   TEXT NOT NULL,
    finished_at  TEXT
);

CREATE TABLE IF NOT EXISTS stats (
    id                      INTEGER PRIMARY KEY,
    total_downloaded_bytes  INTEGER DEFAULT 0,
    total_uploaded_bytes    INTEGER DEFAULT 0,
    total_jobs_completed    INTEGER DEFAULT 0,
    total_jobs_failed       INTEGER DEFAULT 0,
    last_reset_at           TEXT
);

CREATE TABLE IF NOT EXISTS pending_downloads (
    job_id      TEXT PRIMARY KEY,
    url         TEXT NOT NULL,
    max_conn    INTEGER DEFAULT 16,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS activity_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    level     TEXT NOT NULL,
    message   TEXT NOT NULL
);
"""

# PostgreSQL / MySQL don't support AUTOINCREMENT keyword
_DDL_PG = _DDL.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
_DDL_MYSQL = _DDL.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "INT AUTO_INCREMENT PRIMARY KEY")


def _pick_ddl() -> str:
    if DATABASE_URL.startswith("postgresql"):
        return _DDL_PG
    if DATABASE_URL.startswith("mysql"):
        return _DDL_MYSQL
    return _DDL


async def _exec_ddl(conn, ddl: str) -> None:
    for stmt in ddl.strip().split(";"):
        s = stmt.strip()
        if s:
            await conn.execute(text(s))


async def init_db() -> None:
    os.makedirs("data", exist_ok=True)
    async with engine.begin() as conn:
        await _exec_ddl(conn, _pick_ddl())
        try:
            await conn.execute(text(
                "ALTER TABLE stats ADD COLUMN total_uploaded_bytes INTEGER DEFAULT 0"
            ))
        except Exception:
            pass
        await conn.execute(text("INSERT OR IGNORE INTO stats (id) VALUES (1)"))


async def close_db() -> None:
    await engine.dispose()


# ── Session helper ────────────────────────────────────────────────────────────

async def _session() -> AsyncSession:
    return AsyncSessionLocal()


# ── Jobs ──────────────────────────────────────────────────────────────────────

async def insert_job(job) -> None:
    async with AsyncSessionLocal() as s:
        await s.execute(
            text(
                "INSERT OR REPLACE INTO jobs "
                "(job_id, url, job_type, status, filename, size_bytes, "
                " scan_result, sha256, error, ip_origin, created_at, finished_at) "
                "VALUES (:job_id, :url, :job_type, :status, :filename, :size_bytes, "
                "        :scan_result, :sha256, :error, :ip_origin, :created_at, :finished_at)"
            ),
            {
                "job_id": job.job_id, "url": job.url, "job_type": job.job_type,
                "status": job.status, "filename": job.filename,
                "size_bytes": job.size_bytes, "scan_result": job.scan_result,
                "sha256": job.sha256, "error": job.error, "ip_origin": job.ip_origin,
                "created_at": job.created_at, "finished_at": job.finished_at,
            },
        )
        await s.commit()


async def get_job_history(
    status: Optional[str] = None,
    q: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
) -> list[dict]:
    clauses = []
    params: dict[str, Any] = {}
    if status:
        clauses.append("status = :status")
        params["status"] = status
    if q:
        clauses.append("(url LIKE :q OR filename LIKE :q)")
        params["q"] = f"%{q}%"
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params["limit"] = page_size
    params["offset"] = (page - 1) * page_size
    sql = f"SELECT * FROM jobs {where} ORDER BY finished_at DESC LIMIT :limit OFFSET :offset"
    async with AsyncSessionLocal() as s:
        rows = await s.execute(text(sql), params)
        return [dict(r._mapping) for r in rows]


# ── Aggregate stats ───────────────────────────────────────────────────────────

async def increment_stats(size_bytes: int, success: bool) -> None:
    col = "total_jobs_completed" if success else "total_jobs_failed"
    async with AsyncSessionLocal() as s:
        await s.execute(
            text(
                f"UPDATE stats SET total_downloaded_bytes = total_downloaded_bytes + :b, "
                f"{col} = {col} + 1 WHERE id = 1"
            ),
            {"b": size_bytes if success else 0},
        )
        await s.commit()


async def increment_uploaded(size_bytes: int) -> None:
    async with AsyncSessionLocal() as s:
        await s.execute(
            text("UPDATE stats SET total_uploaded_bytes = total_uploaded_bytes + :b WHERE id = 1"),
            {"b": size_bytes},
        )
        await s.commit()


async def get_aggregate_stats() -> dict:
    today = date.today().isoformat()
    async with AsyncSessionLocal() as s:
        row = await s.execute(text("SELECT * FROM stats WHERE id = 1"))
        agg = dict(row.mappings().first() or {})
        count = await s.execute(
            text("SELECT COUNT(*) as c FROM jobs WHERE DATE(finished_at) = :d AND status='done'"),
            {"d": today},
        )
        agg["jobs_today"] = (count.mappings().first() or {}).get("c", 0)
        agg.setdefault("total_uploaded_bytes", 0)
    return agg


# ── Activity log ──────────────────────────────────────────────────────────────

async def insert_log(level: str, message: str) -> None:
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).isoformat()
    async with AsyncSessionLocal() as s:
        await s.execute(
            text("INSERT INTO activity_log (ts, level, message) VALUES (:ts, :level, :msg)"),
            {"ts": ts, "level": level, "msg": message},
        )
        # Trim to latest 1000 rows
        await s.execute(
            text(
                "DELETE FROM activity_log WHERE id NOT IN "
                "(SELECT id FROM activity_log ORDER BY id DESC LIMIT 1000)"
            )
        )
        await s.commit()


async def save_pending(job_id: str, url: str, max_conn: int) -> None:
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).isoformat()
    async with AsyncSessionLocal() as s:
        await s.execute(
            text("INSERT OR REPLACE INTO pending_downloads (job_id, url, max_conn, created_at) "
                 "VALUES (:job_id, :url, :max_conn, :ts)"),
            {"job_id": job_id, "url": url, "max_conn": max_conn, "ts": ts},
        )
        await s.commit()


async def remove_pending(job_id: str) -> None:
    async with AsyncSessionLocal() as s:
        await s.execute(text("DELETE FROM pending_downloads WHERE job_id = :id"), {"id": job_id})
        await s.commit()


async def get_all_pending() -> list[dict]:
    async with AsyncSessionLocal() as s:
        rows = await s.execute(text("SELECT job_id, url, max_conn FROM pending_downloads"))
        return [dict(r._mapping) for r in rows]


async def get_recent_logs(limit: int = 200) -> list[dict]:
    async with AsyncSessionLocal() as s:
        rows = await s.execute(
            text("SELECT ts, level, message FROM activity_log ORDER BY id DESC LIMIT :lim"),
            {"lim": limit},
        )
        return [dict(r._mapping) for r in rows]
