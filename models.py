from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Literal

JobStatus = Literal["queued", "metadata", "downloading", "paused", "hashing", "done", "error", "scanning"]
JobType   = Literal["http", "torrent", "ytdlp"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ChunkInfo:
    index: int
    start: int
    end: int
    downloaded: int = 0
    done: bool = False


@dataclass
class TorrentFile:
    index: int
    path: str
    size_mb: float
    percent: float = 0.0
    selected: bool = True


@dataclass
class Job:
    job_id: str
    url: str
    status: JobStatus = "queued"
    job_type: JobType = "http"
    # progress
    percent: float = 0.0
    speed: str = "N/A"
    speed_mbps: float = 0.0
    speed_history: list[float] = field(default_factory=list)
    eta: str = "N/A"
    size: str = "N/A"
    size_bytes: int = 0
    downloaded_bytes: int = 0
    # http chunked
    chunks: list[ChunkInfo] = field(default_factory=list)
    connections: int = 0
    # torrent
    torrent_name: Optional[str] = None
    seeders: int = 0
    peers: int = 0
    leechers: int = 0
    ratio: float = 0.0
    files: list[TorrentFile] = field(default_factory=list)
    # result
    filename: Optional[str] = None
    error: Optional[str] = None
    scan_result: Optional[str] = None
    sha256: Optional[str] = None
    # meta
    created_at: str = field(default_factory=_now)
    finished_at: Optional[str] = None
    ip_origin: Optional[str] = None

    def push_speed(self, mbps: float) -> None:
        self.speed_mbps = mbps
        self.speed = f"{mbps:.1f} MB/s"
        self.speed_history.append(round(mbps, 2))
        if len(self.speed_history) > 30:
            self.speed_history.pop(0)


@dataclass
class FileItem:
    filename: str
    size_mb: float
    size_bytes: int
    created_at: str
    sha256: Optional[str] = None
    scan_result: Optional[str] = None
    share_token: Optional[str] = None
    is_dir: bool = False
    expires_at: Optional[str] = None


@dataclass
class SystemStats:
    disk_total_gb: float = 0.0
    disk_used_gb: float = 0.0
    disk_free_gb: float = 0.0
    disk_percent: float = 0.0
    active_jobs: int = 0
    queued_jobs: int = 0
    total_downloaded_gb: float = 0.0
    current_speed_mbps: float = 0.0
    jobs_today: int = 0
    total_jobs_completed: int = 0
    total_jobs_failed: int = 0
