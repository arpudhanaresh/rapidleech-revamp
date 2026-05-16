from __future__ import annotations


def fmt_eta(seconds: int) -> str:
    if seconds <= 0:
        return "N/A"
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        m, s = divmod(seconds, 60)
        return f"{m}m {s}s"
    h, rem = divmod(seconds, 3600)
    m = rem // 60
    return f"{h}h {m}m"
