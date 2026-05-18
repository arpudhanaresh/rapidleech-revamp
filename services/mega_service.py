from __future__ import annotations
import asyncio
import base64
import json
import os
import re
import struct
import tempfile
import threading
import time
from typing import Optional

import httpx
import humanize
from Crypto.Cipher import AES
from Crypto.Util import Counter

from config import settings
from services import job_manager
from services.fmt import fmt_eta
from services.security import sanitize_filename

_mega_semaphore = asyncio.Semaphore(1)
_MEGA_API = 'https://g.api.mega.co.nz/cs'

_RATE_LIMIT_PHRASES = (
    'quota', 'bandwidth', 'overquota', 'over quota',
    'rate limit', '509', 'transfer limit',
)


def _is_rate_limit_error(msg: str) -> bool:
    low = msg.lower()
    return any(p in low for p in _RATE_LIMIT_PHRASES)


def is_mega_url(url: str) -> bool:
    from urllib.parse import urlparse
    host = urlparse(url).netloc.lower().lstrip('www.')
    return host in ('mega.nz', 'mega.co.nz')


def _is_folder_url(url: str) -> bool:
    return '/folder/' in url or '/#F' in url


def _extract_handle(url: str) -> Optional[str]:
    m = re.search(r'/file/([^#?/]+)', url)
    if m:
        return m.group(1)
    m = re.search(r'#!([^!]+)', url)
    if m:
        return m.group(1)
    return None


# ── Crypto helpers ────────────────────────────────────────────────────────────

def _b64dec(s: str) -> bytes:
    s = s.replace('-', '+').replace('_', '/')
    s += '=' * ((-len(s)) % 4)
    return base64.b64decode(s)


def _a32(b: bytes) -> tuple:
    if len(b) % 4:
        b += b'\0' * (4 - len(b) % 4)
    return struct.unpack('>%dI' % (len(b) // 4), b)


def _pack32(a: tuple) -> bytes:
    return struct.pack('>%dI' % len(a), *a)


def _decrypt_node_key(enc_key_a32: tuple, master_key: tuple) -> tuple:
    # Mega key protocol: each 16-byte block decrypted independently with AES-CBC(zero IV)  # NOSONAR
    result: tuple = ()
    for i in range(0, len(enc_key_a32), 4):
        cipher = AES.new(_pack32(master_key), AES.MODE_CBC, b'\0' * 16)  # NOSONAR
        result += _a32(cipher.decrypt(_pack32(enc_key_a32[i:i + 4])))
    return result


def _decrypt_node_attrs(attrs_b64: str, key_a32: tuple) -> dict:
    """Decrypt AES-CBC (zero IV) node attributes. Returns {} on failure."""
    if len(key_a32) == 8:
        aes_key = (
            key_a32[0] ^ key_a32[4], key_a32[1] ^ key_a32[5],
            key_a32[2] ^ key_a32[6], key_a32[3] ^ key_a32[7],
        )
    else:
        aes_key = key_a32[:4]
    try:
        data = _b64dec(attrs_b64)
        if len(data) % 16:
            data += b'\0' * (16 - len(data) % 16)
        raw = AES.new(_pack32(aes_key), AES.MODE_CBC, b'\0' * 16).decrypt(data)  # NOSONAR
        if raw[:4] == b'MEGA':
            return json.loads(raw[4:].rstrip(b'\0').decode('utf-8'))
    except Exception:
        pass
    return {}


def _resolve_node_key(node: dict, folder_key: tuple) -> Optional[tuple]:
    """Parse and decrypt the key in a node's k field. Returns None on failure."""
    k_field = node.get('k', '')
    if ':' in k_field:
        k_field = k_field.split(':', 1)[1]
    if not k_field or k_field == '0':
        return None
    try:
        return _decrypt_node_key(_a32(_b64dec(k_field)), folder_key)
    except Exception:
        return None


# ── Folder download ───────────────────────────────────────────────────────────

def _parse_folder_url(url: str) -> tuple[str, str]:
    """Returns (folder_handle, folder_key_b64)."""
    m = re.search(r'/folder/([^#?/]+)#([^&?]+)', url)
    if m:
        return m.group(1), m.group(2)
    m = re.search(r'/#F!([^!]+)!(.+)', url)
    if m:
        return m.group(1), m.group(2)
    raise ValueError(f'Cannot parse folder URL: {url}')


def _get_folder_nodes(folder_handle: str) -> list:
    r = httpx.post(
        _MEGA_API, params={'id': '0', 'n': folder_handle},
        json=[{'a': 'f', 'c': 1, 'r': 1}], timeout=30,
    )
    data = r.json()
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        raise ValueError(f'Mega folder API error: {data}')
    return data[0].get('f', [])


def _get_file_dl_url(folder_handle: str, node_handle: str) -> Optional[str]:
    try:
        r = httpx.post(
            _MEGA_API, params={'id': '0', 'n': folder_handle},
            json=[{'a': 'g', 'g': 1, 'n': node_handle}], timeout=30,
        )
        data = r.json()
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return data[0].get('g')
    except Exception:
        pass
    return None


def _stream_decrypt_file(
    dl_url: str, dest_path: str, node_key: tuple,
    on_bytes: Optional[callable] = None,
) -> int:
    """Stream-download and AES-CTR decrypt a Mega file. Returns bytes written."""
    if len(node_key) == 8:
        aes_key = (
            node_key[0] ^ node_key[4], node_key[1] ^ node_key[5],
            node_key[2] ^ node_key[6], node_key[3] ^ node_key[7],
        )
        iv_high = (node_key[4] << 32) | node_key[5]
    else:
        aes_key = node_key[:4]
        iv_high = 0

    aes = AES.new(
        _pack32(aes_key), AES.MODE_CTR,
        counter=Counter.new(128, initial_value=iv_high * (2 ** 64)),
    )
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    written = 0
    with open(dest_path, 'wb') as f:
        with httpx.Client(follow_redirects=True, timeout=None) as client:
            with client.stream('GET', dl_url) as resp:
                for chunk in resp.iter_bytes(chunk_size=0x100000):
                    f.write(aes.decrypt(chunk))
                    written += len(chunk)
                    if on_bytes:
                        on_bytes(written)
    return written


def _get_folder_root(nodes: list, node_map: dict) -> Optional[dict]:
    """Return the root folder node — the t==1 node whose parent is not in the listing."""
    for node in nodes:
        if node.get('t') == 1 and node.get('p') not in node_map:
            return node
    return next((n for n in nodes if n.get('t') == 1), nodes[0] if nodes else None)


def _make_path_resolver(node_map: dict, root_handle: str, folder_key: tuple):
    def resolve(parent_h: str) -> str:
        parts: list[str] = []
        h = parent_h
        while h and h != root_handle:
            n = node_map.get(h)
            if not n:
                break
            key = _resolve_node_key(n, folder_key)
            if key and n.get('a'):
                attrs = _decrypt_node_attrs(n['a'], key)
                parts.append(sanitize_filename(attrs.get('n', h)))
            h = n.get('p')
        return os.path.join(*reversed(parts)) if parts else ''
    return resolve


def _folder_progress(job_id: str, current: int, total: int, state: dict) -> None:
    now = time.time()
    dt = now - state['last_time']
    if dt < 1.0:
        return
    speed = (current - state['last_dl']) / dt
    mbps = speed / (1024 * 1024)
    pct = min(current / total * 100, 99.9) if total else 0
    eta_s = int((total - current) / speed) if speed > 0 and total > current else 0
    job = job_manager.get_job(job_id)
    if job:
        job.push_speed(mbps)
    job_manager.update_job(job_id, percent=pct, downloaded_bytes=current, eta=fmt_eta(eta_s))
    state['last_dl'] = current
    state['last_time'] = now


def _download_one_folder_file(
    job_id: str, folder_handle: str, file_node: dict, folder_key: tuple,
    dest_dir: str, path_resolver, total_bytes: int,
    downloaded_before: int, progress: dict,
) -> int:
    node_key = _resolve_node_key(file_node, folder_key)
    if not node_key:
        return 0
    attrs = _decrypt_node_attrs(file_node['a'], node_key)
    filename = sanitize_filename(attrs.get('n', file_node['h']))
    sub = path_resolver(file_node.get('p', ''))
    file_path = os.path.join(dest_dir, sub, filename)
    dl_url = _get_file_dl_url(folder_handle, file_node['h'])
    if not dl_url:
        return 0

    def on_bytes(written: int) -> None:
        _folder_progress(job_id, downloaded_before + written, total_bytes, progress)

    try:
        return _stream_decrypt_file(dl_url, file_path, node_key, on_bytes)
    except Exception:
        return 0


def _folder_download(job_id: str, url: str) -> None:
    folder_handle, folder_key_b64 = _parse_folder_url(url)
    folder_key = _a32(_b64dec(folder_key_b64))

    job_manager.update_job(job_id, status='downloading', job_type='http')

    nodes = _get_folder_nodes(folder_handle)
    node_map = {n['h']: n for n in nodes}

    root_node = _get_folder_root(nodes, node_map)
    root_handle = root_node.get('h', '') if root_node else ''

    # Root folder key is decrypted from its k field using the URL share key
    folder_name = root_handle
    if root_node and root_node.get('k') and root_node.get('a'):
        root_key = _resolve_node_key(root_node, folder_key)
        if root_key:
            attrs = _decrypt_node_attrs(root_node['a'], root_key)
            folder_name = sanitize_filename(attrs.get('n') or root_handle) or root_handle

    # Register the folder name on the job now so _build_active_set() hides it
    # from the files listing while the download is still in progress.
    job_manager.update_job(job_id, filename=folder_name)

    dest_dir = os.path.join(settings.DOWNLOAD_DIR, folder_name)
    os.makedirs(dest_dir, exist_ok=True)

    file_nodes = [n for n in nodes if n.get('t') == 0 and n.get('k') and n.get('a')]
    total_bytes = sum(n.get('s', 0) for n in file_nodes)
    if total_bytes:
        job_manager.update_job(
            job_id, size_bytes=total_bytes,
            size=humanize.naturalsize(total_bytes, binary=True),
        )

    path_resolver = _make_path_resolver(node_map, root_handle, folder_key)
    progress = {'last_time': time.time(), 'last_dl': 0}
    downloaded_total = 0

    for file_node in file_nodes:
        n = _download_one_folder_file(
            job_id, folder_handle, file_node, folder_key,
            dest_dir, path_resolver, total_bytes, downloaded_total, progress,
        )
        downloaded_total += n

    job_manager.update_job(
        job_id, status='done', percent=100.0,
        filename=folder_name, downloaded_bytes=downloaded_total,
    )


# ── Single-file download (via mega.py) ────────────────────────────────────────

def _get_file_size(file_handle: str) -> int:
    try:
        r = httpx.post(
            _MEGA_API, params={'id': '0'},
            json=[{'a': 'g', 'g': 1, 'p': file_handle}], timeout=10,
        )
        data = r.json()
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return int(data[0].get('s', 0))
    except Exception:
        pass
    return 0


def _find_megapy_tmp(tmp_dir: str, known: set[str]) -> Optional[str]:
    try:
        for entry in os.scandir(tmp_dir):
            if entry.name.startswith('megapy_') and entry.name not in known:
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
    job_id: str, tmp_file: str, total_size: int, last_bytes: int, last_time: float,
) -> tuple[int, float, Optional[str]]:
    try:
        current = os.path.getsize(tmp_file)
        now = time.time()
        dt = now - last_time
        if dt >= 1.0:
            _emit_progress(job_id, current, total_size, last_bytes, dt)
            return current, now, tmp_file
        return last_bytes, last_time, tmp_file
    except OSError:
        return last_bytes, last_time, None


def _poll_progress(
    job_id: str, done: threading.Event, tmp_dir: str,
    known_tmp: set[str], total_size: int,
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
                job_id, tmp_file, total_size, last_bytes, last_time,
            )


def _start_download_thread(url: str) -> tuple[list, list, threading.Event]:
    try:
        from mega import Mega as _Mega
    except ImportError:
        raise ImportError('mega.py not installed — run: pip install mega.py')

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
        _folder_download(job_id, url)
        return

    os.makedirs(settings.DOWNLOAD_DIR, exist_ok=True)
    job_manager.update_job(job_id, status='downloading', job_type='http')

    handle = _extract_handle(url)
    total_size = _get_file_size(handle) if handle else 0
    if total_size:
        job_manager.update_job(
            job_id, size_bytes=total_size,
            size=humanize.naturalsize(total_size, binary=True),
        )

    tmp_dir = tempfile.gettempdir()
    try:
        known_tmp = {e.name for e in os.scandir(tmp_dir) if e.name.startswith('megapy_')}
    except OSError:
        known_tmp = set()

    try:
        result_path, exc, done = _start_download_thread(url)
    except ImportError as e:
        job_manager.update_job(job_id, status='error', error=str(e))
        return

    _poll_progress(job_id, done, tmp_dir, known_tmp, total_size)

    if exc[0]:
        msg = exc[0]
        if _is_rate_limit_error(msg):
            msg = 'Mega transfer quota exceeded — wait ~6 hours or use a different account'
        job_manager.update_job(job_id, status='error', error=msg)
        return

    if not result_path[0]:
        job_manager.update_job(job_id, status='error', error='Mega download returned no path')
        return

    try:
        fsize = os.path.getsize(result_path[0])
    except OSError:
        fsize = total_size

    fname = sanitize_filename(os.path.basename(result_path[0]))
    job_manager.update_job(
        job_id, status='done', percent=100.0,
        filename=fname, size_bytes=fsize,
        size=humanize.naturalsize(fsize, binary=True),
        downloaded_bytes=fsize,
    )
