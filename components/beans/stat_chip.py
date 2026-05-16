from nicegui import ui


def stat_chip(label: str, value: str) -> None:
    with ui.element("div").classes(
        "flex flex-col items-center px-3 py-1 rounded border border-[#2a2a2a] bg-[#111]"
    ):
        ui.label(label).classes("text-[10px] text-gray-500 uppercase tracking-widest font-mono")
        ui.label(value).classes("text-xs text-[#00e5ff] font-mono font-semibold")
