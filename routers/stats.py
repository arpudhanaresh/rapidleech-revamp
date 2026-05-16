from fastapi import APIRouter
from services import stats_service, job_manager
from services.db import get_recent_logs

router = APIRouter(tags=["stats"])


@router.get("/stats")
async def get_stats():
    s = await stats_service.get_system_stats()
    return {
        "disk_total_gb": s.disk_total_gb,
        "disk_used_gb": s.disk_used_gb,
        "disk_free_gb": s.disk_free_gb,
        "disk_percent": s.disk_percent,
        "active_jobs": s.active_jobs,
        "queued_jobs": s.queued_jobs,
        "total_downloaded_gb": s.total_downloaded_gb,
        "current_speed_mbps": s.current_speed_mbps,
        "jobs_today": s.jobs_today,
        "total_jobs_completed": s.total_jobs_completed,
        "total_jobs_failed": s.total_jobs_failed,
    }


@router.get("/stats/logs")
async def get_logs(limit: int = 100):
    return await get_recent_logs(limit)


@router.get("/jobs/{job_id}/speed-history")
async def speed_history(job_id: str):
    job = job_manager.get_job(job_id)
    return job.speed_history if job else []
