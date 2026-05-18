from __future__ import annotations
import re
from urllib.parse import unquote_plus, urlparse

import httpx

from services import job_manager

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)

_DIRECT_RE = re.compile(
    r'href="(https://download\d*\.mediafire\.com/[^"]+)"',
    re.IGNORECASE,
)


async def _resolve(url: str) -> tuple[str, str]:
    """Fetch the MediaFire page and return (direct_download_url, filename)."""
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=15,
        headers={"User-Agent": _UA},
    ) as client:
        r = await client.get(url)
        r.raise_for_status()

    m = _DIRECT_RE.search(r.text)
    if not m:
        raise RuntimeError("Could not find download link on MediaFire page")

    direct_url = m.group(1)
    seg = urlparse(direct_url).path.split("/")[-1]
    filename = unquote_plus(seg)
    return direct_url, filename


async def download(job_id: str, url: str, max_conn: int) -> None:
    """Resolve MediaFire page to direct URL, then download via accelerator."""
    from services import accelerator

    job_manager.update_job(job_id, status="downloading", job_type="http")

    direct_url, filename = await _resolve(url)
    if filename:
        from services.security import sanitize_filename
        job_manager.update_job(job_id, filename=sanitize_filename(filename))

    await accelerator.accelerate(job_id, direct_url, max_conn)
