from __future__ import annotations
from typing import Callable
from nicegui import ui
from components.fetch.url_input import url_input


def fetch_section(on_job_added: Callable[[str, str], None]) -> None:
    async def _submit(url: str) -> None:
        import asyncio
        from services import job_manager, downloader
        from services.security import validate_and_resolve, SecurityError

        # Torrent uploads are handled by _handle_torrent_upload in url_input —
        # the job + bytes registry are already set up; just dispatch and notify.
        if url.startswith("__torrent_upload__"):
            job_id = url.removeprefix("__torrent_upload__")
            _task = asyncio.create_task(downloader.dispatch(job_id, "torrent://uploaded"))
            on_job_added(job_id, "torrent://uploaded")
            ui.notify("Torrent added to queue", type="positive", timeout=2000)
            return

        try:
            clean = await validate_and_resolve(url)
        except SecurityError as e:
            ui.notify(str(e), type="negative", timeout=5000)
            return

        job_id_new = __import__("uuid").uuid4().__str__()
        job_manager.create_job(job_id_new, clean)
        _task = asyncio.create_task(downloader.dispatch(job_id_new, clean))
        on_job_added(job_id_new, clean)
        ui.notify("Added to queue", type="positive", timeout=2000)

    with ui.column().classes("w-full gap-2"):
        ui.label("Download").classes(
            "text-[#00e5ff] font-mono text-sm uppercase tracking-widest font-bold"
        )
        url_input(on_submit=lambda url: ui.timer(0, lambda: _submit(url), once=True))
