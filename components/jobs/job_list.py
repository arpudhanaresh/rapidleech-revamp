from __future__ import annotations
from nicegui import ui
from components.jobs.job_card import job_card
from components.beans.empty_state import empty_state


def job_list(job_ids: list[str]) -> None:
    with ui.column().classes("w-full gap-3"):
        ui.label("Active Downloads").classes(
            "text-[#00e5ff] font-mono text-sm uppercase tracking-widest font-bold"
        )
        if not job_ids:
            empty_state("No active downloads — paste a link above to get started")
        else:
            for jid in job_ids:
                job_card(jid)
