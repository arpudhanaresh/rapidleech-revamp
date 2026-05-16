from __future__ import annotations
from datetime import date

from models import SystemStats
from services import disk_monitor, job_manager
from services import db


async def get_system_stats() -> SystemStats:
    disk = disk_monitor.get_disk_usage()
    agg = await db.get_aggregate_stats()
    live = job_manager.list_live_jobs()

    active = [j for j in live if j.status == "downloading"]
    current_speed = sum(j.speed_mbps for j in active)

    return SystemStats(
        disk_total_gb=disk["total_gb"],
        disk_used_gb=disk["used_gb"],
        disk_free_gb=disk["free_gb"],
        disk_percent=disk["percent"],
        active_jobs=len(active),
        queued_jobs=sum(1 for j in live if j.status == "queued"),
        total_downloaded_gb=round((agg.get("total_downloaded_bytes") or 0) / 1e9, 3),
        current_speed_mbps=round(current_speed, 2),
        jobs_today=agg.get("jobs_today") or 0,
        total_jobs_completed=agg.get("total_jobs_completed") or 0,
        total_jobs_failed=agg.get("total_jobs_failed") or 0,
    )
