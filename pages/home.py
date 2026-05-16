from __future__ import annotations
from nicegui import ui
from services import job_manager, stats_service
from services.db import get_recent_logs
from components.layout.header import header
from components.layout.footer import footer
from components.dashboard.stats_bar import stats_bar
from components.dashboard.activity_log import activity_log
from components.fetch.fetch_section import fetch_section
from components.jobs.job_list import job_list
from components.files.file_manager import file_manager

_FONTS = """
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&display=swap" rel="stylesheet">
<style>
  * { font-family: 'JetBrains Mono', monospace !important; }
  body { background: #0f0f0f; }
  .q-card { background: #1a1a1a; }
</style>
"""


async def home_page() -> None:
    ui.add_head_html(_FONTS)

    stats = await stats_service.get_system_stats()
    logs = await get_recent_logs(100)
    job_ids = [j.job_id for j in job_manager.list_live_jobs()]

    # Mutable list updated by fetch_section callbacks
    _job_ids: list[str] = list(job_ids)

    header(stats.__dict__)

    with ui.column().classes(
        "w-full max-w-5xl mx-auto px-4 pt-20 pb-10 gap-6"
    ):
        # Live stats bar — refreshes every 2s
        stats_container = ui.element("div").classes("w-full")
        with stats_container:
            stats_bar(stats)

        async def _refresh_stats() -> None:
            s = await stats_service.get_system_stats()
            stats_container.clear()
            with stats_container:
                stats_bar(s)

        ui.timer(2, _refresh_stats)

        # Fetch section
        jobs_container = ui.element("div").classes("w-full")

        def _on_job_added(job_id: str, url: str) -> None:
            _job_ids.append(job_id)
            jobs_container.clear()
            with jobs_container:
                job_list(list(_job_ids))

        fetch_section(on_job_added=_on_job_added)

        # Job list
        with jobs_container:
            job_list(_job_ids)

        # File manager
        file_manager()

        # Activity log
        log_container = ui.element("div").classes("w-full")
        with log_container:
            activity_log(logs)

        async def _refresh_logs() -> None:
            new_logs = await get_recent_logs(100)
            log_container.clear()
            with log_container:
                activity_log(new_logs)

        ui.timer(10, _refresh_logs)

    footer()
