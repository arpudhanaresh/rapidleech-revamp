# RapidLeech-Py — Agent Build Instructions

## Project Overview
A zero-friction, open-access file fetching tool. Anyone pastes any link — HTTP, magnet, `.torrent`, YouTube, social media playlist, HLS stream — and the server downloads it at maximum speed. Users download the result from the server.

**Design philosophy**: Instant, fast, open, safe.
- Open to anyone — no login, no signup, just paste and go.
- Ultra-fast engine — multi-connection, parallel chunking, mirror discovery.
- Live everything — real-time speed graphs, disk gauges, instant progress, peer maps.
- Security first — SSRF protection, rate limiting, abuse detection, virus scan, sandboxed file serving.

**Stack: NiceGUI + FastAPI (single Python process)**
- NiceGUI embeds FastAPI; one `python main.py` starts everything at `http://localhost:8000`.
- Quasar (Vue.js) UI served directly from Python — no Node.js, no second server.
- Real-time updates via NiceGUI's built-in WebSocket.

---

## Project Structure

```
rapidleech-py/
├── main.py
├── requirements.txt
├── .env
├── components/
│   ├── beans/
│   │   ├── badge.py                  # Status pill
│   │   ├── progress_bar.py           # Animated chunked progress bar
│   │   ├── stat_chip.py              # Speed / ETA / Size chip
│   │   ├── speed_graph.py            # Mini sparkline speed history chart
│   │   ├── disk_gauge.py             # Circular disk usage gauge
│   │   └── empty_state.py
│   ├── layout/
│   │   ├── header.py                 # App bar: name + disk gauge + system stats
│   │   └── footer.py
│   ├── fetch/
│   │   ├── url_input.py              # Main paste-and-go input
│   │   ├── torrent_file_picker.py    # Modal: torrent file tree with checkboxes
│   │   └── fetch_section.py
│   ├── jobs/
│   │   ├── job_card.py               # Live card: HTTP + torrent variants
│   │   ├── job_list.py
│   │   ├── torrent_peer_table.py     # Expandable peer list
│   │   └── chunk_map.py              # Visual segment map (16 blocks per file)
│   ├── files/
│   │   ├── file_manager.py
│   │   ├── file_table.py
│   │   └── file_row.py
│   └── dashboard/
│       ├── stats_bar.py              # Total downloaded, active jobs, avg speed
│       ├── disk_panel.py             # Disk usage breakdown + warning thresholds
│       └── activity_log.py           # Live scrolling log of events
├── pages/
│   ├── home.py                       # Main page
│   └── settings.py                   # Concurrency, bandwidth, security config
├── services/
│   ├── job_manager.py                # In-memory live jobs + SQLite persistence
│   ├── db.py                         # aiosqlite connection + schema init
│   ├── stats_service.py              # Combines psutil + SQLite aggregates + live jobs
│   ├── downloader.py                 # Dispatch: aria2 → yt-dlp → httpx chunked
│   ├── torrent_service.py            # libtorrent session + alert loop
│   ├── accelerator.py                # Chunk splitter, mirror finder, CDN detector
│   ├── file_service.py               # List, delete, hash, zip, share links
│   ├── disk_monitor.py               # Disk space polling + threshold events
│   ├── security.py                   # SSRF guard, URL validator, abuse detector
│   ├── scanner.py                    # ClamAV virus scan on completed files
│   ├── cleanup.py                    # Auto-delete files older than FILE_TTL_HOURS (default 5h)
│   └── scheduler.py                  # APScheduler: scheduled + cron downloads + cleanup job
├── routers/
│   ├── fetch.py                      # POST /api/fetch, GET /api/jobs
│   ├── files.py                      # File listing, download, delete, zip
│   ├── torrent.py                    # .torrent upload, file picker, peer list
│   ├── share.py                      # Signed share links
│   ├── stats.py                      # GET /api/stats — live system metrics
│   └── health.py                     # /health, /metrics (Prometheus)
├── middleware/
│   ├── rate_limiter.py               # slowapi per-IP limits
│   ├── security_headers.py           # CSP, HSTS, X-Frame-Options, etc.
│   └── abuse_detector.py             # Pattern detection, auto-block
├── models.py
├── data/
│   └── rapidleech.db                 # SQLite — jobs history, aggregate stats, activity log
└── downloads/
```

---

## `requirements.txt`
```
nicegui[uvicorn]
yt-dlp
aiofiles
aiosqlite              # SQLite async driver (default DB)
sqlalchemy[asyncio]    # DB abstraction — works with SQLite, PostgreSQL, MySQL
asyncpg                # PostgreSQL async driver (used when DATABASE_URL=postgresql+asyncpg://...)
aiomysql               # MySQL/MariaDB async driver (used when DATABASE_URL=mysql+aiomysql://...)
greenlet               # required by SQLAlchemy async
aria2p
APScheduler
pydantic-settings
httpx[http2]           # HTTP/2 support for faster connections
libtorrent             # pip install lbry-libtorrent
python-multipart
slowapi
prometheus-fastapi-instrumentator
clamd                  # ClamAV Python client
itsdangerous           # Signed share link tokens
psutil                 # Disk + system stats
humanize               # Human-readable sizes / durations
validators             # URL + IP validation
ipaddress              # SSRF IP range checks
```

---

## `models.py`

```python
from pydantic import BaseModel
from typing import Optional, Literal
from dataclasses import dataclass, field
from datetime import datetime

JobStatus = Literal["queued", "metadata", "downloading", "paused", "done", "error", "scanning"]
JobType   = Literal["http", "torrent", "ytdlp"]

@dataclass
class ChunkInfo:
    index: int          # 0-15
    start: int          # byte offset
    end: int            # byte offset
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
    speed_history: list[float] = field(default_factory=list)  # last 30 samples (MB/s)
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
    scan_result: Optional[str] = None     # "clean" | "infected:{name}" | "skipped"
    # meta
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    finished_at: Optional[str] = None
    ip_origin: Optional[str] = None       # requester IP for audit

class FetchRequest(BaseModel):
    url: str
    scheduled_at: Optional[str] = None
    torrent_file_indices: Optional[list[int]] = None
    max_connections: int = 16             # overridable per-job

class FileItem(BaseModel):
    filename: str
    size_mb: float
    size_bytes: int
    created_at: str
    sha256: Optional[str] = None
    scan_result: Optional[str] = None
    share_token: Optional[str] = None

class SystemStats(BaseModel):
    disk_total_gb: float
    disk_used_gb: float
    disk_free_gb: float
    disk_percent: float
    active_jobs: int
    queued_jobs: int
    total_downloaded_gb: float
    current_speed_mbps: float
    jobs_today: int
```

---

## Database (`services/db.py`)

Supports **local SQLite** (default, zero config) or any **remote database** the user provides via `DATABASE_URL` in `.env`.

| Config | Driver | Example `DATABASE_URL` |
|---|---|---|
| *(not set)* | `aiosqlite` (SQLite) | auto: `sqlite+aiosqlite:///data/rapidleech.db` |
| PostgreSQL | `asyncpg` | `postgresql+asyncpg://user:pass@host:5432/dbname` |
| MySQL / MariaDB | `aiomysql` | `mysql+aiomysql://user:pass@host:3306/dbname` |

Uses **SQLAlchemy async** (`sqlalchemy[asyncio]`) as the abstraction — same query code regardless of backend.

```python
# config.py reads DATABASE_URL from .env
# db.py creates the engine accordingly:

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from config import settings

DATABASE_URL = settings.DATABASE_URL or "sqlite+aiosqlite:///data/rapidleech.db"
engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
```

One file: `data/rapidleech.db` for SQLite.

### Schema

```sql
-- Completed and failed job records (written once on finish)
CREATE TABLE IF NOT EXISTS jobs (
    job_id       TEXT PRIMARY KEY,
    url          TEXT NOT NULL,
    job_type     TEXT NOT NULL DEFAULT 'http',   -- http | torrent | ytdlp
    status       TEXT NOT NULL,                  -- done | error
    filename     TEXT,
    size_bytes   INTEGER DEFAULT 0,
    scan_result  TEXT,
    error        TEXT,
    ip_origin    TEXT,
    created_at   TEXT NOT NULL,
    finished_at  TEXT
);

-- Single-row aggregate counters, updated atomically on each job completion
CREATE TABLE IF NOT EXISTS stats (
    id                      INTEGER PRIMARY KEY CHECK (id = 1),
    total_downloaded_bytes  INTEGER DEFAULT 0,
    total_jobs_completed    INTEGER DEFAULT 0,
    total_jobs_failed       INTEGER DEFAULT 0,
    last_reset_at           TEXT
);
INSERT OR IGNORE INTO stats (id) VALUES (1);

-- Activity log (trimmed to last 1000 rows automatically)
CREATE TABLE IF NOT EXISTS activity_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    level     TEXT NOT NULL,   -- info | warn | error | security | done
    message   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_activity_log_ts ON activity_log (ts DESC);
```

### Access pattern

```python
# db.py exposes:
async def get_db() -> aiosqlite.Connection   # returns shared connection (opened once at startup)
async def init_db() -> None                  # run CREATE TABLE IF NOT EXISTS on startup
async def close_db() -> None                 # called on shutdown

# Helpers used by job_manager and stats_service:
async def insert_job(job: Job) -> None
async def get_job_history(status, q, page, page_size) -> list[dict]
async def increment_stats(size_bytes: int, success: bool) -> None
async def get_aggregate_stats() -> dict              # total_downloaded_bytes, counts
async def insert_log(level: str, message: str) -> None
async def get_recent_logs(limit: int = 200) -> list[dict]
async def trim_logs() -> None                        # keep only latest 1000 rows
```

---

## `services/job_manager.py` — Two-layer design

**Rule: in-memory for live data, SQLite for permanent record.**

```
         ┌─────────────────────────────┐
         │   In-memory  jobs: dict     │  ← updated hundreds of times/sec during download
         │  (percent, speed, chunks,   │    (speed_history, peer list, chunk map, ETA)
         │   peers, torrent files …)   │
         └────────────┬────────────────┘
                      │ on status → "done" or "error"
                      ▼
         ┌─────────────────────────────┐
         │   SQLite  jobs  table       │  ← written once, never updated
         │  (job_id, url, filename,    │    survives restart, queryable, exportable
         │   size_bytes, finished_at …)│
         └─────────────────────────────┘
```

```python
# In-memory store for active/queued jobs
_live: dict[str, Job] = {}

async def create_job(job_id, url, ip) -> Job:
    job = Job(job_id=job_id, url=url, ip_origin=ip)
    _live[job_id] = job
    return job

async def finish_job(job_id: str) -> None:
    job = _live.get(job_id)
    if job and job.status in ("done", "error"):
        await db.insert_job(job)                           # write to SQLite
        await db.increment_stats(job.size_bytes, job.status == "done")
        # keep in _live for UI display until user navigates away or next restart

async def list_live_jobs() -> list[Job]:
    return list(_live.values())                            # for the active job list UI

async def list_history(status, q, page) -> list[dict]:
    return await db.get_job_history(status, q, page)       # from SQLite
```

---

## `services/stats_service.py`

Single source of truth for `SystemStats`. Combines live in-memory data with SQLite aggregates and `psutil`.

```python
async def get_system_stats() -> SystemStats:
    disk   = psutil.disk_usage(settings.DOWNLOAD_DIR)
    agg    = await db.get_aggregate_stats()          # from SQLite: total_downloaded_bytes, counts
    live   = job_manager.list_live_jobs()

    active = [j for j in live if j.status == "downloading"]
    today  = [j for j in live if j.created_at.startswith(date.today().isoformat())]

    return SystemStats(
        disk_total_gb         = disk.total / 1e9,
        disk_used_gb          = disk.used  / 1e9,
        disk_free_gb          = disk.free  / 1e9,
        disk_percent          = disk.percent,
        active_jobs           = len(active),
        queued_jobs           = sum(1 for j in live if j.status == "queued"),
        total_downloaded_gb   = agg["total_downloaded_bytes"] / 1e9,   # from SQLite ✅
        current_speed_mbps    = sum(j.speed_mbps for j in active),
        jobs_today            = agg["jobs_today"],     # SQLite COUNT WHERE DATE(finished_at)=today
        total_jobs_completed  = agg["total_jobs_completed"],
        total_jobs_failed     = agg["total_jobs_failed"],
    )
```

---

## Download Acceleration (`services/accelerator.py`)

Goal: extract maximum speed from any server, even slow ones.

```python
async def accelerate(job_id: str, url: str, max_conn: int = 16) -> None:
    # 1. HEAD request — get Content-Length + Accept-Ranges
    # 2. If Accept-Ranges: bytes → split into max_conn chunks
    # 3. Download all chunks in parallel via asyncio.gather (httpx async)
    # 4. Reassemble in order as chunks complete
    # 5. If no Accept-Ranges → fall back to single stream

async def _chunk_download(job_id: str, url: str, chunk: ChunkInfo, session: httpx.AsyncClient):
    # Range: bytes={start}-{end}
    # Stream response into partial file: downloads/{job_id}.part{index}
    # Update chunk.downloaded on each chunk; job_manager.update_job(speed, percent)

async def find_mirrors(url: str) -> list[str]:
    # Check common CDN patterns + Wayback Machine / archive.org mirrors
    # Return list of equivalent URLs to use as fallback if primary is slow

def detect_cdn(url: str) -> str | None:
    # Identify Cloudflare, Fastly, Akamai, CloudFront from headers
    # Return CDN name for logging

async def resolve_fastest_mirror(mirrors: list[str]) -> str:
    # Ping all mirrors in parallel; return fastest responding URL
```

**Speed techniques applied:**
| Technique | Implementation |
|---|---|
| Parallel chunking | 16 simultaneous Range requests via `httpx.AsyncClient` |
| Aria2c acceleration | `aria2p` with `-x16 -s16 -k1M` for HTTP(S) links |
| Mirror fallback | archive.org / CDN mirrors tried if primary is slow |
| HTTP/2 multiplexing | `httpx[http2]` — single connection, multiple streams |
| DNS prefetch | Resolve hostname once, reuse across all chunk connections |
| Connection pooling | `httpx.AsyncClient(limits=Limits(max_connections=32))` |
| Keep-alive | Persistent connections reused across chunks |
| Adaptive retry | Exponential backoff per chunk on timeout/error |
| Resume support | `.part` files preserved; incomplete chunks re-requested |
| IPv6 preference | Try AAAA record first; fall back to A |

---

## `services/downloader.py`

```python
async def dispatch(job_id: str, url: str, max_conn: int = 16,
                   torrent_indices: list[int] | None = None) -> None:

    url = await security.validate_and_resolve(url)   # SSRF check first — raises on violation

    if is_torrent(url):
        await torrent_service.start(job_id, url, torrent_indices)
    elif is_media_url(url):           # yt-dlp handles: YouTube, Twitter, TikTok, etc.
        await _ytdlp_download(job_id, url)
    elif aria2_available():
        await _aria2_download(job_id, url, max_conn)
    else:
        await accelerator.accelerate(job_id, url, max_conn)

async def _aria2_download(job_id: str, url: str, max_conn: int) -> None:
    # aria2p.Client — add URI with options:
    # { 'max-connection-per-server': max_conn, 'split': max_conn,
    #   'min-split-size': '1M', 'continue': 'true', 'dir': DOWNLOAD_DIR }
    # Poll GID status every 500ms → update job speed, percent, eta, connections

async def _ytdlp_download(job_id: str, url: str) -> None:
    # asyncio.to_thread — yt-dlp options:
    # { 'format': 'bestvideo+bestaudio/best', 'subtitleslangs': ['en'],
    #   'writesubtitles': True, 'concurrent_fragment_downloads': 8 }
    # Progress hook → update job

async def _post_download(job_id: str, filepath: str) -> None:
    # 1. Compute SHA-256 hash
    # 2. scanner.scan(filepath) → update scan_result
    # 3. disk_monitor.check_threshold() → emit warning if low
    # 4. job_manager.update_job(status="done", finished_at=now)
```

---

## `services/torrent_service.py`

```python
def get_session() -> lt.session:
    # DHT, LSD, UPnP, NAT-PMP enabled
    # Peer exchange ON; anonymous_mode OFF (configurable)
    # Max upload slots: 4 (seeder-friendly default)

async def fetch_metadata(url: str) -> list[TorrentFile]:
    # magnet: → add with flag_upload_mode, wait for metadata_received_alert
    # .torrent URL → httpx download → lt.torrent_info parse
    # Returns file tree for picker

async def start(job_id: str, url: str, selected_indices: list[int] | None) -> None:
    # Set file priorities (4=normal, 0=skip for unselected)
    # Alerts loop in asyncio.to_thread:
    #   state_changed_alert, piece_finished_alert, file_completed_alert,
    #   torrent_finished_alert, torrent_error_alert, peer_connect_alert
    # Every 1s: read handle.status() → speed, percent, seeds, peers, ratio
    # On finish: _post_download()

def get_peers(job_id: str) -> list[dict]:
    # handle.get_peer_info() → [{ ip, client, dl_speed, progress, flags }]
```

---

## Security (`services/security.py`)

This is the most critical layer. The app is open to everyone — so the server must never be weaponised.

```python
# SSRF protection — block ALL private / internal addresses
BLOCKED_NETWORKS = [
    "127.0.0.0/8",     # loopback
    "10.0.0.0/8",      # private
    "172.16.0.0/12",   # private
    "192.168.0.0/16",  # private
    "169.254.0.0/16",  # link-local (AWS/GCP metadata: 169.254.169.254)
    "::1/128",         # IPv6 loopback
    "fc00::/7",        # IPv6 private
]
BLOCKED_HOSTS = {"localhost", "metadata.google.internal", "169.254.169.254"}

async def validate_and_resolve(url: str) -> str:
    # 1. Parse URL — scheme must be http / https / magnet (reject file://, ftp://, etc.)
    # 2. DNS resolve hostname → check ALL resolved IPs against BLOCKED_NETWORKS
    # 3. Follow redirects manually (re-check each hop's IP)
    # 4. Reject URLs > 2048 chars
    # 5. Sanitize filename from Content-Disposition (no path separators, no null bytes)
    # Raises SecurityViolation on any failure — logged + IP flagged

def sanitize_filename(name: str) -> str:
    # Remove path separators, null bytes, control chars
    # Trim to 255 chars
    # Replace spaces with underscores
    # Never allow: ../, ./, absolute paths

# Rate limits (per IP, enforced by middleware/rate_limiter.py via slowapi)
LIMITS = {
    "POST /api/fetch":       "5/minute, 20/hour",
    "POST /api/torrent/upload": "10/hour",
    "GET /api/files/download": "30/minute",
}

# Abuse detection (middleware/abuse_detector.py)
# - Same IP submits >10 URLs in 1 min → temporary 1h block
# - Same IP downloads >5GB in 1h → throttle to 1 Mbps
# - URL pattern match against known abuse patterns → reject + log
# - Admin can manually ban IPs via settings page
```

### Security headers (`middleware/security_headers.py`)
```
Content-Security-Policy: default-src 'self'; script-src 'self' 'nonce-{random}'
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
Referrer-Policy: no-referrer
Permissions-Policy: geolocation=(), microphone=(), camera=()
Strict-Transport-Security: max-age=31536000; includeSubDomains  (HTTPS only)
```

---

## `services/cleanup.py`

Auto-deletes files in `DOWNLOAD_DIR` that are older than `FILE_TTL_HOURS` (default **5 hours**).
Runs as a background APScheduler job every 15 minutes.

```python
import os, asyncio
from datetime import datetime, timedelta, timezone
from services import file_service
from services.activity_log_bus import emit   # pushes event to UI activity log
from config import settings                  # FILE_TTL_HOURS from .env

async def run_cleanup() -> None:
    threshold = datetime.now(timezone.utc) - timedelta(hours=settings.FILE_TTL_HOURS)
    deleted: list[str] = []

    for entry in os.scandir(settings.DOWNLOAD_DIR):
        if not entry.is_file():
            continue
        mtime = datetime.fromtimestamp(entry.stat().st_mtime, tz=timezone.utc)
        if mtime < threshold:
            try:
                os.remove(entry.path)
                deleted.append(entry.name)
                emit("info", f"Auto-deleted {entry.name} (age > {settings.FILE_TTL_HOURS}h)")
            except OSError as e:
                emit("warn", f"Failed to delete {entry.name}: {e}")

    if deleted:
        emit("info", f"Cleanup complete — {len(deleted)} file(s) removed")

def schedule_cleanup(scheduler) -> None:
    # Register as an interval job: every 15 minutes
    scheduler.add_job(
        lambda: asyncio.create_task(run_cleanup()),
        trigger="interval",
        minutes=15,
        id="auto_cleanup",
        replace_existing=True,
    )
```

**Behaviour:**
- Uses `mtime` (last modified time) — the moment the download finished writing.
- Files currently being downloaded (status ≠ done) are never touched — only files in `DOWNLOAD_DIR` whose job status is `done`.
- TTL is configurable: set `FILE_TTL_HOURS=5` in `.env` (default). `0` disables auto-delete entirely.
- Every deletion is logged to the UI activity log in real time.
- Runs every 15 minutes — worst case a file lives 5h 14m, never more.

---

## `services/disk_monitor.py`

```python
async def poll() -> SystemStats:
    # psutil.disk_usage(DOWNLOAD_DIR) every 5s
    # Emit nicegui event if free < WARNING_THRESHOLD (default 10%)
    # Emit nicegui event if free < CRITICAL_THRESHOLD (default 2%) → pause all queued jobs

def get_stats() -> SystemStats:
    # Returns current disk + job stats snapshot for UI

def estimate_fits(size_bytes: int) -> bool:
    # Returns True if size_bytes < disk_free * 0.9
```

---

## `routers/stats.py`

```
GET /api/stats
  - Returns SystemStats: disk_total_gb, disk_used_gb, disk_free_gb, disk_percent,
    active_jobs, queued_jobs, total_downloaded_gb, current_speed_mbps, jobs_today
  - Called by header stats bar every 2s via ui.timer

GET /api/jobs/{job_id}/speed-history
  - Returns last 30 speed samples for the sparkline graph
```

---

## Component Architecture

### Beans (`components/beans/`)

#### `beans/badge.py`
```python
STATUS_COLORS = {
    "queued": "grey", "metadata": "blue", "downloading": "amber",
    "paused": "orange", "scanning": "purple", "done": "positive", "error": "negative"
}
# Quasar rounded pill badge with icon:
# queued=schedule, metadata=search, downloading=download,
# paused=pause, scanning=security, done=check_circle, error=error
```

#### `beans/progress_bar.py`
```python
def progress_bar(percent: float, chunks: list[ChunkInfo], active: bool) -> None:
    # Main bar: Quasar linear-progress, color='cyan', animated
    # Below: 16-segment chunk map (each block = one Range request)
    #   green=done, cyan=active, dark=pending
    # CSS shimmer overlay when active=True
    # Smooth CSS transition: width 300ms cubic-bezier(0.4, 0, 0.2, 1)
```

#### `beans/speed_graph.py`
```python
def speed_graph(history: list[float]) -> None:
    # Last 30 speed samples plotted as a mini sparkline
    # Using ui.echart (ECharts) — area chart, no axes, cyan fill
    # Updates every 500ms via job timer
    # Shows current speed as label overlay
```

#### `beans/disk_gauge.py`
```python
def disk_gauge(stats: SystemStats) -> None:
    # Quasar circular progress ring
    # Color: green < 70%, amber 70-90%, red > 90%
    # Center text: "X.X GB free"
    # Pulses red animation when critical (< 2% free)
    # Tooltip: total / used / free breakdown
```

#### `beans/stat_chip.py`
- Quasar chip, outlined, monospace label+value pair

---

### Layout (`components/layout/`)

#### `layout/header.py`
```python
def header() -> None:
    # ui.header, sticky, q-elevation-4, dark bg
    # Left:  "⚡ RapidLeech-Py" in cyan monospace + tagline
    # Center: stats_bar (active jobs / total speed / jobs today)
    # Right:  disk_gauge + dark/light toggle + settings gear icon
    # disk_gauge pulses red if disk critical
    # Header itself subtly glows cyan while any job is downloading
```

---

### Dashboard (`components/dashboard/`)

#### `dashboard/stats_bar.py`
```python
def stats_bar() -> None:
    # Row of animated counters updated every 1s:
    # [⬇ 3 Active]  [⚡ 24.6 MB/s Total]  [✅ 47 Today]  [💾 12.3 GB Downloaded]
    # Numbers count up/down with CSS transition
    # Each counter glows on change
```

#### `dashboard/disk_panel.py`
```python
def disk_panel() -> None:
    # Expanded disk view (shown in settings page or collapsible in home):
    # Stacked bar: used (dark) / downloads (cyan) / free (green)
    # Warning banner when < 10% free: "Low disk space — X GB remaining"
    # Critical banner when < 2% free: "CRITICAL — downloads paused"
    # Estimated space needed for current queue
    # "Clean old files" quick action button
```

#### `dashboard/activity_log.py`
```python
def activity_log() -> None:
    # Scrolling log of recent events, max 200 lines, dark terminal style
    # Color-coded: cyan=started, green=done, red=error, yellow=warning, white=info
    # Each line: [HH:MM:SS] [LEVEL] message
    # Examples:
    #   [14:32:01] [INFO]  Job abc123 started — https://example.com/file.zip
    #   [14:32:45] [DONE]  Job abc123 complete — file.zip (234 MB, 18.2 MB/s avg)
    #   [14:33:00] [WARN]  Disk usage at 87% — 13.2 GB remaining
    #   [14:33:01] [SEC]   Blocked SSRF attempt from 192.168.1.5 → 10.0.0.1
    # Toggle show/hide via collapsible card
```

---

### Fetch (`components/fetch/`)

#### `fetch/url_input.py`
```python
def url_input(on_submit: Callable) -> None:
    # Large monospace input, full-width, cyan focus ring, placeholder: "Paste any link..."
    # Auto-detects on paste:
    #   - magnet: URI → shows magnet icon chip
    #   - .torrent URL → shows torrent icon chip
    #   - youtube.com / youtu.be / twitter / tiktok / instagram → shows platform icon chip
    #   - direct file URL → shows file extension chip
    # "⚡ Fetch" button — loading spinner while submitting
    # Enter key submits
    # Paste multiple URLs (newline-separated) → submits each as separate job
    # ui.upload (paperclip icon) → accepts .torrent files
    # After submit: animate button → "Added to queue!" → reset after 1.5s
    # Disk space check before submit: warn if estimated file > available space
```

#### `fetch/torrent_file_picker.py`
```python
def torrent_file_picker(job_id: str, files: list[TorrentFile], on_confirm: Callable) -> None:
    # ui.dialog (modal, wide)
    # Header: torrent name + total size
    # File tree with:
    #   - Checkbox per file (all checked by default)
    #   - File path (monospace, truncated)
    #   - Size badge
    #   - File type icon
    # "Select All" / "Deselect All" / "Videos Only" / "Audio Only" quick filters
    # Footer: "X files selected — Y GB total" (updates live as boxes checked)
    # "Start Download" button → POST /api/torrent/{job_id}/start
```

---

### Jobs (`components/jobs/`)

#### `jobs/job_card.py`
```python
def job_card(job: Job) -> None:
    # ui.card, dark bg, animated border:
    #   - downloading: pulsing cyan border glow
    #   - done: solid green border
    #   - error: solid red border
    #   - paused: dashed orange border
    #
    # Row 1: [Type chip] [truncated URL] [badge(status)] [connections count]
    # Row 2: progress_bar(percent, chunks, active)
    # Row 3: speed_graph(speed_history) + stat_chips (Speed | ETA | Size | Downloaded)
    #
    # TORRENT EXTRA rows:
    # Row 4: stat_chips (Seeders | Peers | Leechers | Ratio)
    # Row 5: per-file progress list (collapsible, each file has mini bar)
    # Row 6: [▶ Details] → torrent_peer_table (expandable)
    #
    # Controls row:
    #   - [⏸ Pause] / [▶ Resume] toggle
    #   - [✕ Cancel]
    #   - On done: [⬇ Download] + [🔗 Share Link] + [📋 Copy SHA256]
    #   - scan_result badge: ✅ Clean / ⚠ Infected / ⏳ Scanning
    #
    # Card entrance: Quasar transition scale+fade from top
    # ui.timer(0.5, refresh) while status in (queued, metadata, downloading, scanning)
    # Timer auto-stops on done/error

def job_card_skeleton() -> None:
    # Quasar skeleton placeholder — shown while job metadata is loading
    # Animated shimmer, same dimensions as real card
```

#### `jobs/chunk_map.py`
```python
def chunk_map(chunks: list[ChunkInfo]) -> None:
    # 16 small colored blocks in a row
    # done=cyan, active=pulsing cyan, pending=dark grey
    # Tooltip on hover: "Chunk 4 — 12.4 MB / 16.0 MB (77%)"
    # Shown inside progress_bar for HTTP chunked jobs
```

#### `jobs/torrent_peer_table.py`
```python
def torrent_peer_table(job_id: str) -> None:
    # ui.table: IP | Client | ⬇ Speed | Progress | Flags | Country flag
    # Refreshes every 2s via ui.timer
    # Country flag derived from IP (offline GeoIP database: geoip2)
    # Flags: D=interested, C=choked, H=handshake, E=encrypted
    # Max 50 rows (sorted by dl speed desc)
```

---

### Files (`components/files/`)

#### `files/file_table.py`
```python
def file_table(files: list[FileItem], on_delete, on_share) -> None:
    # ui.table, sortable columns, dark themed, striped rows
    # Columns: [Icon] Name | Size | Downloaded At | Expires In | Scan | Actions
    #
    # "Expires In" column:
    #   - Counts down from FILE_TTL_HOURS using file mtime
    #   - > 1h remaining  → green  "3h 45m"
    #   - 30m–1h          → amber  "47m"
    #   - < 30m remaining → red pulsing  "⚠ 12m"
    #   - TTL disabled    → muted "—"
    #   - Updated every 60s via ui.timer
    #
    # Scan badge: ✅ Clean / ⚠️ Infected / ⏳ Scanning
    # Actions per row:
    #   [⬇ Download]  [🔗 Share]  [📋 SHA256]  [🗑 Delete]
    # Multi-select checkboxes → bulk delete or bulk ZIP
    # Search bar at top filters by filename
    # Sort by: name / size / date / expiry
```

#### `files/file_manager.py`
```python
def file_manager() -> None:
    # Section heading + search + sort controls
    # disk_panel (collapsible)
    # file_table or empty_state
    # Footer: total files count + total size
    # "⬇ Download All as ZIP" button (streams ZIP)
    # Auto-refreshes when jobs complete (triggerRefresh prop)
    # Infinite scroll for large file lists
```

---

### Pages (`pages/`)

#### `pages/home.py`
```python
def home_page() -> None:
    layout.header()
    with ui.column().classes('w-full max-w-6xl mx-auto px-4 gap-6 pt-20'):
        stats_bar()
        fetch_section(on_job_added=...)
        job_list(jobs=job_manager.list_jobs())
        file_manager()
        activity_log()
    layout.footer()
```

#### `pages/settings.py`
```python
def settings_page() -> None:
    # Download settings:
    #   Max concurrent downloads (slider 1-10)
    #   Default connections per job (slider 1-32, default 16)
    #   Bandwidth cap in MB/s (0 = unlimited)
    #   Download directory (path input)
    #   Max file size (GB, 0 = unlimited)
    #   Auto-delete after N hours (ui.number, default 5, 0 = disabled)
    #     → shows countdown per file in file_table: "Expires in 3h 12m"
    #     → warning badge on files with < 30 min remaining
    #
    # Torrent settings:
    #   Enable seeding (toggle)
    #   Target ratio before removing (0.5 / 1.0 / 2.0 / unlimited)
    #   Max upload speed (MB/s)
    #   DHT / LSD / UPnP toggles
    #
    # Security settings:
    #   Rate limit per IP (downloads/hour)
    #   Max file size per download (GB)
    #   Blocked extensions (comma list)
    #   Blocked domains (textarea)
    #   ClamAV scan toggle
    #
    # System:
    #   disk_panel (always visible here)
    #   Theme toggle
    #   Prometheus metrics toggle
    #   Reset all settings button
```

---

## `main.py`

```python
from nicegui import ui, app
from pages.home import home_page
from pages.settings import settings_page
from routers import fetch, files, torrent, share, stats, health
from middleware.rate_limiter import setup_rate_limiter
from middleware.security_headers import add_security_headers
from services.disk_monitor import poll as disk_poll

# Mount FastAPI routers
for router in [fetch.router, files.router, torrent.router, share.router, stats.router, health.router]:
    app.include_router(router, prefix="/api" if router != share.router else "")

setup_rate_limiter(app)
add_security_headers(app)

@app.on_startup
async def startup():
    await db.init_db()                      # create tables if not exist, open connection
    asyncio.create_task(disk_poll())        # background disk monitor
    cleanup.schedule_cleanup(scheduler)     # auto-delete files older than FILE_TTL_HOURS
    scheduler.start()

@app.on_shutdown
async def shutdown():
    await db.close_db()

@ui.page('/')
def index():
    home_page()

@ui.page('/settings')
def settings():
    settings_page()

ui.run(
    title="RapidLeech-Py",
    dark=True,
    port=8000,
    favicon="⚡",
    uvicorn_reload=True,
    storage_secret=settings.SECRET_KEY,
)
```

---

## UI Design Guidelines

- **Theme**: Dark — `bg-[#0f0f0f]`, cards `bg-[#1a1a1a]`, accent cyan `#00e5ff`
- **Font**: JetBrains Mono via Google Fonts in head — monospace everywhere for a terminal feel
- **Animations**:
  - Card mount: `q-transition--scale` + fade, 200ms
  - Progress bar: CSS `transition: width 300ms ease-in-out`
  - Shimmer: `@keyframes shimmer` — diagonal gradient sweep on active bars
  - Chunk blocks: pulse `@keyframes pulse-cyan` on active chunks
  - Numbers (speed, size): `countUp.js` via `ui.run_javascript()` on change
  - Header glow: `box-shadow: 0 0 20px #00e5ff33` while any job is active
  - Disk gauge: pulse red `@keyframes pulse-red` when critical
  - New job card slides in from top: `animate__slideInDown` (Animate.css in head)
  - Completed card: border flashes green × 2 then settles
- **Responsive**: Quasar grid — single column on mobile, 2-col on tablet, max-width 1200px desktop
- **Micro-interactions**: button ripple, hover glow on cards, tooltip on all icons

---

## Running the Project

```bash
pip install -r requirements.txt

# Optional: install aria2c system-wide for max speed
# Ubuntu: sudo apt install aria2
# macOS:  brew install aria2
# Windows: choco install aria2

# Optional: ClamAV for virus scanning
# Ubuntu: sudo apt install clamav && freshclam

python main.py
# Everything at http://localhost:8000
```

---

## Key Behaviors

1. **Ultra-fast downloads** — aria2c `-x16 -s16` first; fallback to httpx 16-chunk parallel; yt-dlp for media
2. **Single process** — NiceGUI embeds FastAPI; no second terminal, no Node.js
3. **SSRF protection** — every URL DNS-resolved and checked against blocked networks before any connection
4. **Live everything** — `ui.timer(0.5)` per job card; disk stats every 2s; speed sparkline every 500ms
5. **Non-blocking** — all downloads in `asyncio.to_thread()`; alert loop in dedicated thread for torrents
6. **Strict sanitization** — filenames sanitized, no path traversal, no code execution of downloaded files
7. **Graceful shutdown** — cancel active downloads, flush partial files on SIGINT

---

## Optional Enhancements (quick wins)

- [ ] Clipboard auto-detect on page focus → pre-fill input
- [ ] `ui.notify` toast on job done / error
- [ ] Copy SHA256 to clipboard button
- [ ] API key header (`X-API-Key`) for external integrations

---

## Advanced Features (Open Source Roadmap)

### Download Engine

| Feature | Details |
|---|---|
| **Torrent seeding** | After download, seed to target ratio; configurable in Settings |
| **RSS torrent feed** | Subscribe to RSS URL; auto-download matching new entries |
| **Sequential torrent** | Force piece order for streaming video before full download |
| **Mirror download** | Detect CDN mirrors; download same file from N sources simultaneously |
| **Adaptive connections** | Auto-increase chunk count if server is slow (detect stall → add connection) |
| **Post-processing** | FFmpeg remux, audio extract, thumbnail generation after download |
| **Auto-extract** | Unzip / unrar to subfolder; delete archive after extraction |
| **Playlist / batch** | yt-dlp playlist support; `.txt` file of URLs drag-dropped onto input |
| **HLS/DASH streams** | yt-dlp handles m3u8/mpd; download and remux to MP4 |
| **Subtitle download** | Auto-fetch `.srt` / `.vtt` subtitles alongside video via yt-dlp |

### Storage & Files

| Feature | Details |
|---|---|
| **Cloud push** | S3-compatible / Google Drive / Dropbox upload after download |
| **Named buckets** | Configurable target dirs by URL pattern (videos/, audio/, docs/) |
| **Deduplication** | SHA-256 check on finish; reject or hard-link duplicates |
| **Expiry + cleanup** | ✅ Core — `FILE_TTL_HOURS=5` default; cleanup job runs every 15 min; countdown shown per file |
| **Signed share links** | `/share/{token}` — time-limited, single-use or multi-use |
| **Batch ZIP** | Select files in table → on-the-fly ZIP stream |
| **GeoIP peer flags** | Offline MaxMind GeoLite2 DB — country flag per torrent peer |

### API & Backend

| Feature | Details |
|---|---|
| **Persistent DB** | ✅ Core — `aiosqlite`, schema in `services/db.py`, init on startup |
| **Job history & search** | `GET /api/jobs?status=done&q=filename&page=1&sort=size` — queries SQLite |
| **Webhooks** | POST to user URL on job done / error with full job payload |
| **Scheduled downloads** | APScheduler — ISO datetime or cron per job; UI date-time picker |
| **Prometheus metrics** | `/metrics` — jobs, queue depth, bytes, error rate, avg speed |
| **Rate limiting tiers** | Trusted IPs get higher limits; configurable via settings |
| **OpenAPI docs** | FastAPI `/docs` — auto-generated, always current |

### Auth & Multi-User (optional overlay)

| Feature | Details |
|---|---|
| **Optional JWT** | Toggle in settings — enable auth to restrict to known users |
| **API keys** | Static keys in DB for scripted / headless use |
| **Admin console** | IP ban list, global bandwidth override, forced cleanup |
| **OAuth2** | GitHub / Google login via `authlib` |

### UI / UX

| Feature | Details |
|---|---|
| **File preview** | Image lightbox; inline HTML5 video player for MP4/WebM |
| **Keyboard shortcuts** | `Ctrl+V` paste+submit; `Ctrl+K` command palette |
| **PWA** | Installable, offline shell, browser push notifications |
| **i18n** | English default; community locale files |
| **Mobile** | Fully usable on phone — stacked layout, large touch targets |
| **Notification panel** | In-app history: done / errors / disk warnings / security events |

### Developer / Open Source

| Feature | Details |
|---|---|
| **Docker + Compose** | Single `Dockerfile`; `compose.yml` with `downloads/` volume |
| **GitHub Actions** | `ruff` + `mypy`, `pytest`, Playwright E2E on every PR |
| **One-command setup** | `make dev` — installs deps + starts server |
| **Env config** | All via `.env` + `pydantic-settings`: `DOWNLOAD_DIR`, `MAX_CONCURRENT`, `SECRET_KEY`, `CLAM_SOCKET`, `ARIA2_RPC_SECRET`, `FILE_TTL_HOURS=5` |
| **Browser extension** | Manifest V3 — right-click any link → "Fetch with RapidLeech" |
| **Changelog** | `CHANGELOG.md`, `release-please` GitHub Action |
