import os
from pathlib import Path
from pydantic_settings import BaseSettings
from typing import Optional

# Project root = directory that contains this file — consistent on all platforms
_ROOT = Path(__file__).resolve().parent


class Settings(BaseSettings):
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    SECRET_KEY: str = "change-me"

    # Database — blank = local SQLite
    DATABASE_URL: Optional[str] = None

    # Downloads — resolved to absolute path so every service agrees on the location
    DOWNLOAD_DIR: str = str(_ROOT / "downloads")
    MAX_CONCURRENT: int = 3
    DEFAULT_CONNECTIONS: int = 4
    MAX_FILE_SIZE_GB: float = 0
    FILE_TTL_DEFAULT_HOURS: int = 5
    FILE_TTL_MAX_HOURS: int = 12

    # Torrent
    TORRENT_MAX_CONNECTIONS: int = 2000
    TORRENT_MAX_CONNECTIONS_PER_TORRENT: int = 500

    # aria2
    ARIA2_HOST: str = "http://localhost"
    ARIA2_PORT: int = 6800
    ARIA2_RPC_SECRET: str = ""

    # Security
    RATE_LIMIT_FETCH: str = "5/minute"
    RATE_LIMIT_DOWNLOAD: str = "30/minute"

    # ClamAV
    CLAM_SOCKET: Optional[str] = None

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
