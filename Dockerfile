# ── Stage 1: builder ─────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# System deps needed to compile some packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --prefix=/install --no-cache-dir -r requirements.txt && \
    pip install --prefix=/install --no-cache-dir --no-deps mega.py


# ── Stage 2: runtime ─────────────────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# aria2c + ffmpeg (required for merging yt-dlp video+audio streams)
RUN apt-get update && apt-get install -y --no-install-recommends \
    aria2 \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy source
COPY . .

# Wire up entrypoint
COPY entrypoint.sh .

RUN mkdir -p static/fonts data downloads && \
    chmod +x entrypoint.sh && \
    useradd -m -u 1000 rapidleech && \
    chown -R rapidleech:rapidleech /app
USER rapidleech

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')"

ENTRYPOINT ["./entrypoint.sh"]
