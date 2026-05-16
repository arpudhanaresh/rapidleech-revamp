from __future__ import annotations
import asyncio
import io
import os
import tempfile
import threading
import uuid as _uuid
import zipfile
from datetime import datetime, timezone
from typing import AsyncIterator, Optional

import humanize

from config import settings
from models import FileItem

_ZIP_TMP_DIR = ".ziptmp"
_zip_jobs: dict[str, dict] = {}


def _build_active_set() -> set[str]:
    from services import job_manager
    active: set[str] = set()
    for j in job_manager.list_live_jobs():
        if j.status in ("done", "error"):
            continue
        if j.filename:
            active.add(j.filename)
        # Torrent jobs: actual files on disk use paths from the torrent info,
        # which may differ from j.filename (e.g. TorrentName vs TorrentName.mkv).
        # Add the top-level component of every tracked file path.
        for f in (j.files or []):
            top = f.path.replace("\\", "/").split("/")[0]
            active.add(top)
    return active


def _dir_size(path: str) -> int:
    total = 0
    for dirpath, _dirs, filenames in os.walk(path):
        for fn in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, fn))
            except OSError:
                pass
    return total


def _should_skip(entry, active: set[str]) -> bool:
    import re
    if entry.name == _ZIP_TMP_DIR:
        return True
    if re.search(r'\.part\d+$', entry.name):
        return True
    if entry.name.endswith(".aria2"):
        return True
    if entry.name in active:
        return True
    return any(entry.name.startswith(a) for a in active)


def _entry_to_item(entry) -> Optional[FileItem]:
    if entry.is_dir():
        size = _dir_size(entry.path)
        stat = entry.stat()
        return FileItem(
            filename=entry.name,
            size_mb=round(size / (1024 * 1024), 2),
            size_bytes=size,
            created_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            is_dir=True,
        )
    if not entry.is_file():
        return None
    if os.path.exists(os.path.join(settings.DOWNLOAD_DIR, entry.name + ".aria2")):
        return None
    stat = entry.stat()
    return FileItem(
        filename=entry.name,
        size_mb=round(stat.st_size / (1024 * 1024), 2),
        size_bytes=stat.st_size,
        created_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
    )


def list_files() -> list[FileItem]:
    active = _build_active_set()
    items: list[FileItem] = []
    try:
        for entry in os.scandir(settings.DOWNLOAD_DIR):
            if _should_skip(entry, active):
                continue
            item = _entry_to_item(entry)
            if item:
                items.append(item)
    except FileNotFoundError:
        pass
    return sorted(items, key=lambda f: f.created_at, reverse=True)


def delete_file(filename: str) -> bool:
    import shutil
    from services.security import sanitize_filename
    safe = sanitize_filename(filename)
    path = os.path.join(settings.DOWNLOAD_DIR, safe)
    if os.path.isdir(path):
        shutil.rmtree(path)
        return True
    if os.path.isfile(path):
        os.remove(path)
        return True
    return False


def get_filepath(filename: str) -> Optional[str]:
    from services.security import sanitize_filename
    safe = sanitize_filename(filename)
    path = os.path.join(settings.DOWNLOAD_DIR, safe)
    return path if os.path.exists(path) else None


def get_dir_filepath(dirname: str, filepath: str) -> Optional[str]:
    """Securely resolve a file inside a downloaded directory (path-traversal safe)."""
    from services.security import sanitize_filename
    safe_dir = sanitize_filename(dirname)
    dir_root = os.path.realpath(os.path.join(settings.DOWNLOAD_DIR, safe_dir))
    # Resolve candidate and ensure it stays inside dir_root
    candidate = os.path.realpath(os.path.join(dir_root, filepath))
    if not candidate.startswith(dir_root + os.sep):
        return None
    return candidate if os.path.isfile(candidate) else None


async def zip_stream(filenames: list[str]) -> AsyncIterator[bytes]:
    """Yields ZIP bytes on-the-fly for a list of filenames."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in filenames:
            path = get_filepath(name)
            if path and os.path.isfile(path):
                zf.write(path, arcname=name)
    buf.seek(0)
    while chunk := buf.read(65536):
        yield chunk


async def _stream_file(path: str) -> AsyncIterator[bytes]:
    """Yield file chunks from a background thread without blocking the event loop."""
    import queue as _q
    q: _q.SimpleQueue = _q.SimpleQueue()

    def _reader() -> None:
        with open(path, "rb") as f:
            while chunk := f.read(65536):
                q.put(chunk)
        q.put(None)

    threading.Thread(target=_reader, daemon=True).start()
    loop = asyncio.get_event_loop()
    while True:
        chunk = await loop.run_in_executor(None, q.get)
        if chunk is None:
            break
        yield chunk


async def zip_dir_stream(dir_path: str) -> AsyncIterator[bytes]:
    """Create a ZIP of a directory in a temp file (non-blocking), then stream it."""
    fd, tmp = tempfile.mkstemp(suffix=".zip")
    os.close(fd)

    def _make() -> None:
        base = os.path.basename(dir_path)
        with zipfile.ZipFile(tmp, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            for dirpath, dirs, filenames in os.walk(dir_path):
                dirs.sort()
                for fn in sorted(filenames):
                    full = os.path.join(dirpath, fn)
                    arcname = os.path.join(base, os.path.relpath(full, dir_path))
                    zf.write(full, arcname=arcname)

    await asyncio.to_thread(_make)
    try:
        async for chunk in _stream_file(tmp):
            yield chunk
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


# ── Folder browse & ZIP jobs ──────────────────────────────────────────────────

def list_dir_contents(dirname: str) -> Optional[dict]:
    """Return file tree + sizes for a downloaded torrent directory."""
    from services.security import sanitize_filename
    safe = sanitize_filename(dirname)
    dir_path = os.path.join(settings.DOWNLOAD_DIR, safe)
    if not os.path.isdir(dir_path):
        return None
    files = []
    total_bytes = 0
    for dirpath, dirs, filenames in os.walk(dir_path):
        dirs.sort()
        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            try:
                size = os.path.getsize(full)
            except OSError:
                size = 0
            rel = os.path.relpath(full, dir_path).replace("\\", "/")
            total_bytes += size
            files.append({
                "path": rel,
                "size_bytes": size,
                "size": humanize.naturalsize(size, binary=True),
            })
    return {
        "name": safe,
        "files": files,
        "total_files": len(files),
        "total_size_bytes": total_bytes,
        "total_size": humanize.naturalsize(total_bytes, binary=True),
    }


def _zip_worker(job_id: str, dir_path: str, out_path: str) -> None:
    job = _zip_jobs[job_id]
    try:
        all_files: list[str] = []
        for dirpath, dirs, filenames in os.walk(dir_path):
            dirs.sort()
            for fn in sorted(filenames):
                all_files.append(os.path.join(dirpath, fn))
        job["total_files"] = len(all_files)
        base = os.path.basename(dir_path)
        with zipfile.ZipFile(out_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            for i, full in enumerate(all_files):
                arcname = os.path.join(base, os.path.relpath(full, dir_path))
                zf.write(full, arcname=arcname)
                job["files_done"] = i + 1
        job["status"] = "ready"
        job["zip_size_bytes"] = os.path.getsize(out_path)
    except Exception as exc:
        job["status"] = "error"
        job["error"] = str(exc)
        try:
            os.unlink(out_path)
        except OSError:
            pass


def start_zip_job(dirname: str) -> Optional[dict]:
    from services.security import sanitize_filename
    safe = sanitize_filename(dirname)
    dir_path = os.path.join(settings.DOWNLOAD_DIR, safe)
    if not os.path.isdir(dir_path):
        return None
    tmp_dir = os.path.join(settings.DOWNLOAD_DIR, _ZIP_TMP_DIR)
    os.makedirs(tmp_dir, exist_ok=True)
    fd, out_path = tempfile.mkstemp(suffix=".zip", dir=tmp_dir)
    os.close(fd)
    job_id = _uuid.uuid4().hex
    _zip_jobs[job_id] = {
        "status": "zipping",
        "dirname": safe,
        "out_path": out_path,
        "files_done": 0,
        "total_files": 0,
        "zip_size_bytes": 0,
        "error": None,
        "created_at": datetime.now(timezone.utc).timestamp(),
    }
    threading.Thread(target=_zip_worker, args=(job_id, dir_path, out_path), daemon=True).start()
    return {"job_id": job_id}


def get_zip_job(job_id: str) -> Optional[dict]:
    return _zip_jobs.get(job_id)


def cleanup_zip_job(job_id: str) -> None:
    job = _zip_jobs.pop(job_id, None)
    if job:
        try:
            os.unlink(job["out_path"])
        except OSError:
            pass


def cleanup_stale_zip_jobs(max_age_seconds: int = 7200) -> int:
    """Remove ZIP jobs older than max_age_seconds. Returns number cleaned up."""
    import time
    now = time.time()
    stale = [
        jid for jid, job in _zip_jobs.copy().items()
        if now - job.get("created_at", now) > max_age_seconds
    ]
    for jid in stale:
        cleanup_zip_job(jid)
    return len(stale)
