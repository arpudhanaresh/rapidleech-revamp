from __future__ import annotations
import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from services import job_manager, stats_service, file_service
from routers.fetch import _job_dict

router = APIRouter(tags=["events"])


@router.get("/events")
async def sse(request: Request):
    async def stream():
        while True:
            if await request.is_disconnected():
                break
            jobs = [_job_dict(j) for j in job_manager.list_live_jobs()]
            stats = await stats_service.get_system_stats()
            files = [f.__dict__ for f in file_service.list_files()]
            payload = json.dumps({
                "jobs": jobs,
                "stats": {
                    "active_jobs": stats.active_jobs,
                    "current_speed_mbps": round(stats.current_speed_mbps, 2),
                    "jobs_today": stats.jobs_today,
                    "disk_free_gb": round(stats.disk_free_gb, 1),
                    "disk_total_gb": round(stats.disk_total_gb, 1),
                    "disk_percent": round(stats.disk_percent, 1),
                },
                "files": files,
            })
            yield f"data: {payload}\n\n"
            await asyncio.sleep(0.8)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
