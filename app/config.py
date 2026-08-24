import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# Groq / LLM
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
LLM_MODEL = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.7"))
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "4096"))

# Azure Speech
AZURE_SPEECH_KEY = os.getenv("AZURE_SPEECH_KEY")
AZURE_SPEECH_REGION = os.getenv("AZURE_SPEECH_REGION", "centralindia")

# Paths
AUDIO_OUTPUT_DIR = PROJECT_ROOT / "audio_output"
AUDIO_OUTPUT_DIR.mkdir(exist_ok=True)

# TTS Voice Options
VOICES = {
    "aria":  "en-US-AriaNeural",
    "davis": "en-US-DavisNeural",
    "jenny": "en-US-JennyNeural",
    "guy":   "en-US-GuyNeural",
    "sara":  "en-US-SaraNeural",
    "jason": "en-US-JasonNeural",
}
DEFAULT_VOICE = VOICES["davis"]

# Rate Limiting
RATE_LIMIT_TOKENS_PER_HOUR = int(os.getenv("RATE_LIMIT_TOKENS_PER_HOUR", "100000"))
RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "3600"))

# OAuth
OAUTH_REDIRECT_URI = os.getenv("OAUTH_REDIRECT_URI", "http://localhost:8000/api/auth/callback")

# MySQL Database
MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "cadence_coach")
