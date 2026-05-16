from __future__ import annotations
from nicegui import ui

_LEVEL_COLOR = {
    "info":     "text-gray-300",
    "done":     "text-green-400",
    "warn":     "text-amber-400",
    "error":    "text-red-400",
    "security": "text-purple-400",
}


def activity_log(logs: list[dict]) -> None:
    with ui.expansion("⌨ Activity Log").classes(
        "w-full bg-[#0a0a0a] border border-[#1e1e1e] rounded font-mono text-xs"
    ):
        with ui.element("div").classes(
            "h-48 overflow-y-auto p-2 flex flex-col-reverse gap-0.5"
        ):
            for entry in logs:
                ts = entry.get("ts", "")[:19].replace("T", " ")
                level = entry.get("level", "info")
                msg = entry.get("message", "")
                color = _LEVEL_COLOR.get(level, "text-gray-400")
                ui.label(f"[{ts}] [{level.upper():8s}] {msg}").classes(
                    f"{color} leading-tight whitespace-pre-wrap break-all"
                )
