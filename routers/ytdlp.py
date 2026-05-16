from __future__ import annotations
import asyncio

from fastapi import APIRouter
from pydantic import BaseModel

from services.downloader import extract_formats

router = APIRouter(tags=["ytdlp"])


class FormatRequest(BaseModel):
    url: str


@router.post("/ytdlp/formats")
async def get_formats(body: FormatRequest):
    try:
        formats = await asyncio.wait_for(
            asyncio.to_thread(extract_formats, body.url),
            timeout=20.0,
        )
        return {"formats": formats}
    except asyncio.TimeoutError:
        return {"formats": [], "error": "Timed out fetching formats"}
    except Exception as e:
        return {"formats": [], "error": str(e)}
