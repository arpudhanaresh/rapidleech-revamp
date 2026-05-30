"""Google Drive download support.

Ported from the classic RapidLeech `hosts/download/google_com.php` plugin and
adapted to this codebase's async / job_manager conventions.

Handles three URL kinds:
  * regular files (incl. the large-file "virus scan" confirm flow)
  * folders (enumerated without an API key, then each file streamed)
  * native Google Docs / Sheets / Slides (exported to pdf/xlsx)

Downloads are single-connection streams on purpose: Google rate-limits parallel
Range requests on Drive, and the confirm/cookie flow makes handing a resolved URL
to the parallel `accelerator` unreliable (it carries no cookies and does not check
content-type, so it could silently save an HTML error page). This mirrors gdown.
"""
from __future__ import annotations

import html as _html
import os
import re
import time
from typing import Callable, Optional
from urllib.parse import unquote, urlparse

import aiofiles
import httpx
import humanize

from config import settings
from services import job_manager
from services.fmt import fmt_eta
from services.security import sanitize_filename

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)

_GDRIVE_HOSTS = {"drive.google.com", "docs.google.com", "drive.usercontent.google.com"}
_USERCONTENT = "https://drive.usercontent.google.com/download"

# Native (Docs editors) export: kind -> (url template, extension)
_NATIVE = {
    "document": ("https://docs.google.com/document/d/{id}/export?format=pdf", "pdf"),
    "presentation": ("https://docs.google.com/presentation/d/{id}/export/pdf", "pdf"),
    "spreadsheet": ("https://docs.google.com/spreadsheets/d/{id}/export?format=xlsx", "xlsx"),
}

_ID_RES = [
    re.compile(r"/file/d/([\w-]{10,})"),
    re.compile(r"/(?:document|presentation|spreadsheets?)/d/([\w-]{10,})"),
    re.compile(r"/folders/([\w-]{10,})"),
    re.compile(r"[?&]id=([\w-]{10,})"),
    re.compile(r"/d/([\w-]{10,})"),
]


# ── URL classification ──────────────────────────────────────────────────────

def is_gdrive_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host in _GDRIVE_HOSTS


def _extract_id(url: str) -> Optional[str]:
    for rx in _ID_RES:
        m = rx.search(url)
        if m:
            return m.group(1)
    return None


def _url_kind(url: str) -> str:
    u = url.lower()
    if "/folders/" in u or "folderview" in u:
        return "folder"
    if "/document/" in u:
        return "document"
    if "/presentation/" in u:
        return "presentation"
    if "/spreadsheet" in u:
        return "spreadsheet"
    return "file"


# ── HTTP helpers ────────────────────────────────────────────────────────────

def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(60.0, connect=15.0),
        headers={"User-Agent": _UA},
        http2=True,
    )


async def _send_stream(
    client: httpx.AsyncClient, url: str, params: Optional[dict] = None
) -> httpx.Response:
    """Open a streaming GET without reading the body (so large files aren't buffered)."""
    req = client.build_request("GET", url, params=params)
    return await client.send(req, stream=True)


def _looks_like_html(resp: httpx.Response) -> bool:
    return "text/html" in resp.headers.get("content-type", "").lower()


def _raise_for_html_errors(text: str) -> None:
    """Inspect an HTML response for Google's quota / private-file pages and raise."""
    low = text.lower()
    if (
        "download quota" in low
        or "can't view or download this file" in low
        or "too many users have viewed or downloaded" in low
        or "quota for this file" in low
    ):
        raise RuntimeError(
            "Google Drive download quota exceeded for this file — "
            "wait ~24 hours or use a different file/account."
        )
    if (
        "servicelogin" in low
        or "accounts.google.com/v3/signin" in low
        or "sign in to continue" in low
    ):
        raise RuntimeError(
            "This Google Drive file is private — share it publicly "
            "('Anyone with the link') to download."
        )


def _filename_from_headers(headers: httpx.Headers, fallback: str) -> str:
    cd = headers.get("content-disposition", "")
    m = re.search(r"filename\*\s*=\s*[\w\-]+''([^;\r\n]+)", cd, re.IGNORECASE)
    if m:
        return sanitize_filename(unquote(m.group(1)))
    m = re.search(r'filename\s*=\s*"([^"]+)"', cd, re.IGNORECASE)
    if m:
        return sanitize_filename(m.group(1))
    m = re.search(r"filename\s*=\s*([^;\r\n]+)", cd, re.IGNORECASE)
    if m:
        return sanitize_filename(m.group(1).strip().strip('"'))
    return sanitize_filename(fallback)


def _parse_confirm_form(html_text: str) -> Optional[dict]:
    """Extract the hidden-input params (and action) of the large-file warning form."""
    fm = re.search(
        r'<form[^>]*\bid="download-form"[^>]*>(.*?)</form>',
        html_text, re.IGNORECASE | re.DOTALL,
    )
    if not fm:
        fm = re.search(r"<form[^>]*>(.*?)</form>", html_text, re.IGNORECASE | re.DOTALL)
    if not fm:
        return None

    form_tag, scope = fm.group(0), fm.group(1)
    params: dict = {}

    am = re.search(r'action="([^"]+)"', form_tag, re.IGNORECASE)
    if am:
        action = _html.unescape(am.group(1))
        if action.startswith("/"):
            action = "https://drive.usercontent.google.com" + action
        params["__action__"] = action

    for tag in re.finditer(r"<input\b[^>]*>", scope, re.IGNORECASE):
        t = tag.group(0)
        nm = re.search(r'name="([^"]*)"', t, re.IGNORECASE)
        if not nm:
            continue
        vm = re.search(r'value="([^"]*)"', t, re.IGNORECASE)
        params[nm.group(1)] = _html.unescape(vm.group(1)) if vm else ""

    has_fields = any(k for k in params if k != "__action__")
    return params if has_fields else None


# ── File resolution + streaming ─────────────────────────────────────────────

async def _resolve_file_response(
    client: httpx.AsyncClient, file_id: str
) -> tuple[httpx.Response, str]:
    """Return an open streaming response positioned at the file bytes, plus filename."""
    resp = await _send_stream(client, _USERCONTENT, {"id": file_id, "export": "download"})

    if _looks_like_html(resp):
        text = (await resp.aread()).decode("utf-8", "replace")
        await resp.aclose()
        _raise_for_html_errors(text)

        params = _parse_confirm_form(text) or {
            "id": file_id, "export": "download", "confirm": "t",
        }
        action = params.pop("__action__", _USERCONTENT)
        resp = await _send_stream(client, action, params)

        if _looks_like_html(resp):
            text = (await resp.aread()).decode("utf-8", "replace")
            await resp.aclose()
            _raise_for_html_errors(text)
            raise RuntimeError(
                "Google Drive returned a confirmation page instead of the file — "
                "the link may have expired or the file is restricted."
            )

    filename = _filename_from_headers(resp.headers, file_id)
    return resp, filename


# report(downloaded_bytes, total_bytes, elapsed_seconds)
ProgressCb = Callable[[int, int, float], None]


async def _stream_response(
    job_id: str, resp: httpx.Response, filepath: str, report: ProgressCb
) -> tuple[Optional[int], int]:
    """Stream `resp` to `filepath`. Returns (bytes_written|None-if-cancelled, total)."""
    total = int(resp.headers.get("content-length", 0))
    os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)

    downloaded = 0
    start = time.monotonic()
    last_emit = 0.0
    cancelled = False

    async with aiofiles.open(filepath, "wb") as f:
        async for chunk in resp.aiter_bytes(262144):
            if job_manager.is_cancelled(job_id):
                cancelled = True
                break
            await f.write(chunk)
            downloaded += len(chunk)
            now = time.monotonic()
            if now - last_emit >= 0.5:
                last_emit = now
                report(downloaded, total, now - start)

    if cancelled:
        try:
            os.remove(filepath)
        except OSError:
            pass
        return None, total

    report(downloaded, total, max(time.monotonic() - start, 0.01))
    return downloaded, total


def _single_report(job_id: str) -> ProgressCb:
    def report(downloaded: int, total: int, elapsed: float) -> None:
        mbps = (downloaded / 1048576) / max(elapsed, 0.01)
        job = job_manager.get_job(job_id)
        if job:
            job.push_speed(mbps)
        pct = min(downloaded / total * 100, 99.9) if total else 0
        eta_s = int((total - downloaded) / (downloaded / elapsed)) if downloaded and total > downloaded else 0
        fields = {"percent": pct, "downloaded_bytes": downloaded, "eta": fmt_eta(eta_s)}
        if total:
            fields["size_bytes"] = total
            fields["size"] = humanize.naturalsize(total, binary=True)
        job_manager.update_job(job_id, **fields)
    return report


# ── Path helpers ────────────────────────────────────────────────────────────

def _unique_path(directory: str, name: str) -> str:
    base, ext = os.path.splitext(name)
    candidate = name
    i = 1
    while os.path.exists(os.path.join(directory, candidate)):
        candidate = f"{base}({i}){ext}"
        i += 1
    return candidate


# ── Folder enumeration ──────────────────────────────────────────────────────

def _dedupe(ids: list[str], folder_id: str) -> list[str]:
    out: list[str] = []
    seen = {folder_id}
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


async def _enumerate_folder(client: httpx.AsyncClient, folder_id: str) -> list[str]:
    # Method 1: embedded folder view — most reliable for public folders
    try:
        r = await client.get(f"https://drive.google.com/embeddedfolderview?id={folder_id}#list")
        ids = _dedupe(re.findall(r"/file/d/([\w-]{10,})", r.text), folder_id)
        if ids:
            return ids
    except Exception:
        pass

    # Method 2: regular folder page — scrape IDs out of the embedded JSON
    try:
        r = await client.get(f"https://drive.google.com/drive/folders/{folder_id}")
        cand = [c for c in re.findall(r'"([\w-]{20,})"', r.text) if 20 <= len(c) <= 60]
        ids = _dedupe(cand, folder_id)
        if ids:
            return ids
    except Exception:
        pass

    # Method 3: internal v2beta listing (works for some shared folders, no key)
    try:
        r = await client.get(
            "https://clients6.google.com/drive/v2beta/files",
            params={"q": f"'{folder_id}' in parents", "fields": "items(id)", "maxResults": "1000"},
            headers={"referer": "https://drive.google.com/"},
        )
        ids = _dedupe(re.findall(r'"id"\s*:\s*"([\w-]{10,})"', r.text), folder_id)
        if ids:
            return ids
    except Exception:
        pass

    return []


async def _folder_name(client: httpx.AsyncClient, folder_id: str) -> Optional[str]:
    try:
        r = await client.get(f"https://drive.google.com/drive/folders/{folder_id}")
        m = re.search(r"<title>(.*?)</title>", r.text, re.IGNORECASE | re.DOTALL)
        if m:
            name = re.sub(r"\s*-\s*Google Drive\s*$", "", _html.unescape(m.group(1)).strip())
            if name and name.lower() != "google drive":
                return name
    except Exception:
        pass
    return None


# ── Public entrypoints ──────────────────────────────────────────────────────

async def download(job_id: str, url: str, max_conn: int = 4) -> None:
    file_id = _extract_id(url)
    if not file_id:
        raise RuntimeError("Could not find a Google Drive file/folder ID in the URL.")

    kind = _url_kind(url)
    job_manager.update_job(job_id, status="downloading", job_type="http", connections=1)

    if kind == "folder":
        await _download_folder(job_id, file_id)
    elif kind in _NATIVE:
        await _download_native_doc(job_id, file_id, kind)
    else:
        await _download_single(job_id, file_id)


async def _download_single(job_id: str, file_id: str) -> None:
    os.makedirs(settings.DOWNLOAD_DIR, exist_ok=True)
    async with _client() as client:
        resp, filename = await _resolve_file_response(client, file_id)
        filename = _unique_path(settings.DOWNLOAD_DIR, sanitize_filename(filename or file_id))
        filepath = os.path.join(settings.DOWNLOAD_DIR, filename)
        job_manager.update_job(job_id, filename=filename)
        try:
            written, _ = await _stream_response(job_id, resp, filepath, _single_report(job_id))
        finally:
            await resp.aclose()

    if written is not None and not job_manager.is_cancelled(job_id):
        job_manager.update_job(job_id, status="done", percent=100.0)


async def _download_native_doc(job_id: str, file_id: str, kind: str) -> None:
    os.makedirs(settings.DOWNLOAD_DIR, exist_ok=True)
    url_tmpl, ext = _NATIVE[kind]
    async with _client() as client:
        resp = await _send_stream(client, url_tmpl.format(id=file_id))
        if _looks_like_html(resp):
            text = (await resp.aread()).decode("utf-8", "replace")
            await resp.aclose()
            _raise_for_html_errors(text)
            raise RuntimeError(f"Could not export this Google {kind} (it may be private).")

        filename = _unique_path(
            settings.DOWNLOAD_DIR,
            _filename_from_headers(resp.headers, f"gdoc_{file_id[:8]}.{ext}"),
        )
        filepath = os.path.join(settings.DOWNLOAD_DIR, filename)
        job_manager.update_job(job_id, filename=filename)
        try:
            written, _ = await _stream_response(job_id, resp, filepath, _single_report(job_id))
        finally:
            await resp.aclose()

    if written is not None and not job_manager.is_cancelled(job_id):
        job_manager.update_job(job_id, status="done", percent=100.0)


async def _download_folder(job_id: str, folder_id: str) -> None:
    os.makedirs(settings.DOWNLOAD_DIR, exist_ok=True)
    async with _client() as client:
        file_ids = await _enumerate_folder(client, folder_id)
        if not file_ids:
            raise RuntimeError(
                "Empty folder, private folder, or contents could not be read — "
                "ensure it is shared with 'Anyone with the link'."
            )

        raw_name = await _folder_name(client, folder_id) or f"GDrive_{folder_id[:8]}"
        folder_name = _unique_path(settings.DOWNLOAD_DIR, sanitize_filename(raw_name) or f"GDrive_{folder_id[:8]}")
        dest_dir = os.path.join(settings.DOWNLOAD_DIR, folder_name)
        os.makedirs(dest_dir, exist_ok=True)
        job_manager.update_job(job_id, filename=folder_name)

        n = len(file_ids)
        done_files = 0
        total_downloaded = 0

        for idx, file_id in enumerate(file_ids):
            if job_manager.is_cancelled(job_id):
                break
            try:
                resp, fname = await _resolve_file_response(client, file_id)
            except RuntimeError:
                # Skip individual quota/private/restricted files but keep going
                continue

            fname = _unique_path(dest_dir, sanitize_filename(fname or file_id))
            fpath = os.path.join(dest_dir, fname)

            def report(dl: int, total: int, elapsed: float, _idx: int = idx, _base: int = total_downloaded) -> None:
                frac = (dl / total) if total else 0
                pct = min((_idx + frac) / n * 100, 99.9)
                mbps = (dl / 1048576) / max(elapsed, 0.01)
                job = job_manager.get_job(job_id)
                if job:
                    job.push_speed(mbps)
                job_manager.update_job(job_id, percent=pct, downloaded_bytes=_base + dl)

            try:
                written, _ = await _stream_response(job_id, resp, fpath, report)
            finally:
                await resp.aclose()

            if written:
                total_downloaded += written
                done_files += 1

    if job_manager.is_cancelled(job_id):
        return
    if done_files == 0:
        raise RuntimeError(
            "No files in the folder could be downloaded — they may all be "
            "over quota, private, or Google-native documents."
        )
    job_manager.update_job(
        job_id, status="done", percent=100.0,
        filename=folder_name, downloaded_bytes=total_downloaded,
        size_bytes=total_downloaded,
        size=humanize.naturalsize(total_downloaded, binary=True),
    )
