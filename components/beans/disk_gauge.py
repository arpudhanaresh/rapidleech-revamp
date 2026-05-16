from nicegui import ui

_PULSE_CSS = """
@keyframes pulse-red {
  0%, 100% { box-shadow: 0 0 0 0 rgba(244,67,54,0.7); }
  50%       { box-shadow: 0 0 0 8px rgba(244,67,54,0); }
}
.disk-critical { animation: pulse-red 1.2s ease-in-out infinite; border-radius: 50%; }
"""


def disk_gauge(disk_percent: float, free_gb: float) -> ui.circular_progress:
    ui.add_css(_PULSE_CSS)
    if disk_percent >= 98:
        color, extra = "negative", "disk-critical"
    elif disk_percent >= 90:
        color, extra = "warning", ""
    else:
        color, extra = "positive", ""

    with ui.element("div").classes(extra).tooltip(
        f"Disk: {disk_percent:.1f}% used — {free_gb:.1f} GB free"
    ):
        prog = ui.circular_progress(
            value=disk_percent / 100,
            min=0, max=1,
            size="40px",
            color=color,
            show_value=False,
        )
        ui.label(f"{free_gb:.1f}G").classes("text-[9px] font-mono text-gray-400 absolute")
    return prog
