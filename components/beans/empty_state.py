from nicegui import ui


def empty_state(message: str) -> None:
    with ui.column().classes("w-full items-center py-12 gap-2"):
        ui.label("📥").classes("text-6xl text-gray-600")
        ui.label(message).classes("text-gray-500 font-mono text-sm italic")
