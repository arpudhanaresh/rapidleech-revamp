from nicegui import ui
from components.beans.disk_gauge import disk_gauge

_GLOW_CSS = """
.header-glow { box-shadow: 0 2px 20px rgba(0,229,255,0.15); }
"""


def header(stats: dict | None = None) -> None:
    ui.add_css(_GLOW_CSS)
    s = stats or {}
    with ui.header().classes(
        "header-glow bg-[#0f0f0f] border-b border-[#1e1e1e] px-6 py-2 "
        "flex items-center justify-between"
    ):
        # Left: brand
        with ui.row().classes("items-center gap-3"):
            ui.label("⚡ RapidLeech-Py").classes(
                "text-[#00e5ff] font-mono font-bold text-lg tracking-tight"
            )
            ui.label("Fetch files at full speed").classes(
                "text-gray-500 font-mono text-xs hidden sm:block"
            )

        # Center: quick stats
        with ui.row().classes("items-center gap-4"):
            ui.label(f"▼ {s.get('active_jobs', 0)} active").classes(
                "text-amber-400 font-mono text-xs"
            )
            ui.label(f"⚡ {s.get('current_speed_mbps', 0):.1f} MB/s").classes(
                "text-[#00e5ff] font-mono text-xs"
            )
            ui.label(f"✅ {s.get('jobs_today', 0)} today").classes(
                "text-green-400 font-mono text-xs"
            )

        # Right: disk + settings
        with ui.row().classes("items-center gap-3"):
            disk_gauge(s.get("disk_percent", 0), s.get("disk_free_gb", 0))
            ui.button("⚙", on_click=lambda: ui.navigate.to("/settings")).props(
                "flat round color=grey-6 size=sm"
            )
