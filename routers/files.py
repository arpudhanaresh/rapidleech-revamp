from __future__ import annotations
import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Annotated

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from config import settings
from services import file_service, db
from services.security import sanitize_filename

_COOKIES_FILE = Path(__file__).resolve().parent.parent / "data" / "cookies.txt"
_MIME_ZIP = "application/zip"
_404 = {404: {"description": "Not found"}}
_MSG_NOT_FOUND = "File not found"

router = APIRouter(tags=["files"])


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
    files = file_service.list_files()
    expiries = await db.get_all_expiries()
    for f in files:
        f.expires_at = expiries.get(f.filename)
    return files


@router.get("/files/download/{filename:path}", responses={404: {"description": _MSG_NOT_FOUND}})
async def download_file(filename: str, request: Request):
    safe = sanitize_filename(filename)
    path = file_service.get_filepath(safe)
    if not path:
        raise HTTPException(404, _MSG_NOT_FOUND)
    if os.path.isdir(path):
        return StreamingResponse(
            file_service.zip_dir_stream(path),
            media_type=_MIME_ZIP,
            headers={"Content-Disposition": f'attachment; filename="{safe}.zip"'},
        )
    size = os.path.getsize(path)
    served = _served_bytes(request.headers.get("range"), size)
    await db.increment_uploaded(served)
    return FileResponse(
        path,
        filename=safe,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{safe}"'},
    )


@router.delete("/files/{filename:path}", responses={404: {"description": _MSG_NOT_FOUND}})
async def delete_file(filename: str):
    safe = sanitize_filename(filename)
    ok = file_service.delete_file(safe)
    if not ok:
        raise HTTPException(404, _MSG_NOT_FOUND)
    await db.delete_file_expiry(safe)
    return {"message": f"{safe} deleted"}


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


# ── YouTube cookies ───────────────────────────────────────────────────────────

@router.get("/cookies/status")
async def cookies_status():
    return {"exists": _COOKIES_FILE.exists()}


@router.post("/cookies/upload", responses={400: {"description": "Empty file"}})
async def upload_cookies(file: Annotated[UploadFile, File()]):
    content = await file.read()
    if not content.strip():
        raise HTTPException(400, "Empty file")
    _COOKIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    _COOKIES_FILE.write_bytes(content)
    return {"ok": True}


@router.delete("/cookies")
async def delete_cookies():
    if _COOKIES_FILE.exists():
        _COOKIES_FILE.unlink()
    return {"ok": True}
