from __future__ import annotations
import asyncio
import logging
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

from config import settings
from services import file_service, db
from services.security import sanitize_filename

_MIME_ZIP = "application/zip"
_404 = {404: {"description": "Not found"}}
_MSG_NOT_FOUND = "File not found"

router = APIRouter(tags=["files"])

# Short-lived stream tokens: token -> (filename, expires_at)
_stream_tokens: dict[str, tuple[str, float]] = {}
_TOKEN_TTL = 300  # seconds


def _served_bytes(range_header: Optional[str], file_size: int) -> int:
    if not range_header:
        return file_size
    try:
        spec = range_header.replace("bytes=", "").split(",")[0].strip()
        start_s, end_s = spec.split("-")
        start = int(start_s) if start_s else 0
        end = int(end_s) if end_s else file_size - 1
        return max(0, end - start + 1)
    except Exception:
        return file_size


@router.get("/files")
async def list_files():
    expiries = await db.get_all_expiries()
    now_iso = datetime.now(timezone.utc).isoformat()
    result = []
    for f in file_service.list_files():
        exp = expiries.get(f.filename)
        if exp and exp < now_iso:
            file_service.delete_file(f.filename)
            await db.delete_file_expiry(f.filename)
            continue
        f.expires_at = exp
        result.append(f)
    return result


@router.get("/files/download/{filename:path}", responses={404: {"description": _MSG_NOT_FOUND}})
async def download_file(filename: str, request: Request):
    name = Path(filename).name  # strip any directory components
    path = file_service.get_filepath(name)
    if not path:
        raise HTTPException(404, _MSG_NOT_FOUND)
    if os.path.isdir(path):
        return StreamingResponse(
            file_service.zip_dir_stream(path),
            media_type=_MIME_ZIP,
            headers={"Content-Disposition": f'attachment; filename="{name}.zip"'},
        )
    size = os.path.getsize(path)
    served = _served_bytes(request.headers.get("range"), size)
    await db.increment_uploaded(served)
    return FileResponse(
        path,
        filename=name,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


async def _do_delete_file(name: str):
    ok, err = file_service.delete_file(name)
    if not ok:
        if err == "not_found":
            raise HTTPException(404, _MSG_NOT_FOUND)
        raise HTTPException(500, f"Delete failed: {err}")
    await db.delete_file_expiry(name)
    return {"message": f"{name} deleted"}


@router.delete("/files/{filename:path}", responses={404: {"description": _MSG_NOT_FOUND}, 500: {"description": "Delete failed"}})
async def delete_file(filename: str):
    return await _do_delete_file(Path(filename).name)


class DeleteRequest(BaseModel):
    filename: str


@router.post("/files/delete", responses={404: {"description": _MSG_NOT_FOUND}, 500: {"description": "Delete failed"}})
async def delete_file_post(body: DeleteRequest):
    """POST fallback for proxies that block DELETE or encode filenames in URLs."""
    return await _do_delete_file(Path(body.filename).name)


class StreamTokenRequest(BaseModel):
    filename: str


@router.post("/files/stream-token", responses={404: {"description": _MSG_NOT_FOUND}})
async def create_stream_token(body: StreamTokenRequest):
    """Issue a short-lived token for streaming a file by name in a clean URL (no encoded chars)."""
    name = Path(body.filename).name
    if not file_service.get_filepath(name):
        raise HTTPException(404, _MSG_NOT_FOUND)
    # Purge expired tokens lazily
    now = time.monotonic()
    expired = [t for t, (_, exp) in _stream_tokens.items() if exp < now]
    for t in expired:
        del _stream_tokens[t]
    token = secrets.token_urlsafe(16)
    _stream_tokens[token] = (name, now + _TOKEN_TTL)
    return {"token": token}


@router.get("/files/stream/{token}", responses={404: {"description": "Token not found or expired"}})
async def stream_by_token(token: str, request: Request):
    """Serve a file by short-lived token — avoids nginx WAF blocking percent-encoded filenames."""
    entry = _stream_tokens.get(token)
    if not entry or entry[1] < time.monotonic():
        _stream_tokens.pop(token, None)
        raise HTTPException(404, "Token not found or expired")
    filename, _ = entry
    path = file_service.get_filepath(filename)
    if not path:
        raise HTTPException(404, _MSG_NOT_FOUND)
    if os.path.isdir(path):
        raise HTTPException(400, "Cannot stream a directory")
    size = os.path.getsize(path)
    served = _served_bytes(request.headers.get("range"), size)
    await db.increment_uploaded(served)
    return FileResponse(
        path,
        filename=filename,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


class ZipRequest(BaseModel):
    filenames: list[str]


class ZipPrepareRequest(BaseModel):
    dirname: str


class ExtendExpiryRequest(BaseModel):
    hours: int


@router.patch("/files/{filename:path}/expiry", responses=_404)
async def extend_expiry(filename: str, body: ExtendExpiryRequest):
    safe = sanitize_filename(filename)
    hours = max(1, min(body.hours, settings.FILE_TTL_MAX_HOURS))
    new_exp = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
    updated = await db.extend_file_expiry(safe, new_exp)
    if not updated:
        # File exists on disk but not yet in expiry table — create the record
        path = file_service.get_filepath(safe)
        if not path:
            raise HTTPException(404, _MSG_NOT_FOUND)
        now = datetime.now(timezone.utc)
        new_exp = (now + timedelta(hours=hours)).isoformat()
        await db.set_file_expiry(safe, new_exp, now.isoformat())
    return {"expires_at": new_exp}


@router.get("/files/browse/{dirname:path}", responses=_404)
async def browse_dir(dirname: str):
    result = await asyncio.to_thread(file_service.list_dir_contents, dirname)
    if result is None:
        raise HTTPException(404, "Directory not found")
    return result


@router.post("/files/zip-prepare", responses=_404)
async def zip_prepare(body: ZipPrepareRequest):
    result = file_service.start_zip_job(body.dirname)
    if result is None:
        raise HTTPException(404, "Directory not found")
    return result


@router.get("/files/zip-status/{job_id}", responses=_404)
async def zip_status(job_id: str):
    job = file_service.get_zip_job(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return {
        "status": job["status"],
        "files_done": job["files_done"],
        "total_files": job["total_files"],
        "zip_size_bytes": job["zip_size_bytes"],
        "error": job.get("error"),
    }


@router.get("/files/dir-file", responses=_404)
async def download_dir_file(dirname: str, path: str, request: Request):
    filepath = file_service.get_dir_filepath(dirname, path)
    if not filepath:
        raise HTTPException(404, _MSG_NOT_FOUND)
    size = os.path.getsize(filepath)
    served = _served_bytes(request.headers.get("range"), size)
    await db.increment_uploaded(served)
    return FileResponse(
        filepath,
        filename=os.path.basename(filepath),
        media_type="application/octet-stream",
    )


@router.get("/files/zip-download/{job_id}", responses=_404)
async def zip_download(job_id: str, background: BackgroundTasks):
    job = file_service.get_zip_job(job_id)
    if job is None or job["status"] != "ready":
        raise HTTPException(404, "ZIP not ready")
    out_path = job["out_path"]
    dirname = job["dirname"]
    background.add_task(file_service.cleanup_zip_job, job_id)
    return FileResponse(
        out_path,
        filename=f"{dirname}.zip",
        media_type=_MIME_ZIP,
        headers={"Content-Disposition": f'attachment; filename="{dirname}.zip"'},
    )


@router.post("/files/zip")
async def zip_files(body: ZipRequest):
    safe_names = [sanitize_filename(n) for n in body.filenames]
    return StreamingResponse(
        file_service.zip_stream(safe_names),
        media_type=_MIME_ZIP,
        headers={"Content-Disposition": 'attachment; filename="rapidleech_files.zip"'},
    )

