from __future__ import annotations
import uuid
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Request
from pydantic import BaseModel

from middleware.rate_limiter import limiter
from middleware.abuse_detector import record_fetch
from services import job_manager, downloader
from services.security import validate_and_resolve, SecurityError

router = APIRouter(tags=["fetch"])


class FetchRequest(BaseModel):
    url: str
    max_connections: int = 4
    torrent_file_indices: Optional[list[int]] = None
    format_id: Optional[str] = None


@router.post("/fetch")
@limiter.limit("5/minute")
async def fetch(request: Request, body: FetchRequest, background: BackgroundTasks):
    ip = request.client.host if request.client else "unknown"
    if not record_fetch(ip):
        return {"error": "Rate limit exceeded"}, 429
    try:
        clean_url = await validate_and_resolve(body.url)
    except SecurityError as e:
        from services.db import insert_log
        await insert_log("security", f"Blocked {ip} → {body.url}: {e}")
        return {"error": str(e)}

    # Reject duplicate — same URL already active
    duplicate = next((j for j in job_manager.list_live_jobs() if j.url == clean_url), None)
    if duplicate:
        return {"error": f"Already downloading this URL (job {duplicate.job_id[:8]})"}

    job_id = str(uuid.uuid4())
    job_manager.create_job(job_id, clean_url, ip=ip)
    background.add_task(
        downloader.dispatch,
        job_id,
        clean_url,
        body.max_connections,
        body.torrent_file_indices,
        body.format_id,
    )
    return {"job_id": job_id}


@router.get("/jobs")
async def get_jobs():
    return [_job_dict(j) for j in job_manager.list_live_jobs()]


@router.get("/jobs/history")
async def get_history(status: Optional[str] = None, q: Optional[str] = None, page: int = 1):
    from services.db import get_job_history
    return await get_job_history(status=status, q=q, page=page)


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    await job_manager.cancel_job(job_id)
    return {"ok": True}


@router.post("/jobs/{job_id}/pause")
async def pause_job(job_id: str):
    job = job_manager.get_job(job_id)
    if job and job.job_type == "torrent":
        from services.torrent_service import pause_torrent
        pause_torrent(job_id)
    else:
        job_manager.update_job(job_id, status="paused")
    return {"ok": True}


@router.post("/jobs/{job_id}/resume")
async def resume_job(job_id: str):
    job = job_manager.get_job(job_id)
    if job and job.job_type == "torrent":
        from services.torrent_service import resume_torrent
        resume_torrent(job_id)
    else:
        job_manager.update_job(job_id, status="downloading")
    return {"ok": True}


def _job_dict(j) -> dict:
    return {
        "job_id": j.job_id,
        "url": j.url,
        "status": j.status,
        "job_type": j.job_type,
        "percent": round(j.percent, 1),
        "speed": j.speed,
        "speed_history": j.speed_history[-30:],
        "eta": j.eta,
        "size": j.size,
        "size_bytes": j.size_bytes,
        "downloaded_bytes": j.downloaded_bytes,
        "connections": j.connections,
        "filename": j.filename,
        "error": j.error,
        "scan_result": j.scan_result,
        "sha256": j.sha256,
        "torrent_name": j.torrent_name,
        "seeders": j.seeders,
        "peers": j.peers,
        "leechers": j.leechers,
        "ratio": j.ratio,
        "upload_speed": j.upload_speed,
        "upload_speed_mbps": j.upload_speed_mbps,
        "chunks": [{"index": c.index, "downloaded": c.downloaded, "end": c.end - c.start, "done": c.done} for c in j.chunks],
        "files": [{"index": f.index, "path": f.path, "size_mb": f.size_mb, "percent": f.percent} for f in j.files],
        "created_at": j.created_at,
    }
