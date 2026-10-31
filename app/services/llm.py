"""
llm.py — Groq plumbing shared by the Director and the Coach
==============================================================
The lazily-built chat client, rate-limit handling, token-usage and SSE
helpers, and the boot-time check that the configured model still exists.
"""

import re
import json
import logging
from typing import Optional

import groq as groq_sdk
import httpx
from langchain_groq import ChatGroq
from app.config import LLM_MODEL, LLM_TEMPERATURE, LLM_MAX_TOKENS, GROQ_API_KEY

logger = logging.getLogger(__name__)

# ── LLM instance (lazy: the app must import and run tests without a key) ──
_llm = None


def get_llm() -> ChatGroq:
    global _llm
    if _llm is None:
        logger.info("Initializing ChatGroq with model=%s, max_tokens=%s", LLM_MODEL, LLM_MAX_TOKENS)
        if not GROQ_API_KEY:
            logger.warning("GROQ_API_KEY is not set — LLM calls will fail until it is configured.")
        _llm = ChatGroq(
            model=LLM_MODEL,
            temperature=LLM_TEMPERATURE,
            max_tokens=LLM_MAX_TOKENS,
            api_key=GROQ_API_KEY or "not-configured",
            # The SDK would otherwise retry a 429 silently (up to ~25 s of dead air
            # on the free tier). We handle rate limits ourselves and tell the user.
            max_retries=0,
        )
    return _llm


async def warm_llm() -> None:
    """
    Pay the first-request costs at boot: building the client (~0.5 s) and opening the
    first connection (~0.4 s) used to land on the user's first click.
    """
    if not GROQ_API_KEY:
        return
    try:
        await get_llm().ainvoke("Reply with one word: ok", max_tokens=1)
        logger.info("Groq client warmed.")
    except Exception as err:
        logger.warning("Groq warm-up skipped: %s", err)


# ── Groq rate limits ─────────────────────────────────────────────
# Free-tier keys get ~8,000 tokens per minute (prompt + completion; `max_tokens`
# is not charged up front). A call that would exceed it gets a 429 whose headers
# say when the budget resets. Streams wait that long — visibly — and resume.

def _parse_duration(raw) -> Optional[float]:
    """'9.282s', '4m19.2s', '500ms', '12' → seconds."""
    text = str(raw or "").strip().lower()
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return float(text)
    total, found = 0.0, False
    for num, unit in re.findall(r"(\d+(?:\.\d+)?)(ms|s|m|h)", text):
        found = True
        total += float(num) * {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}[unit]
    return total if found else None


def daily_limit_message(err) -> Optional[str]:
    """
    Groq also caps tokens PER DAY per model (200,000 on the free tier — about 100 Director runs).
    That one does not clear in a minute, so waiting a minute and retrying is pointless: say so.
    """
    if not isinstance(err, groq_sdk.RateLimitError) or "tokens per day" not in str(err).lower():
        return None
    found = re.search(r"try again in ([0-9hms.]+)", str(err))
    secs = _parse_duration(found.group(1)) if found else None
    when = f"in about {max(1, round(secs / 60))} min" if secs else "later today"
    return (f"Groq's daily token allowance for {LLM_MODEL} is used up — it frees up {when}. "
            "Set LLM_MODEL to another model in .env, or upgrade the Groq tier.")


def rate_limit_wait_seconds(err) -> Optional[float]:
    """If `err` is Groq's per-minute 429, how long until the budget resets (1–60 s); else None."""
    if not isinstance(err, groq_sdk.RateLimitError) or daily_limit_message(err):
        return None
    headers = getattr(getattr(err, "response", None), "headers", None) or {}
    for key in ("retry-after", "x-ratelimit-reset-tokens", "x-ratelimit-reset-requests"):
        secs = _parse_duration(headers.get(key))
        if secs is not None:
            return min(60.0, max(1.0, secs))
    return 15.0


def rate_limited_event(wait_s: float) -> str:
    """The SSE event the UI turns into a countdown."""
    return sse({"type": "rate_limited", "wait_s": round(wait_s, 1),
                "message": f"Groq's per-minute token budget is used up — resuming in {wait_s:.0f} s."})


def usage_from(message) -> Optional[dict]:
    """Real token usage when the provider reports it (on the final stream chunk)."""
    usage = getattr(message, "usage_metadata", None)
    if usage and (usage.get("input_tokens") or usage.get("output_tokens")):
        return usage
    return None


def estimate_tokens(text: str) -> int:
    """Rough token estimation (~4 chars per token for English)."""
    return max(1, len(text) // 4)


def sse(payload: dict) -> str:
    """One Server-Sent Event carrying a JSON payload."""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


# ── Model availability ──────────────────────────────────────────
# Groq retires models without notice (llama-3.3-70b-versatile disappeared in
# Oct 2026 and every feature returned 404 until the model was changed). The
# app checks once at boot and /api/health reports it, so this is visible
# before a demo rather than during one.
_model_status: dict = {"model": LLM_MODEL, "available": None, "alternatives": [], "error": None}
_NOT_CHAT = ("whisper", "tts", "guard", "orpheus", "safeguard")


async def check_model_available(timeout: float = 4.0) -> dict:
    """Ask Groq whether LLM_MODEL exists; never raises."""
    if not GROQ_API_KEY:
        _model_status.update(available=None, error="GROQ_API_KEY not set")
        return dict(_model_status)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get("https://api.groq.com/openai/v1/models",
                                    headers={"Authorization": f"Bearer {GROQ_API_KEY}"})
            resp.raise_for_status()
            ids = [m.get("id", "") for m in resp.json().get("data", [])]
        chat_models = sorted(i for i in ids if i and not any(x in i for x in _NOT_CHAT))
        _model_status.update(available=LLM_MODEL in ids, alternatives=chat_models, error=None)
        if LLM_MODEL not in ids:
            logger.error("Groq does not offer LLM_MODEL=%s. Available chat models: %s", LLM_MODEL, ", ".join(chat_models))
        else:
            logger.info("Groq model %s is available.", LLM_MODEL)
    except Exception as err:
        _model_status.update(error=f"{type(err).__name__}: {err}")
        logger.warning("Could not verify Groq model availability: %s", err)
    return dict(_model_status)


def model_status() -> dict:
    return dict(_model_status)
