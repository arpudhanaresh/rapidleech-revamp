from __future__ import annotations
from datetime import datetime, timezone, timedelta
from nicegui import ui
from services import file_service
from config import settings
from components.beans.empty_state import empty_state

_MONO_XS = "font-mono text-xs"

_TABLE_COLUMNS = [
    {"name": "name",    "label": "Filename",   "field": "name",    "sortable": True, "align": "left"},
    {"name": "size",    "label": "Size",        "field": "size",    "sortable": True},
    {"name": "date",    "label": "Downloaded",  "field": "date",    "sortable": True},
    {"name": "expires", "label": "Expires In",  "field": "expires", "sortable": True},
    {"name": "actions", "label": "Actions",     "field": "actions"},
]


def file_manager() -> None:
    with ui.column().classes("w-full gap-3"):
        with ui.row().classes("w-full items-center justify-between"):
            ui.label("Downloaded Files").classes(
                "text-[#00e5ff] font-mono text-sm uppercase tracking-widest font-bold"
            )
            ui.button("⬇ Download All as ZIP", on_click=_zip_all).props(
                "flat dense color=cyan"
            ).classes(_MONO_XS)
        _file_table()
        ui.timer(60, _file_table.refresh)


@ui.refreshable
def _file_table() -> None:
    files = file_service.list_files()
    if not files:
        empty_state("No files yet — downloads will appear here")
        return

    now = datetime.now(timezone.utc)
    rows = [_build_row(f, now) for f in files]

    with ui.table(columns=_TABLE_COLUMNS, rows=rows, selection="multiple").classes(
        f"w-full bg-[#111] border border-[#1e1e1e] rounded {_MONO_XS}"
    ).props("dark flat dense") as table:
        table.add_slot("body-cell-expires", """
            <q-td :props="props">
                <span :class="props.row._exp_color">{{ props.row.expires }}</span>
            </q-td>
        """)
        table.add_slot("body-cell-actions", """
            <q-td :props="props" style="white-space:nowrap">
                <q-btn flat dense label="⬇" color="cyan" style="font-size:14px;padding:2px 8px"
                    :href="`/api/files/download/${encodeURIComponent(props.row.name)}`"
                    target="_blank" title="Download" />
                <q-btn flat dense label="⧉" color="grey" style="font-size:14px;padding:2px 8px"
                    @click="navigator.clipboard.writeText(`/api/files/download/${props.row.name}`)"
                    title="Copy link" />
                <q-btn flat dense label="✕" color="negative" style="font-size:14px;padding:2px 8px"
                    @click="$emit('delete', props.row.name)"
                    title="Delete" />
            </q-td>
        """)
        table.on("delete", lambda e: _delete_file(e.args))

    def _bulk_delete() -> None:
        for row in table.selected:
            file_service.delete_file(row["name"])
        _file_table.refresh()

    def _bulk_zip() -> None:
        names = [r["name"] for r in table.selected]
        if names:
            ui.navigate.to(f"/api/files/zip?names={','.join(names)}", new_tab=True)

    with ui.row().classes("gap-2 mt-1"):
        ui.button("✕ Delete Selected", on_click=_bulk_delete).props(
            "flat dense color=negative size=sm"
        ).classes(_MONO_XS)
        ui.button("📦 ZIP Selected", on_click=_bulk_zip).props(
            "flat dense color=cyan size=sm"
        ).classes(_MONO_XS)


def _build_row(f, now: datetime) -> dict:
    try:
        mtime = datetime.fromisoformat(f.created_at)
    except Exception:
        mtime = now
    exp_str, exp_color = _expiry_info(mtime, settings.FILE_TTL_HOURS, now)
    return {
        "name": f.filename,
        "size": f"{f.size_mb:.1f} MB",
        "date": f.created_at[:16].replace("T", " "),
        "expires": exp_str,
        "_exp_color": exp_color,
        "_scan": f.scan_result or "",
    }


def _expiry_info(mtime: datetime, ttl_hours: int, now: datetime) -> tuple[str, str]:
    if ttl_hours <= 0:
        return "—", "text-gray-600"
    total_sec = ((mtime + timedelta(hours=ttl_hours)) - now).total_seconds()
    if total_sec <= 0:
        return "Expired", "text-red-500"
    if total_sec < 1800:
        return f"⚠ {int(total_sec // 60)}m", "text-red-400 animate-pulse"
    if total_sec < 3600:
        return f"{int(total_sec // 60)}m", "text-amber-400"
    h, m = int(total_sec // 3600), int((total_sec % 3600) // 60)
    return f"{h}h {m}m", "text-green-400"


def _delete_file(filename: str) -> None:
    file_service.delete_file(filename)
    _file_table.refresh()


def _zip_all() -> None:
    if file_service.list_files():
        ui.navigate.to("/api/files/zip", new_tab=True)
