from __future__ import annotations
import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException, UploadFile, File
from pydantic import BaseModel
from typing import Optional

from services import job_manager, downloader
from services import torrent_service

router = APIRouter(tags=["torrent"])


@router.post("/torrent/upload")
async def upload_torrent(file: UploadFile = File(...)):
    data = await file.read()
    try:
        files = torrent_service._parse_torrent_info(data)
    except Exception as e:
        raise HTTPException(400, f"Invalid .torrent file: {e}")

    job_id = str(uuid.uuid4())

    # Store raw bytes in-memory registry — no file:// URL, no temp file, cross-platform
    torrent_service._uploaded_torrent_bytes[job_id] = data

    job = job_manager.create_job(job_id, "torrent://uploaded", job_type="torrent")
    job.files = files
    return {
        "job_id": job_id,
        "files": [{"index": f.index, "path": f.path, "size_mb": f.size_mb} for f in files],
    }


class StartTorrentRequest(BaseModel):
    file_indices: Optional[list[int]] = None


@router.post("/torrent/{job_id}/start")
async def start_torrent(job_id: str, body: StartTorrentRequest, background: BackgroundTasks):
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    background.add_task(
        downloader.dispatch,
        job_id,
        job.url,
        16,
        body.file_indices,
    )
    return {"ok": True}


@router.get("/torrent/{job_id}/files")
async def torrent_files(job_id: str):
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return [
        {"index": f.index, "path": f.path, "size_mb": f.size_mb, "percent": f.percent}
        for f in job.files
    ]


@router.get("/torrent/{job_id}/peers")
async def torrent_peers(job_id: str):
    return torrent_service.get_peers(job_id)
