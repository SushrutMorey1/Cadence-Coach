# ──────────────────────────────────────────────
#  Cadence Coach — Dockerfile
# ──────────────────────────────────────────────
#  Runs FastAPI backend on port 8000.
#
#  Build :  docker build -t cadence-coach .
#  Run   :  docker run --env-file .env -p 8000:8000 cadence-coach
# ──────────────────────────────────────────────

# Pinned to Debian 12, the release the Azure Speech SDK is built and tested for (the plain
# `python:3.11-slim` tag has moved on to Debian 13).
FROM python:3.11-slim-bookworm

# ── System deps for Azure Speech SDK ──
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libssl-dev \
    ca-certificates \
    libasound2 \
    wget \
    && rm -rf /var/lib/apt/lists/*

# ── Working directory ──
WORKDIR /app

# ── Install Python dependencies ──
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Copy project source ──
COPY . .

# ── Create directories (generated audio) ──
RUN mkdir -p /app/audio_output /app/data

# ── Expose port ──
EXPOSE 8000

# ── Start FastAPI via uvicorn ──
# X-Forwarded-For is trusted only from the addresses in FORWARDED_ALLOW_IPS (uvicorn reads it;
# default 127.0.0.1). Never "*": any client could then claim any IP.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
