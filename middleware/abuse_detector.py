from __future__ import annotations
import time
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

# ip → list of fetch timestamps (last 60s)
_fetch_times: dict[str, list[float]] = defaultdict(list)
# ip → bytes downloaded in last hour
_bytes_hour: dict[str, float] = defaultdict(float)
# blocked IPs → unblock timestamp
_blocked: dict[str, float] = {}

_MAX_FETCHES_PER_MIN = 10
_MAX_GB_PER_HOUR = 5.0
_BLOCK_SECONDS = 3600


def record_fetch(ip: str) -> bool:
    """Returns False if IP should be blocked."""
    now = time.time()
    if ip in _blocked:
        if now < _blocked[ip]:
            return False
        del _blocked[ip]

    times = _fetch_times[ip]
    times[:] = [t for t in times if now - t < 60]
    if len(times) >= _MAX_FETCHES_PER_MIN:
        _blocked[ip] = now + _BLOCK_SECONDS
        return False
    times.append(now)
    return True


def record_bytes(ip: str, size: int) -> bool:
    """Returns False if IP has exceeded hourly download limit."""
    gb = size / 1e9
    _bytes_hour[ip] = _bytes_hour.get(ip, 0) + gb
    if _bytes_hour[ip] > _MAX_GB_PER_HOUR:
        return False
    return True


def is_blocked(ip: str) -> bool:
    now = time.time()
    if ip in _blocked:
        if now < _blocked[ip]:
            return True
        del _blocked[ip]
    return False


class AbuseDetectorMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        ip = request.client.host if request.client else "unknown"
        if is_blocked(ip):
            return JSONResponse(
                {"detail": "Too many requests — please try again later."},
                status_code=429,
            )
        return await call_next(request)
