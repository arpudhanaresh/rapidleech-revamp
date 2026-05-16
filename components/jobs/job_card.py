from __future__ import annotations
from nicegui import ui
from services import job_manager

_CARD_CSS = """
@keyframes border-glow {
  0%, 100% { box-shadow: 0 0 6px rgba(0,229,255,0.3); }
  50%       { box-shadow: 0 0 16px rgba(0,229,255,0.7); }
}
.card-downloading { animation: border-glow 2s ease-in-out infinite; border-color: #00e5ff !important; }
.card-done        { border-color: #4caf50 !important; }
.card-error       { border-color: #f44336 !important; }
.card-paused      { border-style: dashed !important; border-color: #ff9800 !important; }
"""

_STATUS_CLASS = {
    "downloading": "card-downloading",
    "done": "card-done",
    "error": "card-error",
    "paused": "card-paused",
}


def job_card(job_id: str) -> None:
    ui.add_css(_CARD_CSS)

    with ui.card().classes(
        "w-full bg-[#1a1a1a] border border-[#2a2a2a] p-4 gap-3 "
        "transition-all duration-300 animate-in slide-in-from-top"
    ) as card:
        _render_card(card, job_id)

    def _tick() -> None:
        job = job_manager.get_job(job_id)
        if not job or job.status in ("done", "error"):
            _timer.cancel()
            return
        _render_card.refresh(card, job_id)

    _timer = ui.timer(0.5, _tick)


@ui.refreshable
def _render_card(card: ui.card, job_id: str) -> None:
    from components.beans.badge import badge
    from components.beans.progress_bar import progress_bar
    from components.beans.stat_chip import stat_chip

    job = job_manager.get_job(job_id)
    if not job:
        return

    _apply_border(card, job.status)
    _render_header(job)
    progress_bar(job.percent, job.chunks, job.status == "downloading")
    _render_stats(job, stat_chip)
    _render_torrent_files(job)
    _render_result(job)
    _render_controls(job_id, job.status)


def _apply_border(card: ui.card, status: str) -> None:
    card.classes(remove="card-downloading card-done card-error card-paused")
    css_class = _STATUS_CLASS.get(status)
    if css_class:
        card.classes(add=css_class)


def _render_header(job) -> None:
    from components.beans.badge import badge
    with ui.row().classes("w-full items-center justify-between gap-2"):
        ui.label((job.torrent_name or job.url)[:80]).classes(
            "font-mono text-xs text-gray-300 flex-1 truncate"
        )
        if job.job_type == "torrent":
            ui.badge("TORRENT", color="blue-6").classes("text-[9px] font-mono")
        badge(job.status)
        if job.connections > 0:
            ui.label(f"×{job.connections}").classes("text-[10px] font-mono text-gray-500")


def _render_stats(job, stat_chip) -> None:
    with ui.row().classes("flex-wrap gap-2"):
        stat_chip("Speed", job.speed)
        stat_chip("ETA", job.eta)
        stat_chip("Size", job.size)
        stat_chip("Done", f"{job.percent:.1f}%")
        if job.job_type == "torrent":
            stat_chip("Seeds", str(job.seeders))
            stat_chip("Peers", str(job.peers))
            stat_chip("Ratio", f"{job.ratio:.2f}")


def _render_torrent_files(job) -> None:
    if job.job_type != "torrent" or not job.files:
        return
    with ui.expansion(f"📁 Files ({len(job.files)})").classes(
        "w-full bg-[#111] rounded text-xs font-mono"
    ):
        for f in job.files:
            with ui.row().classes("w-full items-center gap-2 py-0.5"):
                ui.label(f.path).classes("text-gray-400 flex-1 truncate")
                ui.label(f"{f.size_mb:.1f} MB").classes("text-gray-500 text-[10px]")
                with ui.element("div").classes("w-16 h-1 bg-[#2a2a2a] rounded overflow-hidden"):
                    ui.element("div").classes("h-full bg-[#00e5ff] rounded").style(
                        f"width:{f.percent:.0f}%"
                    )


def _render_result(job) -> None:
    if job.status == "done" and job.filename:
        with ui.row().classes("items-center gap-2 flex-wrap"):
            ui.label("✓").classes("text-green-400 font-bold")
            ui.label(job.filename).classes("text-green-400 font-mono text-xs")
            ui.button(
                "⬇ Download",
                on_click=lambda: ui.navigate.to(f"/api/files/download/{job.filename}", new_tab=True),
            ).props("flat dense color=positive size=sm")
            if job.sha256:
                ui.button(
                    "⧉ SHA256",
                    on_click=lambda: ui.clipboard.write(job.sha256),
                ).props("flat dense color=grey-6 size=sm")
        if job.scan_result:
            symbol = "✓" if job.scan_result == "clean" else "⚠"
            color = "positive" if job.scan_result == "clean" else "negative"
            ui.chip(f"{symbol} {job.scan_result}", color=color).classes("text-[10px]")

    if job.status == "error":
        with ui.row().classes("items-center gap-1"):
            ui.label("✕").classes("text-red-400 font-bold")
            ui.label(job.error or "Unknown error").classes("text-red-400 font-mono text-xs")


def _render_controls(job_id: str, status: str) -> None:
    if status not in ("downloading", "paused", "queued"):
        return
    with ui.row().classes("gap-2 mt-1"):
        if status == "downloading":
            ui.button("⏸ Pause", on_click=lambda: _pause(job_id)).props(
                "flat dense color=orange size=sm"
            )
        elif status == "paused":
            ui.button("▶ Resume", on_click=lambda: _resume(job_id)).props(
                "flat dense color=cyan size=sm"
            )
        ui.button("✕ Cancel", on_click=lambda: _cancel(job_id)).props(
            "flat dense color=negative size=sm"
        )


def _pause(job_id: str) -> None:
    import asyncio
    from routers.fetch import pause_job
    _t = asyncio.create_task(pause_job(job_id))


def _resume(job_id: str) -> None:
    import asyncio
    from routers.fetch import resume_job
    _t = asyncio.create_task(resume_job(job_id))


def _cancel(job_id: str) -> None:
    import asyncio
    _t = asyncio.create_task(job_manager.cancel_job(job_id))
