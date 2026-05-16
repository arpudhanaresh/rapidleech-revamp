from __future__ import annotations
from nicegui import ui
from config import settings
from components.layout.header import header
from components.layout.footer import footer
from services import disk_monitor


async def settings_page() -> None:
    disk = disk_monitor.get_disk_usage()

    with ui.header().classes("bg-[#0f0f0f] border-b border-[#1e1e1e] px-6 py-2 flex items-center justify-between"):
        with ui.row().classes("items-center gap-3"):
            ui.button("←", on_click=lambda: ui.navigate.to("/")).props("flat round color=grey-6 size=sm")
            ui.label("⚡ RapidLeech-Py — Settings").classes("text-[#00e5ff] font-mono font-bold")

    with ui.column().classes("w-full max-w-3xl mx-auto px-4 pt-20 pb-10 gap-6"):

        # ── Download ──────────────────────────────────────────────────────────
        with ui.card().classes("w-full bg-[#1a1a1a] border border-[#2a2a2a] p-5 gap-4"):
            ui.label("Download Settings").classes("text-[#00e5ff] font-mono text-sm font-bold uppercase tracking-widest")

            with ui.row().classes("w-full gap-6 flex-wrap"):
                with ui.column().classes("gap-1"):
                    ui.label("Max Concurrent Downloads").classes("text-gray-400 font-mono text-xs")
                    ui.number(value=settings.MAX_CONCURRENT, min=1, max=10, step=1).props(
                        "outlined dense dark color=cyan"
                    ).classes("w-32 font-mono")

                with ui.column().classes("gap-1"):
                    ui.label("Connections per Job").classes("text-gray-400 font-mono text-xs")
                    ui.number(value=settings.DEFAULT_CONNECTIONS, min=1, max=32, step=1).props(
                        "outlined dense dark color=cyan"
                    ).classes("w-32 font-mono")

                with ui.column().classes("gap-1"):
                    ui.label("Auto-Delete After (hours, 0=off)").classes("text-gray-400 font-mono text-xs")
                    ui.number(value=settings.FILE_TTL_HOURS, min=0, max=720, step=1).props(
                        "outlined dense dark color=cyan"
                    ).classes("w-32 font-mono")

            with ui.column().classes("gap-1 w-full"):
                ui.label("Download Directory").classes("text-gray-400 font-mono text-xs")
                ui.input(value=settings.DOWNLOAD_DIR).props(
                    "outlined dense dark color=cyan"
                ).classes("w-full font-mono text-sm")

        # ── Database ──────────────────────────────────────────────────────────
        with ui.card().classes("w-full bg-[#1a1a1a] border border-[#2a2a2a] p-5 gap-4"):
            ui.label("Database").classes("text-[#00e5ff] font-mono text-sm font-bold uppercase tracking-widest")
            with ui.column().classes("gap-1 w-full"):
                ui.label("DATABASE_URL (blank = local SQLite)").classes("text-gray-400 font-mono text-xs")
                ui.input(
                    value=settings.DATABASE_URL or "",
                    placeholder="postgresql+asyncpg://user:pass@host:5432/dbname",
                ).props("outlined dense dark color=cyan").classes("w-full font-mono text-sm")
                ui.label("Restart the server after changing the database URL.").classes("text-gray-600 font-mono text-[10px]")

        # ── Disk space ────────────────────────────────────────────────────────
        with ui.card().classes("w-full bg-[#1a1a1a] border border-[#2a2a2a] p-5 gap-3"):
            ui.label("Disk Space").classes("text-[#00e5ff] font-mono text-sm font-bold uppercase tracking-widest")
            used_pct = disk["percent"]
            color = "negative" if used_pct > 90 else "warning" if used_pct > 70 else "positive"
            ui.linear_progress(value=used_pct / 100, color=color, size="12px").classes("w-full rounded-full")
            with ui.row().classes("w-full justify-between font-mono text-xs text-gray-400"):
                ui.label(f"Used: {disk['used_gb']:.1f} GB")
                ui.label(f"Free: {disk['free_gb']:.1f} GB")
                ui.label(f"Total: {disk['total_gb']:.1f} GB")
            if used_pct > 90:
                ui.label("⚠ Low disk space — consider deleting old files").classes("text-amber-400 font-mono text-xs")

        # ── Security ─────────────────────────────────────────────────────────
        with ui.card().classes("w-full bg-[#1a1a1a] border border-[#2a2a2a] p-5 gap-4"):
            ui.label("Security").classes("text-[#00e5ff] font-mono text-sm font-bold uppercase tracking-widest")
            with ui.row().classes("w-full gap-6 flex-wrap"):
                with ui.column().classes("gap-1"):
                    ui.label("Rate Limit (fetches/min per IP)").classes("text-gray-400 font-mono text-xs")
                    ui.input(value=settings.RATE_LIMIT_FETCH).props(
                        "outlined dense dark color=cyan"
                    ).classes("w-40 font-mono text-sm")
                with ui.column().classes("gap-1"):
                    ui.label("ClamAV Socket (optional)").classes("text-gray-400 font-mono text-xs")
                    ui.input(
                        value=settings.CLAM_SOCKET or "",
                        placeholder="/var/run/clamav/clamd.ctl",
                    ).props("outlined dense dark color=cyan").classes("w-64 font-mono text-sm")

        ui.label("Changes to .env settings require a server restart to take effect.").classes(
            "text-gray-600 font-mono text-xs text-center w-full"
        )

    footer()
