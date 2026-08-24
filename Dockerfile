# ──────────────────────────────────────────────
#  Cadence Coach v2.0 — Dockerfile
# ──────────────────────────────────────────────
#  Runs FastAPI backend on port 8000.
#
#  Build :  docker build -t cadence-coach .
#  Run   :  docker run --env-file .env -p 8000:8000 cadence-coach
# ──────────────────────────────────────────────

FROM python:3.11-slim

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

# ── Create directories ──
RUN mkdir -p /app/audio_output

# ── Expose port ──
EXPOSE 8000

# ── Start FastAPI via uvicorn ──
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
