from nicegui import ui
from models import SystemStats


def stats_bar(stats: SystemStats) -> None:
    items = [
        ("⬇",  f"{stats.active_jobs} Active",                    "text-amber-400"),
        ("⚡",  f"{stats.current_speed_mbps:.1f} MB/s",           "text-[#00e5ff]"),
        ("✓",  f"{stats.jobs_today} Today",                      "text-green-400"),
        ("💾",  f"{stats.total_downloaded_gb:.1f} GB Total",      "text-purple-400"),
    ]
    with ui.row().classes("w-full justify-center gap-6 py-2"):
        for symbol, label, color in items:
            with ui.row().classes(f"items-center gap-1 {color}"):
                ui.label(symbol).classes("text-base leading-none")
                ui.label(label).classes("font-mono text-sm font-semibold")
