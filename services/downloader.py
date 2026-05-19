from __future__ import annotations
import asyncio
import os
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import humanize
import httpx
import yt_dlp

from config import settings
from services import job_manager, accelerator
from services.fmt import fmt_eta
from services.security import sanitize_filename

_COOKIES_FILE = Path(__file__).resolve().parent.parent / "data" / "cookies.txt"

_aria2_client = None


def _get_aria2():
    global _aria2_client
    if _aria2_client is not None:
        return _aria2_client
    try:
        import aria2p
        _aria2_client = aria2p.API(
            aria2p.Client(
                host=settings.ARIA2_HOST,
                port=settings.ARIA2_PORT,
                secret=settings.ARIA2_RPC_SECRET,
            )
        )
        _aria2_client.get_stats()  # probe — raises if not available
        return _aria2_client
    except Exception:
        _aria2_client = None
        return None


_INTERNAL_PREFIXES = ("torrent://", "__torrent_upload__")


def is_torrent(url: str) -> bool:
    if url.startswith("magnet:"):
        return True
    if any(url.startswith(p) for p in _INTERNAL_PREFIXES):
        return True  # uploaded via UI — bytes already in _uploaded_torrent_bytes registry
    parsed = urlparse(url)
    if parsed.path.lower().endswith(".torrent"):
        return True
    return False


def is_mega_url(url: str) -> bool:
    host = urlparse(url).netloc.lower().lstrip("www.")
    return host in ("mega.nz", "mega.co.nz")


def is_mediafire_url(url: str) -> bool:
    host = urlparse(url).netloc.lower().lstrip("www.")
    return host == "mediafire.com"


def is_media_url(url: str) -> bool:
    """Return True if yt-dlp has a dedicated (non-generic) extractor for this URL."""
    return any(
        ie.suitable(url)
        for ie in yt_dlp.extractor.gen_extractors()
        if ie.IE_NAME != "generic"
    )


async def dispatch(
    job_id: str,
    url: str,
    max_conn: int = 4,
    torrent_indices: Optional[list[int]] = None,
    format_id: Optional[str] = None,
    ttl_hours: Optional[int] = None,
) -> None:
    """Entry point — routes URL to the right backend."""
    from services import db as _db
    # Skip persisting upload sentinels — bytes won't survive a restart anyway
    if not any(url.startswith(p) for p in _INTERNAL_PREFIXES):
        await _db.save_pending(job_id, url, max_conn)
    try:
        if is_torrent(url):
            # Internal upload sentinels skip network validation — bytes are already local
            from services import torrent_service
            await torrent_service.start(job_id, url, torrent_indices, ttl_hours)
        elif is_mega_url(url):
            from services import mega_service
            job_manager.update_job(job_id, status="queued")
            async with mega_service._mega_semaphore:
                await asyncio.to_thread(mega_service.download, job_id, url)
        elif is_mediafire_url(url):
            from services import mediafire_service
            await mediafire_service.download(job_id, url, max_conn)
        elif is_media_url(url):
            await asyncio.to_thread(_ytdlp_download, job_id, url, format_id)
        else:
            aria2 = _get_aria2()
            if aria2:
                size_bytes = await _head_content_length(url)
                await asyncio.to_thread(_aria2_download, job_id, url, max_conn, aria2, size_bytes)
            else:
                await accelerator.accelerate(job_id, url, max_conn)
    except Exception as e:
        job_manager.update_job(job_id, status="error", error=str(e))
    finally:
        await _db.remove_pending(job_id)
        job = job_manager.get_job(job_id)
        if job and job.status in ("done", "error"):
            await _post_download(job_id, ttl_hours)
        job_manager.clear_cancelled(job_id)


_SUSPICIOUS_BASENAMES = frozenset({
    "file", "download", "view", "get", "index", "attachment", "f", "d",
})


def _try_rename(old_name: str, new_name: str) -> None:
    try:
        old = os.path.join(settings.DOWNLOAD_DIR, old_name)
        new = os.path.join(settings.DOWNLOAD_DIR, new_name)
        if os.path.exists(old) and not os.path.exists(new):
            os.rename(old, new)
    except OSError:
        pass


def _recover_filename(filename: str, d: dict, url: str) -> str:
    """Fix yt-dlp filenames that are URL path segments (e.g. 'file' for MediaFire).

    Priority:
    1. display_id from info_dict — file-hosting extractors set this to the real filename.
    2. URL path segment that looks like a real filename (has a recognised extension).
    3. Append missing extension from info_dict.ext as last resort.
    """
    from urllib.parse import urlparse, unquote
    info = d.get("info_dict") or {}

    # 1. display_id is the true filename for sites like MediaFire
    display_id = (info.get("display_id") or "").strip()
    if display_id and os.path.splitext(display_id)[1]:
        new_name = sanitize_filename(display_id)
        if new_name and new_name != filename:
            _try_rename(filename, new_name)
            return new_name

    # 2. Scan URL path segments for a proper filename (has a recognisable extension)
    for seg in reversed(urlparse(url).path.split("/")):
        seg = unquote(seg)
        base, ext_part = os.path.splitext(seg)
        if ext_part and 1 < len(ext_part) <= 7 and ext_part[1:].isalnum() and base:
            new_name = sanitize_filename(seg)
            if new_name and new_name.lower() not in _SUSPICIOUS_BASENAMES:
                if new_name != filename:
                    _try_rename(filename, new_name)
                return new_name

    # 3. At least add the missing extension
    if not os.path.splitext(filename)[1]:
        ext = (info.get("ext") or "").lower().strip().lstrip(".")
        if ext and ext not in ("unknown", "file"):
            new_name = f"{filename}.{ext}"
            _try_rename(filename, new_name)
            return new_name

    return filename


def _ytdlp_download(job_id: str, url: str, format_id: Optional[str] = None) -> None:
    os.makedirs(settings.DOWNLOAD_DIR, exist_ok=True)
    job_manager.update_job(job_id, status="downloading", job_type="ytdlp")

    def progress_hook(d: dict) -> None:
        job = job_manager.get_job(job_id)
        if not job:
            return
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes", 0)
            pct = (downloaded / total * 100) if total else 0
            speed_raw = d.get("speed") or 0
            mbps = speed_raw / (1024 * 1024)
            eta_s = d.get("eta") or 0
            job.push_speed(mbps)
            job_manager.update_job(
                job_id,
                percent=min(pct, 99.9),
                downloaded_bytes=downloaded,
                size_bytes=total,
                size=humanize.naturalsize(total, binary=True),
                eta=fmt_eta(eta_s),
            )
        elif d["status"] == "finished":
            raw = d.get("filename") or ""
            filename = sanitize_filename(os.path.basename(raw))
            base = os.path.splitext(filename)[0].lower()
            if filename and (not os.path.splitext(filename)[1] or base in _SUSPICIOUS_BASENAMES):
                filename = _recover_filename(filename, d, url)
            job_manager.update_job(
                job_id, percent=100.0, status="done", filename=filename
            )

    ydl_opts = {
        "outtmpl": os.path.join(settings.DOWNLOAD_DIR, "%(title)s.%(ext)s"),
        "progress_hooks": [progress_hook],
        "quiet": True,
        "no_warnings": True,
        "format": format_id if format_id else "bestvideo+bestaudio/bestvideo/best",
        "concurrent_fragment_downloads": 8,
        "writesubtitles": False,
    }
    if _COOKIES_FILE.exists():
        ydl_opts["cookiefile"] = str(_COOKIES_FILE)
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        # Fallback: if the "finished" hook didn't fire (yt-dlp swallowed the exception),
        # find the most recently modified file in the download dir and mark the job done.
        job = job_manager.get_job(job_id)
        if job and job.status == "downloading":
            try:
                entries = [
                    e for e in os.scandir(settings.DOWNLOAD_DIR)
                    if e.is_file() and not e.name.endswith((".part", ".aria2", ".ytdl"))
                ]
                if entries:
                    newest = max(entries, key=lambda e: e.stat().st_mtime)
                    filename = sanitize_filename(newest.name)
                    base = os.path.splitext(filename)[0].lower()
                    if filename and (not os.path.splitext(filename)[1] or base in _SUSPICIOUS_BASENAMES):
                        filename = _recover_filename(filename, {}, url)
                    job_manager.update_job(job_id, percent=100.0, status="done", filename=filename)
                else:
                    job_manager.update_job(job_id, percent=100.0, status="done")
            except Exception:
                job_manager.update_job(job_id, percent=100.0, status="done")
    except Exception as e:
        job_manager.update_job(job_id, status="error", error=str(e))


async def _head_content_length(url: str) -> int:
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=8) as client:
            r = await client.head(url)
            return int(r.headers.get("content-length", 0))
    except Exception:
        return 0


def _build_aria2_options(max_conn: int, size_bytes: int) -> dict:
    """Compute optimal aria2 split settings from known file size."""
    mb = size_bytes / (1024 * 1024) if size_bytes > 0 else 0
    if mb <= 0:
        conns, min_split = max_conn, "5M"
    elif mb < 5:
        conns, min_split = 1, "1M"
    elif mb < 50:
        conns, min_split = min(4, max_conn), "2M"
    elif mb < 500:
        conns, min_split = min(8, max_conn), "5M"
    elif mb < 2048:
        conns, min_split = max_conn, "10M"
    else:
        conns, min_split = max_conn, "20M"
    return {
        "max-connection-per-server": str(conns),
        "split": str(conns),
        "min-split-size": min_split,
        "continue": "true",
        "allow-overwrite": "true",
        "auto-file-renaming": "true",
        "dir": os.path.abspath(settings.DOWNLOAD_DIR),
    }


def _aria2_download(job_id: str, url: str, max_conn: int, aria2, size_bytes: int = 0) -> None:
    os.makedirs(settings.DOWNLOAD_DIR, exist_ok=True)
    options = _build_aria2_options(max_conn, size_bytes)
    conns = int(options["split"])
    job_manager.update_job(job_id, status="downloading", connections=conns)
    gid = aria2.add_uris([url], options=options).gid
    while True:
        time.sleep(0.5)
        if job_manager.is_cancelled(job_id):
            try:
                dl = aria2.get_download(gid)
                file_path = dl.files[0].path if dl.files else None
                aria2.remove([dl])
                if file_path:
                    try:
                        os.remove(file_path)
                    except OSError:
                        pass
                    try:
                        os.remove(file_path + ".aria2")
                    except OSError:
                        pass
            except Exception:
                pass
            break
        try:
            dl = aria2.get_download(gid)
        except Exception:
            break
        if _aria2_tick(job_id, dl):
            break


def _aria2_tick(job_id: str, dl) -> bool:
    """Process one aria2 poll tick. Returns True when the download is terminal."""
    if dl.status == "complete":
        filename = sanitize_filename(os.path.basename(dl.files[0].path if dl.files else ""))
        job_manager.update_job(
            job_id, percent=100.0, status="done", filename=filename,
            size_bytes=dl.total_length,
            size=humanize.naturalsize(dl.total_length, binary=True),
        )
        return True
    if dl.status == "error":
        job_manager.update_job(job_id, status="error", error=dl.error_message)
        return True
    _aria2_progress(job_id, dl)
    return False


def _aria2_progress(job_id: str, dl) -> None:
    total = dl.total_length or 1
    done = dl.completed_length
    spd = dl.download_speed
    eta_s = int((total - done) / spd) if spd > 0 else 0
    job = job_manager.get_job(job_id)
    if job:
        job.push_speed(spd / (1024 * 1024))
    job_manager.update_job(
        job_id,
        percent=min(done / total * 100, 99.9),
        downloaded_bytes=done,
        size_bytes=total,
        size=humanize.naturalsize(total, binary=True),
        connections=dl.connections,
        eta=fmt_eta(eta_s),
    )


async def _post_download(job_id: str, ttl_hours: Optional[int] = None) -> None:
    """SHA-256 hash + optional ClamAV scan after download completes."""
    from datetime import datetime, timezone, timedelta
    from services import db as _db

    job = job_manager.get_job(job_id)
    if not job or job.status != "done" or not job.filename:
        await job_manager.finish_job(job_id)
        return

    effective_ttl = ttl_hours if ttl_hours is not None else settings.FILE_TTL_DEFAULT_HOURS

    filepath = os.path.join(settings.DOWNLOAD_DIR, job.filename)
    # Directories (multi-file torrents) are not hashed
    if not os.path.exists(filepath) or not os.path.isfile(filepath):
        if effective_ttl:
            now = datetime.now(timezone.utc)
            await _db.set_file_expiry(
                job.filename,
                (now + timedelta(hours=effective_ttl)).isoformat(),
                now.isoformat(),
            )
        await job_manager.finish_job(job_id)
        return

    # ClamAV scan
    if settings.CLAM_SOCKET:
        job_manager.update_job(job_id, status="scanning", scan_result="scanning")
        result = await asyncio.to_thread(_clam_scan, filepath)
        job_manager.update_job(job_id, scan_result=result, status="done")

    if effective_ttl:
        now = datetime.now(timezone.utc)
        await _db.set_file_expiry(
            job.filename,
            (now + timedelta(hours=effective_ttl)).isoformat(),
            now.isoformat(),
        )
    await job_manager.finish_job(job_id)



_QUALITY_PRESETS = [
    (4320, "8K (4320p)"),
    (2160, "4K (2160p)"),
    (1440, "2K (1440p)"),
    (1080, "1080p HD"),
    (720,  "720p HD"),
    (480,  "480p"),
    (360,  "360p"),
]


def _best_size_at_height(raw: list, height: int) -> str:
    sizes = [
        int(f.get("filesize") or f.get("filesize_approx") or 0)
        for f in raw if int(f.get("height") or 0) == height
    ]
    b = max(sizes, default=0)
    return humanize.naturalsize(b, binary=True) if b else "?"


def _best_audio_entry(raw: list) -> Optional[dict]:
    candidates = [
        f for f in raw
        if (f.get("vcodec") or "none").lower() == "none"
        and (f.get("acodec") or "none").lower() != "none"
    ]
    return max(candidates, key=lambda f: float(f.get("abr") or 0), default=None)


def _fetch_raw_formats(url: str) -> list:
    opts: dict = {"quiet": True, "no_warnings": True}
    if _COOKIES_FILE.exists():
        opts["cookiefile"] = str(_COOKIES_FILE)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False) or {}
    return info.get("formats") or []


def _collect_video_presets(raw: list, heights: set) -> list[dict]:
    result: list[dict] = []
    seen_best: set[int] = set()
    for threshold, label in _QUALITY_PRESETS:
        avail = [h for h in heights if h <= threshold]
        if not avail:
            continue
        best_h = max(avail)
        if best_h in seen_best:
            continue
        seen_best.add(best_h)
        result.append({
            "format_id": f"bestvideo[height<={threshold}]+bestaudio/best[height<={threshold}]/best",
            "label": label, "height": threshold, "ext": "mp4",
            "has_video": True, "has_audio": True,
            "size_str": _best_size_at_height(raw, best_h),
        })
    return result


def _make_audio_entry(raw: list) -> Optional[dict]:
    fa = _best_audio_entry(raw)
    if not fa:
        return None
    abr = float(fa.get("abr") or 0)
    ext = (fa.get("ext") or "m4a").upper()
    size = int(fa.get("filesize") or fa.get("filesize_approx") or 0)
    label = f"Audio only · {int(abr)}kbps {ext}" if abr else "Audio only"
    return {
        "format_id": "bestaudio/best", "label": label,
        "height": 0, "ext": "m4a",
        "has_video": False, "has_audio": True,
        "size_str": humanize.naturalsize(size, binary=True) if size else "?",
    }


def _has_audio_only_stream(raw: list) -> bool:
    return any(
        (f.get("vcodec") or "none").lower() == "none"
        and (f.get("acodec") or "none").lower() != "none"
        for f in raw
    )


def extract_formats(url: str) -> list[dict]:
    """Return quality-tier presets filtered to what the video actually offers."""
    raw = _fetch_raw_formats(url)
    heights: set[int] = {int(f.get("height") or 0) for f in raw if f.get("height")}
    result = _collect_video_presets(raw, heights)
    if _has_audio_only_stream(raw):
        entry = _make_audio_entry(raw)
        if entry:
            result.append(entry)
    return result


def _clam_scan(path: str) -> str:
    try:
        import clamd
        if settings.CLAM_SOCKET and settings.CLAM_SOCKET.startswith("/"):
            cd = clamd.ClamdUnixSocket(settings.CLAM_SOCKET)
        else:
            host, _, port = (settings.CLAM_SOCKET or "localhost:3310").partition(":")
            cd = clamd.ClamdNetworkSocket(host, int(port or 3310))
        result = cd.scan(path)
        status, virus = result.get(path, ("OK", None))
        return "clean" if status == "OK" else f"infected:{virus}"
    except Exception as e:
        return f"scan_error:{e}"
