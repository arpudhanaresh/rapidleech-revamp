from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from config import settings
from services import disk_monitor, stats_service

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory="templates")


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    stats = await stats_service.get_system_stats()
    return templates.TemplateResponse("index.html", {
        "request": request,
        "stats": stats,
        "ttl_max_hours": settings.FILE_TTL_MAX_HOURS,
    })


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    stats = await stats_service.get_system_stats()
    from services import db
    agg = await db.get_aggregate_stats()
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "stats": stats,
        "agg": agg,
    })
