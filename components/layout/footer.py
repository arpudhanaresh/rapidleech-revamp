from nicegui import ui


def footer() -> None:
    with ui.footer().classes(
        "bg-[#0a0a0a] border-t border-[#1e1e1e] py-2 flex justify-center"
    ):
        ui.label("RapidLeech-Py — Powered by yt-dlp, aria2 & libtorrent").classes(
            "text-gray-600 font-mono text-xs"
        )
