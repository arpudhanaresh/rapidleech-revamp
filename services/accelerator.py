from __future__ import annotations
import asyncio
import mimetypes
import os
import re
import time
from typing import Optional
from urllib.parse import unquote

import aiofiles
import httpx
import humanize

from config import settings
from models import ChunkInfo, Job
from services import job_manager
from services.fmt import fmt_eta

_LIMITS = httpx.Limits(max_connections=64, max_keepalive_connections=32)
_start_times: dict[str, float] = {}


async def accelerate(job_id: str, url: str, max_conn: int = 16) -> Optional[str]:
    """
    Download url into DOWNLOAD_DIR using parallel Range requests.
    Returns the final filepath on success, raises on failure.
    """
    os.makedirs(settings.DOWNLOAD_DIR, exist_ok=True)
    job = job_manager.get_job(job_id)
    if not job:
        return None

    async with httpx.AsyncClient(
        limits=_LIMITS,
        follow_redirects=True,
        timeout=httpx.Timeout(30.0, connect=10.0),
        http2=True,
    ) as client:
        # ── HEAD — get size and check Range support ───────────────────────────
        try:
            head = await client.head(url)
            head.raise_for_status()
        except Exception:
            # Fall back to GET with stream if HEAD fails
            return await _single_stream(job_id, url, client)

        content_length = int(head.headers.get("content-length", 0))
        accepts_ranges = head.headers.get("accept-ranges", "").lower() == "bytes"

        filename = _filename_from_headers(head.headers, url)
        filename = _unique_path(filename)
        filepath = os.path.join(settings.DOWNLOAD_DIR, filename)

        job_manager.update_job(
            job_id,
            filename=filename,
            size_bytes=content_length,
            size=humanize.naturalsize(content_length, binary=True),
            status="downloading",
        )

        if not accepts_ranges or content_length == 0 or max_conn == 1:
            return await _single_stream(job_id, url, client, filepath)

        # ── Parallel chunked download ─────────────────────────────────────────
        n = min(max_conn, 16)
        chunk_size = content_length // n
        chunks: list[ChunkInfo] = []
        for i in range(n):
            start = i * chunk_size
            end = (start + chunk_size - 1) if i < n - 1 else content_length - 1
            chunks.append(ChunkInfo(index=i, start=start, end=end))

        job_manager.update_job(job_id, chunks=chunks, connections=n)

        part_paths = [f"{filepath}.part{i}" for i in range(n)]
        start_time = time.monotonic()
        _start_times[job_id] = start_time

        await asyncio.gather(
            *[_chunk_download(job_id, url, chunk, client, part_paths[chunk.index])
              for chunk in chunks],
            return_exceptions=True,
        )

        if job_manager.is_cancelled(job_id):
            for part in part_paths:
                try:
                    os.remove(part)
                except OSError:
                    pass
            return None

        # ── Reassemble ───────────────────────────────────────────────────────
        async with aiofiles.open(filepath, "wb") as out:
            for part in part_paths:
                async with aiofiles.open(part, "rb") as f:
                    await out.write(await f.read())
                os.remove(part)

        elapsed = time.monotonic() - start_time
        _start_times.pop(job_id, None)
        avg_mbps = (content_length / (1024 * 1024)) / max(elapsed, 0.1)
        job_manager.update_job(
            job_id,
            percent=100.0,
            status="done",
            speed=f"{avg_mbps:.1f} MB/s",
        )
        return filepath


async def _chunk_download(
    job_id: str,
    url: str,
    chunk: ChunkInfo,
    client: httpx.AsyncClient,
    part_path: str,
) -> None:
    headers = {"Range": f"bytes={chunk.start}-{chunk.end}"}
    retries = 0
    while retries < 3:
        try:
            async with client.stream("GET", url, headers=headers) as resp:
                resp.raise_for_status()
                async with aiofiles.open(part_path, "wb") as f:
                    async for data in resp.aiter_bytes(chunk_size=65536):
                        if job_manager.is_cancelled(job_id):
                            return
                        await f.write(data)
                        chunk.downloaded += len(data)
                        _update_progress(job_id)
            chunk.done = True
            return
        except Exception:
            retries += 1
            await asyncio.sleep(2 ** retries)
    raise RuntimeError(f"Chunk {chunk.index} failed after 3 retries")


def _update_progress(job_id: str) -> None:
    job = job_manager.get_job(job_id)
    if not job or not job.chunks:
        return
    downloaded = sum(c.downloaded for c in job.chunks)
    total = job.size_bytes
    if total > 0:
        pct = min(downloaded / total * 100, 99.9)
        elapsed = time.monotonic() - _start_times.get(job_id, time.monotonic())
        if elapsed > 0.1 and downloaded > 0:
            mbps = (downloaded / (1024 * 1024)) / elapsed
            eta_s = int((total - downloaded) / (downloaded / elapsed))
            job.push_speed(mbps)
            eta = fmt_eta(eta_s)
        else:
            eta = "N/A"
        job_manager.update_job(job_id, percent=pct, downloaded_bytes=downloaded, eta=eta)


async def _single_stream(
    job_id: str,
    url: str,
    client: httpx.AsyncClient,
    filepath: Optional[str] = None,
) -> Optional[str]:
    job = job_manager.get_job(job_id)
    if not job:
        raise RuntimeError("Job not found")

    if not filepath:
        filename = _unique_path(url.split("/")[-1].split("?")[0] or "download")
        filepath = os.path.join(settings.DOWNLOAD_DIR, filename)
        job_manager.update_job(job_id, filename=os.path.basename(filepath), status="downloading")

    downloaded = 0
    start = time.monotonic()
    async with client.stream("GET", url) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        if total:
            job_manager.update_job(
                job_id,
                size_bytes=total,
                size=humanize.naturalsize(total, binary=True),
            )
        async with aiofiles.open(filepath, "wb") as f:
            async for chunk in resp.aiter_bytes(65536):
                if job_manager.is_cancelled(job_id):
                    break
                await f.write(chunk)
                downloaded += len(chunk)
                elapsed = time.monotonic() - start
                mbps = (downloaded / (1024 * 1024)) / max(elapsed, 0.01)
                pct = (downloaded / total * 100) if total else 0
                job = job_manager.get_job(job_id)
                if job:
                    job.push_speed(mbps)
                    job_manager.update_job(
                        job_id, percent=min(pct, 99.9), downloaded_bytes=downloaded
                    )

    if job_manager.is_cancelled(job_id):
        try:
            os.remove(filepath)
        except OSError:
            pass
        return None

    job_manager.update_job(job_id, percent=100.0, status="done")
    return filepath


def _filename_from_headers(headers: httpx.Headers, url: str) -> str:
    from services.security import sanitize_filename
    name = _parse_content_disposition(headers.get("content-disposition", ""))
    if not name:
        raw = url.split("/")[-1].split("?")[0]
        name = unquote(raw) or "download"
    name = sanitize_filename(name)
    if "." not in os.path.basename(name):
        ct = headers.get("content-type", "").split(";")[0].strip()
        ext = _ext_for_content_type(ct)
        if ext:
            name += ext
    return name


_CT_OVERRIDES = {
    "image/jpeg": ".jpg",
    "image/tiff": ".tif",
    "application/octet-stream": "",
}


def _ext_for_content_type(ct: str) -> str:
    if not ct:
        return ""
    if ct in _CT_OVERRIDES:
        return _CT_OVERRIDES[ct]
    return mimetypes.guess_extension(ct) or ""


def _parse_content_disposition(cd: str) -> str:
    if not cd:
        return ""
    # RFC 5987: filename*=charset'lang'percent-encoded takes priority
    m = re.search(r"filename\*\s*=\s*[\w-]+''([^;\s]+)", cd, re.IGNORECASE)
    if m:
        return unquote(m.group(1))
    # Quoted: filename="foo.pdf"
    m = re.search(r'filename\s*=\s*"([^"]*)"', cd, re.IGNORECASE)
    if m:
        return m.group(1)
    # Unquoted: filename=foo.pdf
    m = re.search(r"filename\s*=\s*([^;\s]+)", cd, re.IGNORECASE)
    if m:
        return m.group(1).strip("'")
    return ""


def _unique_path(filename: str) -> str:
    base, ext = os.path.splitext(filename)
    candidate = filename
    n = 1
    while os.path.exists(os.path.join(settings.DOWNLOAD_DIR, candidate)):
        candidate = f"{base}({n}){ext}"
        n += 1
    return candidate
