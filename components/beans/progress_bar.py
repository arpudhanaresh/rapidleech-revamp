from __future__ import annotations
from nicegui import ui
from models import ChunkInfo

_SHIMMER_CSS = """
@keyframes shimmer {
  0%   { background-position: -200% center; }
  100% { background-position:  200% center; }
}
.shimmer-bar {
  background: linear-gradient(90deg,
    #00e5ff 25%, #80ffff 50%, #00e5ff 75%);
  background-size: 200% auto;
  animation: shimmer 1.5s linear infinite;
}
@keyframes pulse-chunk {
  0%, 100% { opacity: 1; }
  50%       { opacity: 0.4; }
}
.chunk-active { animation: pulse-chunk 1s ease-in-out infinite; }
"""


def progress_bar(percent: float, chunks: list[ChunkInfo], active: bool) -> None:
    ui.add_css(_SHIMMER_CSS)
    pct = min(max(percent, 0), 100)

    with ui.column().classes("w-full gap-1"):
        # Main bar
        bar_cls = "w-full h-3 rounded-full overflow-hidden bg-[#2a2a2a]"
        with ui.element("div").classes(bar_cls):
            fill_cls = "h-full rounded-full transition-all duration-300 "
            fill_cls += "shimmer-bar" if active else "bg-[#00e5ff]"
            ui.element("div").classes(fill_cls).style(f"width:{pct:.1f}%")

        # Chunk map — only shown when chunked download is active
        if chunks:
            with ui.row().classes("w-full gap-0.5"):
                for chunk in chunks:
                    chunk_pct = (chunk.downloaded / max(chunk.end - chunk.start, 1)) * 100
                    if chunk.done:
                        cls = "bg-[#00e5ff] rounded"
                    elif chunk_pct > 0:
                        cls = "bg-[#00b8cc] rounded chunk-active"
                    else:
                        cls = "bg-[#2a2a2a] rounded"
                    ui.element("div").classes(cls).style(
                        f"flex:1; height:4px"
                    ).tooltip(f"Chunk {chunk.index+1} — {chunk_pct:.0f}%")
