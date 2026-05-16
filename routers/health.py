from fastapi import APIRouter
from services import disk_monitor, job_manager

router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    disk = disk_monitor.get_disk_usage()
    live = job_manager.list_live_jobs()
    return {
        "status": "ok",
        "disk_free_gb": disk["free_gb"],
        "active_jobs": sum(1 for j in live if j.status == "downloading"),
    }
