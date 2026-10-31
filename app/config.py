"""
config.py — Centralised Environment Configuration
====================================================
Every tunable lives here. Nothing else in the app reads os.environ
directly, so the full surface of configuration is visible in one file.

The stack is deliberately small: Groq (LLM + Whisper) and Azure Neural
TTS. Optional pieces (Google OAuth, MySQL) degrade gracefully — the app
boots in guest mode on SQLite when they are not configured.
"""

import os
import secrets
import logging
from pathlib import Path
from dotenv import load_dotenv, dotenv_values

logger = logging.getLogger(__name__)


def drop_comment_values(values: dict, environ) -> list:
    """
    Treat `KEY=   # a comment` as unset.

    python-dotenv only strips an inline comment when the value before it is
    non-empty, so a template line like `KEY=   # note` loads the *comment*
    as the value — and a feature then looks configured while sending garbage
    to the provider. Remove such entries from `environ` and return their
    names (never their values).
    """
    dropped = []
    for key, value in values.items():
        if value and value.strip().startswith("#") and environ.get(key) == value:
            del environ[key]
            dropped.append(key)
    return dropped


# Load .env from project root (tests set CADENCE_SKIP_DOTENV so a developer's
# real .env can never leak into the test run).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if not os.getenv("CADENCE_SKIP_DOTENV"):
    _ENV_FILE = PROJECT_ROOT / ".env"
    load_dotenv(_ENV_FILE, override=True)
    _ignored = drop_comment_values(dotenv_values(_ENV_FILE), os.environ)
    if _ignored:
        logger.warning(
            "Ignoring .env entries whose value is only a comment (treated as empty): %s. "
            "Put the value right after '=' or move the comment to its own line.", ", ".join(_ignored),
        )


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# ── Groq / LLM ──────────────────────────────────────────────────────
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
# Groq retires models without notice (llama-3.3-70b-versatile went in Oct 2026);
# `GET /api/health` → `llm.available` says whether this one still exists.
LLM_MODEL = os.getenv("LLM_MODEL", "qwen/qwen3.8-27b")
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.4"))
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "2500"))
# Whisper for the Coach's record/upload mode. large-v3 hears filler words
# better than -turbo, and fillers are the Coach's headline metric.
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "whisper-large-v3")

# ── Azure Speech (TTS) ──────────────────────────────────────────────
AZURE_SPEECH_KEY = os.getenv("AZURE_SPEECH_KEY")
AZURE_SPEECH_REGION = os.getenv("AZURE_SPEECH_REGION", "centralindia")
# Pre-warmed synthesizers; also the parallelism of the Director's segment pipeline.
TTS_POOL_SIZE = int(os.getenv("TTS_POOL_SIZE", "3"))

# ── Paths ───────────────────────────────────────────────────────────
AUDIO_OUTPUT_DIR = PROJECT_ROOT / "audio_output"
AUDIO_OUTPUT_DIR.mkdir(exist_ok=True)
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
# Generated MP3s are disposable; delete them after this many hours (at boot).
AUDIO_RETENTION_HOURS = float(os.getenv("AUDIO_RETENTION_HOURS", "24"))

# ── TTS voice options (Azure Neural) ───────────────────────────────
VOICES = {
    "aria":  "en-US-AriaNeural",
    "davis": "en-US-DavisNeural",
    "jenny": "en-US-JennyNeural",
    "guy":   "en-US-GuyNeural",
    "sara":  "en-US-SaraNeural",
    "jason": "en-US-JasonNeural",
}
DEFAULT_VOICE = VOICES["davis"]

# ── Rate limiting ──────────────────────────────────────────────────
RATE_LIMIT_TOKENS_PER_HOUR = int(os.getenv("RATE_LIMIT_TOKENS_PER_HOUR", "100000"))
RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "3600"))

# ── Auth ────────────────────────────────────────────────────────────
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
OAUTH_REDIRECT_URI = os.getenv("OAUTH_REDIRECT_URI", "http://localhost:8000/api/auth/callback")
SESSION_SECRET_KEY = os.getenv("SESSION_SECRET_KEY") or secrets.token_hex(32)
if not os.getenv("SESSION_SECRET_KEY"):
    logger.warning("SESSION_SECRET_KEY not set — generated an ephemeral key (sessions reset on restart).")

# Google sign-in is only enforced when credentials exist. Without them the
# app runs in guest mode so a demo never blocks on OAuth setup.
AUTH_REQUIRED = _env_bool("AUTH_REQUIRED", bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET))

# ── Database ────────────────────────────────────────────────────────
# auto   → try MySQL, fall back to SQLite if unreachable
# mysql  → MySQL only (raise on failure)
# sqlite → SQLite only (zero-infra local dev / demo)
DB_BACKEND = os.getenv("DB_BACKEND", "auto").lower()
SQLITE_PATH = Path(os.getenv("SQLITE_PATH", str(DATA_DIR / "cadence.db")))
MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "cadence_coach")


def integration_status() -> dict:
    """Which integrations are configured. Surfaced by /api/health."""
    return {
        "groq": bool(GROQ_API_KEY),
        "azure_tts": bool(AZURE_SPEECH_KEY),
        "google_oauth": AUTH_REQUIRED,
    }
