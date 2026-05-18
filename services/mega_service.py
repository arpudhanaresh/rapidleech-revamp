from __future__ import annotations
import asyncio
import os
import re
import tempfile
import threading
import time
from typing import Optional

import humanize

from config import settings
from services import job_manager
from services.fmt import fmt_eta
from services.security import sanitize_filename

_mega_semaphore = asyncio.Semaphore(1)

_RATE_LIMIT_PHRASES = (
    "quota", "bandwidth", "overquota", "over quota",
    "rate limit", "509", "transfer limit",
)


def _is_rate_limit_error(msg: str) -> bool:
    low = msg.lower()
    return any(p in low for p in _RATE_LIMIT_PHRASES)


def is_mega_url(url: str) -> bool:
    from urllib.parse import urlparse
    host = urlparse(url).netloc.lower().lstrip("www.")
    return host in ("mega.nz", "mega.co.nz")


def _extract_handle(url: str) -> Optional[str]:
    m = re.search(r'/file/([^#?/]+)', url)
    if m:
        return m.group(1)
    m = re.search(r'#!([^!]+)', url)
    if m:
        return m.group(1)
    return None


def _is_folder_url(url: str) -> bool:
    return "/folder/" in url or "/#F" in url


def _get_file_size(file_handle: str) -> int:
    """Query Mega's public API for file size (no download)."""
    try:
        import httpx
        r = httpx.post(
            "https://g.api.mega.co.nz/cs",
            params={"id": "0"},
            json=[{"a": "g", "g": 1, "p": file_handle}],
            timeout=10,
        )
        data = r.json()
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return int(data[0].get("s", 0))
    except Exception:
        pass
    return 0


def _find_megapy_tmp(tmp_dir: str, known: set[str]) -> Optional[str]:
    try:
        for entry in os.scandir(tmp_dir):
            if entry.name.startswith("megapy_") and entry.name not in known:
                return entry.path
    except OSError:
        pass
    return None


def _emit_progress(job_id: str, current: int, total_size: int, last_bytes: int, dt: float) -> None:
    speed = (current - last_bytes) / dt
    mbps = speed / (1024 * 1024)
    pct = min(current / total_size * 100, 99.9) if total_size else 0
    eta_s = int((total_size - current) / speed) if speed > 0 and total_size > current else 0
    job = job_manager.get_job(job_id)
    if job:
        job.push_speed(mbps)
    job_manager.update_job(job_id, percent=pct, downloaded_bytes=current, eta=fmt_eta(eta_s))


def _sample_progress(
    job_id: str, tmp_file: str, total_size: int, last_bytes: int, last_time: float
) -> tuple[int, float, Optional[str]]:
    """Read temp file size, emit progress if 1 s elapsed. Returns updated (last_bytes, last_time, tmp_file)."""
    try:
        current = os.path.getsize(tmp_file)
        now = time.time()
        dt = now - last_time
        if dt >= 1.0:
            _emit_progress(job_id, current, total_size, last_bytes, dt)
            return current, now, tmp_file
        return last_bytes, last_time, tmp_file
    except OSError:
        return last_bytes, last_time, None  # temp file renamed — download finishing


def _poll_progress(
    job_id: str,
    done: threading.Event,
    tmp_dir: str,
    known_tmp: set[str],
    total_size: int,
) -> None:
    tmp_file: Optional[str] = None
    last_bytes = 0
    last_time = time.time()

    while not done.is_set():
        done.wait(timeout=1.0)
        if tmp_file is None:
            tmp_file = _find_megapy_tmp(tmp_dir, known_tmp)
        if tmp_file:
            last_bytes, last_time, tmp_file = _sample_progress(
                job_id, tmp_file, total_size, last_bytes, last_time
            )


def _start_download_thread(url: str) -> tuple[list, list, threading.Event]:
    try:
        from mega import Mega as _Mega
    except ImportError:
        raise ImportError("mega.py not installed — run: pip install mega.py")

    result_path: list[Optional[str]] = [None]
    exc: list[Optional[str]] = [None]
    done = threading.Event()

    def _do() -> None:
        try:
            result_path[0] = str(_Mega().download_url(url, dest_path=settings.DOWNLOAD_DIR))
        except Exception as e:
            exc[0] = str(e)
        finally:
            done.set()

    threading.Thread(target=_do, daemon=True).start()
    return result_path, exc, done


def download(job_id: str, url: str) -> None:
    if _is_folder_url(url):
        job_manager.update_job(
            job_id, status="error",
            error="Mega folder downloads are not supported — use a file link",
        )
        return

    os.makedirs(settings.DOWNLOAD_DIR, exist_ok=True)
    job_manager.update_job(job_id, status="downloading", job_type="http")

    handle = _extract_handle(url)
    total_size = _get_file_size(handle) if handle else 0
    if total_size:
        job_manager.update_job(
            job_id,
            size_bytes=total_size,
            size=humanize.naturalsize(total_size, binary=True),
        )

    tmp_dir = tempfile.gettempdir()
    try:
        known_tmp = {e.name for e in os.scandir(tmp_dir) if e.name.startswith("megapy_")}
    except OSError:
        known_tmp = set()

    try:
        result_path, exc, done = _start_download_thread(url)
    except ImportError as e:
        job_manager.update_job(job_id, status="error", error=str(e))
        return

    _poll_progress(job_id, done, tmp_dir, known_tmp, total_size)

    if exc[0]:
        msg = exc[0]
        if _is_rate_limit_error(msg):
            msg = "Mega transfer quota exceeded — wait ~6 hours or use a different account"
        job_manager.update_job(job_id, status="error", error=msg)
        return

    if not result_path[0]:
        job_manager.update_job(job_id, status="error", error="Mega download returned no path")
        return

    try:
        fsize = os.path.getsize(result_path[0])
    except OSError:
        fsize = total_size

    fname = sanitize_filename(os.path.basename(result_path[0]))
    job_manager.update_job(
        job_id,
        status="done",
        percent=100.0,
        filename=fname,
        size_bytes=fsize,
        size=humanize.naturalsize(fsize, binary=True),
        downloaded_bytes=fsize,
    )
