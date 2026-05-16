from __future__ import annotations
from typing import Callable
from nicegui import ui, events


_PLATFORM_ICONS = {
    "youtube.com": "smart_display", "youtu.be": "smart_display",
    "twitter.com": "tag", "x.com": "tag",
    "tiktok.com": "music_note", "instagram.com": "photo_camera",
    "vimeo.com": "videocam", "twitch.tv": "live_tv",
}


def url_input(on_submit: Callable[[str], None]) -> None:
    with ui.card().classes(
        "w-full bg-[#1a1a1a] border border-[#2a2a2a] p-4 "
        "hover:border-[#00e5ff33] transition-all duration-300"
    ):
        ui.label("Paste any link to download").classes(
            "text-gray-400 font-mono text-xs mb-2 uppercase tracking-widest"
        )

        with ui.row().classes("w-full gap-2 items-start"):
            inp = ui.input(
                placeholder="https://example.com/file.zip  or  magnet:?xt=urn:btih:...",
            ).classes(
                "flex-1 font-mono text-sm bg-[#111] border border-[#333] "
                "focus:border-[#00e5ff] rounded px-3 py-2"
            ).props("outlined dense dark color=cyan")

            # Detect type chip
            type_label = ui.label("").classes("hidden font-mono text-[10px] text-[#00e5ff] mt-2")

            def on_input(e: events.ValueChangeEventArguments) -> None:
                v = e.value or ""
                if v.startswith("magnet:"):
                    type_label.set_text("🧲 TORRENT").classes("block")
                elif v.lower().endswith(".torrent"):
                    type_label.set_text("📦 .TORRENT").classes("block")
                else:
                    from urllib.parse import urlparse
                    host = urlparse(v).netloc.lower().lstrip("www.")
                    icon = next((i for d, i in _PLATFORM_ICONS.items() if host.endswith(d)), None)
                    if icon:
                        type_label.set_text("▶ MEDIA").classes("block")
                    else:
                        type_label.set_text("").classes("hidden")

            inp.on("input", on_input)

            fetch_btn = ui.button("⚡ Fetch").props(
                "color=cyan unelevated"
            ).classes("font-mono font-bold px-6")

        # .torrent file upload
        with ui.row().classes("items-center gap-2 mt-1"):
            ui.label("or").classes("text-gray-600 font-mono text-xs")
            ui.upload(
                label="Upload .torrent",
                auto_upload=True,
                on_upload=lambda e: _handle_torrent_upload(e, on_submit),
            ).props("accept='.torrent' flat dense color=grey-7").classes("font-mono text-xs")

        def _submit() -> None:
            raw = inp.value.strip()
            if not raw:
                return
            # Multi-URL: split by newline
            urls = [u.strip() for u in raw.splitlines() if u.strip()]
            for url in urls:
                on_submit(url)
            inp.set_value("")
            fetch_btn.set_text("✅ Added!")
            ui.timer(1.5, lambda: fetch_btn.set_text("⚡ Fetch"), once=True)

        fetch_btn.on("click", _submit)
        inp.on("keydown.enter", _submit)


def _handle_torrent_upload(e, on_submit: Callable) -> None:
    import uuid
    from services import job_manager, torrent_service

    data = e.content.read()
    job_id = str(uuid.uuid4())

    # Store bytes in registry — no temp file, works on Windows and Linux
    torrent_service._uploaded_torrent_bytes[job_id] = data
    job_manager.create_job(job_id, "torrent://uploaded", job_type="torrent")

    try:
        files = torrent_service._parse_torrent_info(data)
        job_manager.get_job(job_id).files = files  # type: ignore[union-attr]
    except Exception:
        pass

    on_submit(f"__torrent_upload__{job_id}")
