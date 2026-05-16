from nicegui import ui

_STATUS = {
    "queued":      ("grey-7",   "⏳"),
    "metadata":    ("blue-6",   "🔍"),
    "downloading": ("amber-6",  "⬇"),
    "paused":      ("orange-7", "⏸"),
    "scanning":    ("purple-6", "🛡"),
    "done":        ("positive", "✓"),
    "error":       ("negative", "✕"),
}


def badge(status: str) -> None:
    color, symbol = _STATUS.get(status, ("grey-7", "?"))
    ui.badge(f"{symbol} {status.upper()}", color=color).classes(
        "px-2 py-1 text-xs font-mono rounded-full"
    )
