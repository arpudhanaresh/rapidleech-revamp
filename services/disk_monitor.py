from __future__ import annotations
import asyncio
import os

import psutil

from config import settings

_WARNING_PCT = 90.0
_CRITICAL_PCT = 98.0

# Callbacks registered by UI components
_warning_callbacks: list = []
_critical_callbacks: list = []


def on_warning(cb) -> None:
    _warning_callbacks.append(cb)


def on_critical(cb) -> None:
    _critical_callbacks.append(cb)


_GiB = 1024 ** 3


def get_disk_usage() -> dict:
    os.makedirs(settings.DOWNLOAD_DIR, exist_ok=True)
    usage = psutil.disk_usage(settings.DOWNLOAD_DIR)
    return {
        "total_gb": round(usage.total / _GiB, 2),
        "used_gb":  round(usage.used  / _GiB, 2),
        "free_gb":  round(usage.free  / _GiB, 2),
        "percent":  usage.percent,
    }


def estimate_fits(size_bytes: int) -> bool:
    free = psutil.disk_usage(settings.DOWNLOAD_DIR).free
    return size_bytes < free * 0.9


async def poll() -> None:
    prev_state = "ok"
    while True:
        try:
            d = get_disk_usage()
            pct = d["percent"]
            if pct >= _CRITICAL_PCT and prev_state != "critical":
                prev_state = "critical"
                for cb in _critical_callbacks:
                    try:
                        cb(d)
                    except Exception:
                        pass
            elif pct >= _WARNING_PCT and prev_state == "ok":
                prev_state = "warning"
                for cb in _warning_callbacks:
                    try:
                        cb(d)
                    except Exception:
                        pass
            elif pct < _WARNING_PCT:
                prev_state = "ok"
        except Exception:
            pass
        await asyncio.sleep(5)
