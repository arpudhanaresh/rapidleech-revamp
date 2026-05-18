from __future__ import annotations
import asyncio
import os
import tempfile
import time
from pathlib import Path
from typing import Optional

import httpx
import humanize

from config import settings
from models import TorrentFile
from services import job_manager
from services.fmt import fmt_eta

# libtorrent is optional — graceful degradation if not installed
try:
    import libtorrent as lt
    _LT_AVAILABLE = True
except ImportError:
    lt = None  # type: ignore
    _LT_AVAILABLE = False

_session: Optional[object] = None
_handles: dict[str, object] = {}          # job_id → torrent handle
_uploaded_torrent_bytes: dict[str, bytes] = {}  # job_id → raw .torrent bytes (upload flow)


def _get_session():
    global _session
    if not _LT_AVAILABLE:
        raise RuntimeError("libtorrent is not installed. Run: pip install lbry-libtorrent")
    if _session is None:
        _session = lt.session({
            "alert_mask": lt.alert.category_t.all_categories,
            "listen_interfaces": "0.0.0.0:6881",
            "dht_bootstrap_nodes": "router.bittorrent.com:6881,dht.transmissionbt.com:6881,router.utorrent.com:6881",
            "enable_dht": True,
            "enable_lsd": True,
            "enable_upnp": True,
            "enable_natpmp": True,
            "connections_limit": settings.TORRENT_MAX_CONNECTIONS,
        })
    return _session


async def fetch_metadata(url: str) -> list[TorrentFile]:
    """Return file list from a magnet URI or .torrent URL before downloading."""
    if not _LT_AVAILABLE:
        return []

    sess = _get_session()

    if url.startswith("magnet:"):
        return await asyncio.to_thread(_magnet_metadata, sess, url)
    else:
        torrent_bytes = await _download_torrent_file(url)
        return _parse_torrent_info(torrent_bytes)


def _magnet_metadata(sess, magnet: str) -> list[TorrentFile]:
    params = lt.parse_magnet_uri(magnet)
    params.save_path = tempfile.mkdtemp()  # tempfile.mkdtemp is cross-platform
    params.flags = lt.torrent_flags.upload_mode  # don't download data yet

    handle = sess.add_torrent(params)
    deadline = time.time() + 30
    while time.time() < deadline:
        if handle.status().has_metadata:
            break
        time.sleep(0.5)

    if not handle.status().has_metadata:
        sess.remove_torrent(handle)
        raise RuntimeError("Could not fetch torrent metadata within 30s")

    info = handle.torrent_file()
    sess.remove_torrent(handle)
    return _files_from_info(info)


async def _download_torrent_file(url: str) -> bytes:
    async with httpx.AsyncClient(follow_redirects=True) as client:
        resp = await client.get(url, timeout=15)
        resp.raise_for_status()
        return resp.content


def _parse_torrent_info(data: bytes) -> list[TorrentFile]:
    info = lt.torrent_info(lt.bdecode(data))
    return _files_from_info(info)


def _files_from_info(info) -> list[TorrentFile]:
    files = []
    fs = info.files()
    for i in range(fs.num_files()):
        files.append(
            TorrentFile(
                index=i,
                path=fs.file_path(i),
                size_mb=round(fs.file_size(i) / (1024 * 1024), 2),
            )
        )
    return files


async def start(
    job_id: str,
    url: str,
    selected_indices: Optional[list[int]] = None,
) -> None:
    """Start torrent download — runs the alert loop in a thread."""
    uploaded = await asyncio.to_thread(_torrent_download, job_id, url, selected_indices)
    if uploaded:
        from services.db import increment_uploaded
        await increment_uploaded(uploaded)
    from services.downloader import _post_download
    await _post_download(job_id)


def _torrent_download(
    job_id: str,
    url: str,
    selected_indices: Optional[list[int]],
) -> int:
    """Returns total uploaded bytes for the session."""
    if not _LT_AVAILABLE:
        job_manager.update_job(job_id, status="error", error="libtorrent not installed")
        return 0

    sess = _get_session()
    os.makedirs(settings.DOWNLOAD_DIR, exist_ok=True)
    job_manager.update_job(job_id, status="metadata", job_type="torrent")

    save_path = str(Path(settings.DOWNLOAD_DIR).resolve())
    handle = _add_torrent_handle(sess, job_id, url, save_path)
    _handles[job_id] = handle

    if not _wait_for_metadata(job_id, handle):
        return 0

    files = _apply_metadata(job_id, handle)
    _apply_file_priorities(handle, files, selected_indices)
    uploaded = _run_alert_loop(job_id, sess, handle, files)
    _handles.pop(job_id, None)
    try:
        sess.remove_torrent(handle, option=0)  # 0 = keep files, just stop seeding
    except Exception:
        pass
    return uploaded


def _add_torrent_handle(sess, job_id: str, url: str, save_path: str):
    """Create and return a libtorrent handle from a magnet URI, uploaded bytes, or remote URL."""
    if url.startswith("magnet:"):
        params = lt.parse_magnet_uri(url)
        params.save_path = save_path
        handle = sess.add_torrent(params)
    elif job_id in _uploaded_torrent_bytes:
        data = _uploaded_torrent_bytes.pop(job_id)
        info = lt.torrent_info(lt.bdecode(data))
        handle = sess.add_torrent({"ti": info, "save_path": save_path})
    else:
        import httpx as _httpx
        data = _httpx.get(url, follow_redirects=True, timeout=15).content
        info = lt.torrent_info(lt.bdecode(data))
        handle = sess.add_torrent({"ti": info, "save_path": save_path})

    try:
        handle.set_max_connections(settings.TORRENT_MAX_CONNECTIONS_PER_TORRENT)
    except Exception:
        pass
    return handle


_METADATA_TIMEOUT_S = 60
_STALL_TIMEOUT_S = 1200  # 20 min with zero bytes = dead torrent


def _wait_for_metadata(job_id: str, handle) -> bool:
    """Block until metadata arrives or timeout. Returns False if job gone or timed out."""
    deadline = time.time() + _METADATA_TIMEOUT_S
    while not handle.status().has_metadata:
        if job_manager.get_job(job_id) is None:
            return False
        if time.time() > deadline:
            job_manager.update_job(job_id, status="error",
                                   error="Metadata timeout — torrent may be dead or have no seeds")
            return False
        time.sleep(0.5)
    return True


def _apply_metadata(job_id: str, handle) -> list[TorrentFile]:
    """Populate job fields from torrent metadata; return file list."""
    ti = handle.torrent_file()
    files = _files_from_info(ti)
    job_manager.update_job(
        job_id,
        torrent_name=ti.name(),
        files=files,
        size_bytes=ti.total_size(),
        size=humanize.naturalsize(ti.total_size(), binary=True),
        status="downloading",
        filename=ti.name(),
    )
    return files


def _apply_file_priorities(handle, files: list[TorrentFile], selected_indices: Optional[list[int]]) -> None:
    """Skip unselected files by setting their priority to 0."""
    if selected_indices is None:
        return
    for i in range(len(files)):
        priority = lt.default_priority if i in selected_indices else lt.low_priority
        handle.file_priority(i, priority)


def _process_alerts(job_id: str, sess, handle, files: list[TorrentFile]) -> bool:
    """Process pending alerts. Returns True if a fatal file error was detected."""
    for alert in sess.pop_alerts():
        if not (hasattr(alert, "handle") and alert.handle == handle):
            continue
        _handle_alert(job_id, alert, files)
        if isinstance(alert, lt.file_error_alert):
            job_manager.update_job(job_id, status="error",
                                   error=f"File error: {alert.message()}")
            return True
    return False


def _check_stall(job_id: str, st, last_done: int, last_progress: float) -> tuple[int, float, bool]:
    """Update stall tracker. Returns (last_done, last_progress, stalled)."""
    if st.total_wanted_done > last_done:
        return st.total_wanted_done, time.time(), False
    stalled = st.num_seeds == 0 and time.time() - last_progress > _STALL_TIMEOUT_S
    if stalled:
        job_manager.update_job(job_id, status="error",
                               error="Stalled — no seeds and no progress for 5 minutes")
    return last_done, last_progress, stalled


def _run_alert_loop(job_id: str, sess, handle, files: list[TorrentFile]) -> int:
    """Poll libtorrent alerts and status until the torrent finishes or errors.
    Returns total uploaded bytes."""
    _finished = (lt.torrent_status.seeding, lt.torrent_status.finished)
    last_progress = time.time()
    last_done = 0

    while True:
        job = job_manager.get_job(job_id)
        if not job or job.status == "error":
            return 0
        if _process_alerts(job_id, sess, handle, files):
            return 0

        st = handle.status()
        _update_torrent_status(job_id, st, files, handle)

        if st.state in _finished:
            job_manager.update_job(job_id, status="done", percent=100.0)
            return st.all_time_upload

        # Skip stall detection while libtorrent is verifying existing pieces
        if st.state in _checking_states():
            time.sleep(1)
            continue

        last_done, last_progress, stalled = _check_stall(job_id, st, last_done, last_progress)
        if stalled:
            return 0

        time.sleep(1)


def _handle_alert(job_id: str, alert, files: list[TorrentFile]) -> None:
    if lt and isinstance(alert, lt.file_completed_alert):
        idx = alert.index
        if 0 <= idx < len(files):
            files[idx].percent = 100.0
            job_manager.update_job(job_id, files=files)


_CHECKING_STATES = None  # resolved lazily after lt is confirmed available


def _checking_states():
    global _CHECKING_STATES
    if _CHECKING_STATES is None:
        _CHECKING_STATES = {
            lt.torrent_status.checking_files,
            lt.torrent_status.checking_resume_data,
        }
    return _CHECKING_STATES


def _update_torrent_status(job_id: str, st, files: list[TorrentFile], handle) -> None:
    total = st.total_wanted or 1
    done = st.total_wanted_done
    pct = min(done / total * 100, 99.9) if st.state != lt.torrent_status.seeding else 100.0
    mbps = st.download_rate / (1024 * 1024)
    eta_s = int((total - done) / st.download_rate) if st.download_rate > 0 else 0

    if st.state in _checking_states():
        check_pct = st.progress * 100 if hasattr(st, "progress") else 0
        job_manager.update_job(job_id, status="checking", percent=round(check_pct, 1))
        return

    # Update per-file progress from piece map
    if handle.status().has_metadata:
        for i, f in enumerate(files):
            if f.selected:
                prog = handle.file_progress()
                if i < len(prog):
                    fs_size = max(int(f.size_mb * 1024 * 1024), 1)
                    f.percent = min(prog[i] / fs_size * 100, 100.0)

    upload_mbps = st.upload_rate / (1024 * 1024)
    job = job_manager.get_job(job_id)
    if job:
        job.push_speed(mbps)
    job_manager.update_job(
        job_id,
        status="downloading",
        percent=pct,
        downloaded_bytes=done,
        eta=fmt_eta(eta_s),
        seeders=st.num_seeds,
        peers=st.num_peers,
        leechers=max(st.num_peers - st.num_seeds, 0),
        ratio=round(st.all_time_upload / max(st.all_time_download, 1), 3),
        upload_speed=f"{upload_mbps:.1f} MB/s",
        upload_speed_mbps=upload_mbps,
        files=files,
    )


def get_peers(job_id: str) -> list[dict]:
    handle = _handles.get(job_id)
    if not handle:
        return []
    try:
        peers = []
        for p in handle.get_peer_info():
            peers.append({
                "ip": p.ip[0],
                "client": p.client.decode("utf-8", errors="replace"),
                "dl_speed_kb": round(p.down_speed / 1024, 1),
                "progress": round(p.progress * 100, 1),
                "flags": _peer_flags(p),
            })
        return sorted(peers, key=lambda x: x["dl_speed_kb"], reverse=True)[:50]
    except Exception:
        return []


def _peer_flags(p) -> str:
    flags = []
    if p.flags & lt.peer_info.interesting:
        flags.append("D")
    if p.flags & lt.peer_info.choked:
        flags.append("C")
    if p.flags & lt.peer_info.rc4_encrypted:
        flags.append("E")
    return "".join(flags)


def pause_torrent(job_id: str) -> None:
    handle = _handles.get(job_id)
    if handle:
        handle.pause()
        job_manager.update_job(job_id, status="paused")


def resume_torrent(job_id: str) -> None:
    handle = _handles.get(job_id)
    if handle:
        handle.resume()
        job_manager.update_job(job_id, status="downloading")


def cancel_torrent(job_id: str) -> None:
    handle = _handles.pop(job_id, None)
    if handle and _session:
        _session.remove_torrent(handle, option=0)
    job_manager.update_job(job_id, status="error", error="Cancelled by user")
