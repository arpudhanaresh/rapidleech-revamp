<div align="center">

# ⚡ RapidLeech-Py

**A self-hosted, real-time download manager — HTTP, Torrents, and 1000+ media sites.**  
Download files at full datacenter speed directly to your server, then pull them at any time.

[![Docker Pulls](https://img.shields.io/docker/pulls/arpudhanaresh/rapidleech?style=flat-square&logo=docker&color=2496ED)](https://hub.docker.com/r/arpudhanaresh/rapidleech)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)](LICENSE)

</div>

---

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Architecture](#architecture)
- [Quick Start](#quick-start)
- [Deployment Guides](#deployment-guides)
  - [Docker (single container)](#docker-single-container)
  - [Docker Compose](#docker-compose)
  - [AWS EC2](#aws-ec2)
  - [Bare Metal](#bare-metal)
- [Configuration](#configuration)
- [Usage Guide](#usage-guide)
  - [HTTP Downloads](#http-downloads)
  - [Torrents](#torrents)
  - [Media (yt-dlp)](#media-yt-dlp)
  - [File Manager](#file-manager)
  - [Settings](#settings)
- [API Reference](#api-reference)
- [Performance Tuning](#performance-tuning)
- [Troubleshooting](#troubleshooting)
- [Security](#security)
- [Contributing](#contributing)

---

## Overview

RapidLeech-Py is a Python rewrite of the classic RapidLeech PHP downloader. It runs as a lightweight FastAPI server with a reactive Alpine.js frontend. All downloads happen on the server side — useful when you have a fast server (e.g. AWS EC2, VPS) and want to pull files locally later.

```
Browser  ──→  RapidLeech (FastAPI)  ──→  Internet
                    │
              ┌─────┴──────┐
            aria2c     libtorrent / yt-dlp
                    │
             /app/downloads  ←─── (pull back to browser)
```

---

## Features

### Downloads
| | |
|---|---|
| **HTTP / Direct** | Multi-connection accelerator via aria2c — auto-tunes split count by file size |
| **Torrents** | Magnet links + `.torrent` files; file picker before download; stops seeding on completion |
| **Media** | 1000+ sites via yt-dlp; quality picker (360p → 4K/8K); audio-only mode |
| **Resumable** | Interrupted downloads survive server restarts (persisted pending queue) |

### UI / UX
| | |
|---|---|
| **Real-time** | Speed graph, ETA, chunk progress map, seeder/peer count — live via SSE |
| **Format picker** | YouTube/media: shows all available resolutions with file size before downloading |
| **Folder browser** | Torrent folders: browse files, download individually or as ZIP with progress bar |
| **History** | Searchable job history with status, filename, size, SHA-256 |
| **Activity log** | Timestamped server-side log (last 1000 entries) |

### Operations
| | |
|---|---|
| **Auto-cleanup** | TTL-based file deletion (default 5 h); active downloads are never touched |
| **SHA-256** | Computed automatically after every completed download |
| **ClamAV** | Optional virus scan on completion (Unix socket or TCP) |
| **Disk monitor** | Real-time free/used/total display in the header |
| **Multi-DB** | SQLite (default), PostgreSQL, MySQL |

### Security
| | |
|---|---|
| **SSRF protection** | Blocks private IPs, localhost, cloud metadata endpoints (169.254.169.254) |
| **Rate limiting** | Per-IP rate limits on fetch and download endpoints |
| **Abuse detection** | Automatic IP blocking on repeated abuse |
| **Input sanitisation** | Filenames stripped of traversal sequences and illegal characters |
| **Security headers** | HSTS, X-Frame-Options, CSP, X-Content-Type-Options |

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                      Browser (Alpine.js)                 │
│  SSE ←─ /api/events    POST /api/fetch    GET /api/files │
└──────────────────────────────┬───────────────────────────┘
                               │ HTTP
┌──────────────────────────────▼───────────────────────────┐
│                    FastAPI Application                    │
│                                                          │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────┐  │
│  │   Routers   │  │  Middleware  │  │   APScheduler  │  │
│  │ fetch files │  │ rate-limit   │  │ cleanup (5 min)│  │
│  │ torrent     │  │ abuse-detect │  │                │  │
│  │ ytdlp stats │  │ sec-headers  │  └────────────────┘  │
│  └──────┬──────┘  └──────────────┘                      │
│         │                                               │
│  ┌──────▼────────────────────────────────────────────┐  │
│  │                    Services                       │  │
│  │  downloader  │ torrent_service │ file_service     │  │
│  │  job_manager │ accelerator     │ disk_monitor     │  │
│  │  cleanup     │ stats_service   │ security         │  │
│  └──────┬───────────────┬─────────────────┬──────────┘  │
│         │               │                 │             │
│      aria2c         libtorrent          yt-dlp          │
│      (RPC)           (native)           (process)       │
│                                                         │
│  ┌─────────────────────────────────────────────────┐   │
│  │   SQLite / PostgreSQL / MySQL  (SQLAlchemy)     │   │
│  └─────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────┘
                          │
               /app/downloads  (Docker volume)
```

**Job lifecycle:**
```
queued → metadata* → downloading → hashing → done
                                          ↘ scanning* → done
                                 → error
                   * torrent only / ClamAV only
```

---

## Quick Start

```bash
docker run -d \
  --name rapidleech \
  --restart unless-stopped \
  -p 80:8000 \
  -p 6881:6881/tcp \
  -p 6881:6881/udp \
  -v rapidleech_data:/app/data \
  -v rapidleech_downloads:/app/downloads \
  arpudhanaresh/rapidleech:latest
```

Open **`http://<server-ip>`** — use `http://`, not `https://`.

---

## Deployment Guides

### Docker (single container)

**Run:**
```bash
docker run -d \
  --name rapidleech \
  --restart unless-stopped \
  -p 80:8000 \
  -p 6881:6881/tcp \
  -p 6881:6881/udp \
  -e SECRET_KEY=your-random-secret \
  -e FILE_TTL_HOURS=24 \
  -v rapidleech_data:/app/data \
  -v rapidleech_downloads:/app/downloads \
  arpudhanaresh/rapidleech:latest
```

**Update to latest:**
```bash
docker stop rapidleech && docker rm rapidleech && \
docker rmi arpudhanaresh/rapidleech:latest && \
docker run -d --name rapidleech --restart unless-stopped \
  -p 80:8000 -p 6881:6881/tcp -p 6881:6881/udp \
  -e SECRET_KEY=your-random-secret \
  -v rapidleech_data:/app/data \
  -v rapidleech_downloads:/app/downloads \
  arpudhanaresh/rapidleech:latest
```

Downloads and the database live in named volumes — they survive the update.

---

### Docker Compose

```bash
cp .env.example .env
# Edit .env — at minimum set SECRET_KEY
docker compose up -d
```

**With PostgreSQL:**
```bash
# Add to .env:
# POSTGRES_PASSWORD=strongpassword
docker compose --profile postgres up -d
```

`docker-compose.yml` exposes ports 80 (HTTP) and 6881 (BitTorrent). The `downloads` and `db_data` volumes persist across restarts.

---

### AWS EC2

**Recommended instance:** t3.medium or larger (2 vCPU, 4 GB RAM).  
**Storage:** 100–500 GB gp3 EBS volume mounted at `/`.

**Security Group inbound rules:**

| Port | Protocol | Source | Purpose |
|------|----------|--------|---------|
| 80 | TCP | 0.0.0.0/0 | Web UI |
| 6881 | TCP + UDP | 0.0.0.0/0 | BitTorrent |
| 22 | TCP | Your IP | SSH |

> **Important:** EC2 outbound data transfer is billed (~$0.09/GB). Torrents upload to peers while downloading. RapidLeech stops seeding as soon as a torrent finishes. Consider setting `FILE_TTL_HOURS` to reclaim disk automatically.

**Install Docker on Amazon Linux 2023:**
```bash
sudo dnf install -y docker
sudo systemctl enable --now docker
sudo usermod -aG docker ec2-user
# Re-login, then:
docker run -d --name rapidleech --restart unless-stopped \
  -p 80:8000 -p 6881:6881/tcp -p 6881:6881/udp \
  -e SECRET_KEY=$(openssl rand -hex 32) \
  -v rapidleech_data:/app/data \
  -v rapidleech_downloads:/app/downloads \
  arpudhanaresh/rapidleech:latest
```

---

### Bare Metal

**Requirements:** Python 3.11+, `aria2c`, `ffmpeg`

```bash
# Install system deps (Debian/Ubuntu)
sudo apt update && sudo apt install -y aria2 ffmpeg

# Clone and install
git clone https://github.com/arpudhanaresh/rapidleech-revamp
cd rapidleech-revamp
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env

# Run (development)
python main.py

# Run (production with uvicorn)
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
```

> Use a single worker — the in-memory job store is not shared across processes.

---

## Configuration

All options are environment variables or a `.env` file in the project root.

### Server

| Variable | Default | Description |
|---|---|---|
| `HOST` | `0.0.0.0` | Bind address |
| `PORT` | `8000` | Listen port |
| `SECRET_KEY` | `change-me` | Session/CSRF secret — **change in production** |

### Database

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | _(SQLite)_ | Leave blank for `data/rapidleech.db` (SQLite) |
| | | PostgreSQL: `postgresql+asyncpg://user:pass@host:5432/db` |
| | | MySQL: `mysql+aiomysql://user:pass@host:3306/db` |

### Downloads

| Variable | Default | Description |
|---|---|---|
| `DOWNLOAD_DIR` | `downloads/` | Directory where files are saved |
| `MAX_CONCURRENT` | `3` | Maximum simultaneous downloads |
| `DEFAULT_CONNECTIONS` | `16` | HTTP parallel connections per file |
| `MAX_FILE_SIZE_GB` | `0` | Maximum allowed file size in GB (0 = unlimited) |
| `FILE_TTL_HOURS` | `5` | Auto-delete files older than N hours (0 = disabled) |

### aria2c

| Variable | Default | Description |
|---|---|---|
| `ARIA2_HOST` | `http://localhost` | aria2c JSON-RPC host |
| `ARIA2_PORT` | `6800` | aria2c JSON-RPC port |
| `ARIA2_RPC_SECRET` | _(empty)_ | aria2c RPC secret token |

### Security

| Variable | Default | Description |
|---|---|---|
| `RATE_LIMIT_FETCH` | `5/minute` | Fetch request rate limit per IP |
| `RATE_LIMIT_DOWNLOAD` | `30/minute` | File download rate limit per IP |

### Optional

| Variable | Default | Description |
|---|---|---|
| `CLAM_SOCKET` | _(empty)_ | ClamAV: Unix socket path (e.g. `/var/run/clamav/clamd.ctl`) or `host:port` |

---

## Usage Guide

### HTTP Downloads

Paste any direct URL into the input box and press **Download**.

- The app performs a `HEAD` request to get the file size, then selects the optimal number of connections and chunk sizes:

  | File size | Connections | Min chunk |
  |-----------|-------------|-----------|
  | < 5 MB | 1 | 1 MB |
  | 5–50 MB | up to 4 | 2 MB |
  | 50–500 MB | up to 8 | 5 MB |
  | 500 MB–2 GB | up to 16 | 10 MB |
  | > 2 GB | 16 | 20 MB |

- If aria2c is unavailable the built-in accelerator runs instead.
- The chunk map in the job card shows each connection's progress in real time.

### Torrents

Paste a **magnet link** or a `.torrent` URL. A file picker appears — uncheck files you don't want before starting. Per-file progress is shown during download. Seeding stops automatically when the download completes.

**Upload a `.torrent` file directly:**  
Use the torrent upload button in the fetch bar to pick a local `.torrent` file.

### Media (yt-dlp)

Paste a URL from YouTube, Vimeo, Twitter/X, TikTok, Instagram, Twitch, Dailymotion, Reddit, or 1000+ other sites.

A **quality picker** appears showing every available resolution with estimated file sizes:

| Preset | Format string |
|--------|--------------|
| 8K (4320p) | `bestvideo[height<=4320]+bestaudio` |
| 4K (2160p) | `bestvideo[height<=2160]+bestaudio` |
| 1080p HD | `bestvideo[height<=1080]+bestaudio` |
| … | … |
| Audio only | `bestaudio/best` |

> Resolutions above 1080p require ffmpeg to merge separate video and audio streams. ffmpeg is included in the Docker image.

**YouTube cookies (bot detection bypass):**  
Go to **Settings → YouTube Cookies → Upload cookies.txt**.  
Export a Netscape-format cookies file using a browser extension such as [Get cookies.txt LOCALLY](https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc).

### File Manager

The **Downloaded Files** section lists all files and torrent folders on disk.

- **Files** — direct download or copy link
- **Folders (📁)** — click the download icon to open the **Folder Browser**:
  - View all files with individual sizes
  - Download a single file directly
  - **Download as ZIP** — server zips in the background; a progress bar shows files processed; download triggers automatically when ready
- **Bulk actions** — select multiple items, then ZIP or delete
- **Expiry timer** — shows time remaining before TTL deletion

### Settings

| Setting | Description |
|---|---|
| YouTube Cookies | Upload / delete `cookies.txt` for authenticated yt-dlp downloads |
| Disk usage | Visual gauge of used / free space |
| Stats | Total downloaded, uploaded, jobs completed/failed |

---

## API Reference

Base path: `/api` — Interactive docs at `/api/docs`.

### Downloads

| Method | Endpoint | Body / Params | Description |
|--------|----------|--------------|-------------|
| `POST` | `/fetch` | `{url, max_connections?, torrent_file_indices?, format_id?}` | Start a download |
| `GET` | `/jobs` | — | List active jobs |
| `GET` | `/jobs/history` | `?status=&q=&page=` | Paginated job history |
| `POST` | `/jobs/{id}/cancel` | — | Cancel a job |
| `POST` | `/jobs/{id}/pause` | — | Pause a job |
| `POST` | `/jobs/{id}/resume` | — | Resume a job |

### Files

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/files` | List downloaded files and folders |
| `GET` | `/files/download/{name}` | Download a file |
| `DELETE` | `/files/{name}` | Delete a file or folder |
| `POST` | `/files/zip` | `{filenames:[]}` — zip selected files |
| `GET` | `/files/browse/{dir}` | List contents of a torrent folder |
| `GET` | `/files/dir-file?dirname=&path=` | Download one file from a folder |
| `POST` | `/files/zip-prepare` | `{dirname}` — start background ZIP job |
| `GET` | `/files/zip-status/{id}` | `{status, files_done, total_files}` |
| `GET` | `/files/zip-download/{id}` | Download the completed ZIP |

### Media

| Method | Endpoint | Body | Description |
|--------|----------|------|-------------|
| `POST` | `/ytdlp/formats` | `{url}` | Get available quality tiers |

### Torrents

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/torrent/upload` | Upload a `.torrent` file |
| `GET` | `/torrent/{id}/peers` | List connected peers |

### System

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/stats` | Disk, speed, aggregate totals |
| `GET` | `/health` | Health check — `{"status":"ok"}` |
| `GET` | `/events` | SSE stream: jobs + stats + files (updates every second) |

### Cookies

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/cookies/status` | Check if cookies.txt exists |
| `POST` | `/cookies/upload` | Upload cookies.txt (multipart) |
| `DELETE` | `/cookies` | Delete cookies.txt |

---

## Performance Tuning

### HTTP Speed
- **aria2c installed** — fastest path; use `DEFAULT_CONNECTIONS=16` and let the app auto-tune split sizes
- **No aria2c** — the built-in accelerator still uses parallel connections but with less tuning

### Disk I/O
- Use SSD-backed storage (`gp3` on AWS)
- Set `DOWNLOAD_DIR` to a dedicated volume to isolate download I/O

### Memory
- Each active job holds ~30 speed samples and chunk metadata in RAM — negligible
- Stale ZIP jobs (folder browser, never downloaded) are evicted after 2 hours automatically
- libtorrent maintains a piece cache; this is the largest memory consumer for active torrents

### Concurrent Downloads
- `MAX_CONCURRENT=3` is the default; increase if your server and network support it
- Each HTTP download spawns up to 16 aria2c connections — plan accordingly

### AWS Egress Costs
- Downloading files *to* EC2 is free (inbound)
- Serving files *from* EC2 to your browser costs ~$0.09/GB (outbound)
- Torrents stop seeding on completion — no continuous upload cost
- Set `FILE_TTL_HOURS` to reclaim disk and avoid accidentally keeping large files

---

## Troubleshooting

### Connection refused in browser
Use `http://` explicitly — browsers silently upgrade to HTTPS. Type the full address: `http://13.x.x.x`.

### "Sign in to confirm you're not a bot" (YouTube)
Upload a `cookies.txt` file via **Settings → YouTube Cookies**. Export using a browser extension while logged into YouTube.

### "Requested format is not available"
The quality preset requires a format the video doesn't have. Use the quality picker and select a lower resolution, or choose **Best (Auto)**.

### Torrent shows ERROR after 100%
Multi-file torrents save to a directory. The SHA-256 hash step skips directories automatically — if you see this, rebuild from the latest image which includes the fix.

### Files not appearing in File Manager
Files are hidden while a download is in progress. They appear only after the job transitions to `done` or `error`. Torrent folders appear as 📁 items.

### Torrent stuck at "Metadata"
The torrent may have no seeds. Try a different torrent or wait for peers. The app will time out after 60 seconds and report an error.

### High upload bandwidth (EC2 alarm)
Torrent seeding stops automatically when a download completes. If you still see high upload, a previous torrent may still be seeding — cancel it via the job controls. From the latest image, seeding is stopped immediately on completion.

### Out of disk space
Lower `FILE_TTL_HOURS` or delete files manually via the File Manager. The disk gauge in the header shows current usage.

---

## Security

RapidLeech is intended to run as a **private, single-user tool**. The following protections are built in, but it should not be exposed to the public internet without additional authentication (e.g. an Nginx reverse proxy with HTTP Basic Auth or OAuth2).

- **SSRF** — all fetch URLs are DNS-resolved; private IP ranges and cloud metadata endpoints are blocked
- **Rate limiting** — per-IP limits on fetch (5/min) and file downloads (30/min)
- **Abuse detection** — automatic temporary IP blocks on repeated violations
- **Filename sanitisation** — path traversal sequences and illegal characters stripped
- **Security headers** — HSTS, CSP, X-Frame-Options, X-Content-Type-Options on every response
- **Path traversal** — folder file downloads are jail-checked with `os.path.realpath`

---

## Contributing

1. Fork the repo and create a feature branch
2. Follow the existing code style — no comments except for non-obvious WHY explanations
3. Keep cognitive complexity below 15 per function (enforced by SonarLint)
4. Test with at least one HTTP download, one magnet link, and one media URL
5. Open a pull request with a clear description of what changed and why

---

<div align="center">

Built with FastAPI · yt-dlp · libtorrent · aria2c

</div>
