"""
stt.py — Speech-to-Text Service (Deep Profiled, Async)
=========================================================
Transcribes audio files using Groq's Whisper API and computes
speaking metrics. Every sub-step is individually traced.
Includes async wrapper and retry logic for transient failures.
"""

import re
import sys
import asyncio
import logging
from typing import Optional

from groq import Groq

from app.config import GROQ_API_KEY
from app.services.profiler import trace, sizeof_fmt
from app.utils.retry import sync_retry

logger = logging.getLogger(__name__)

# Groq client for Whisper
_client = Groq(api_key=GROQ_API_KEY)

# Common filler words to detect
FILLER_WORDS = {
    "um", "uh", "hmm", "like", "you know", "basically",
    "actually", "literally", "so", "right", "okay", "ok",
    "sort of", "kind of", "i mean", "well",
}


@sync_retry(
    max_retries=2,
    base_delay=1.5,
    retryable_exceptions=(ConnectionError, TimeoutError, OSError),
)
def _whisper_api_call(file_tuple, language):
    """Call Groq Whisper API with retry on transient errors."""
    return _client.audio.transcriptions.create(
        model="whisper-large-v3",
        file=file_tuple,
        language=language,
        response_format="verbose_json",
    )


def transcribe_audio(
    file_bytes: bytes,
    filename: str,
    language: str = "en",
) -> dict:
    """
    Transcribe audio bytes using Groq Whisper.
    Every sub-operation is individually traced for deep latency inspection.
    """
    try:
        # Step 1: Prepare upload payload
        with trace("Prepare Audio Payload", category="compute",
                    filename=filename) as span:
            file_size = len(file_bytes)
            file_tuple = (filename, file_bytes)
            span["meta"]["file_size_bytes"] = file_size
            span["meta"]["file_size"] = sizeof_fmt(file_size)
            span["meta"]["format"] = filename.rsplit(".", 1)[-1] if "." in filename else "unknown"

        # Step 2: Groq Whisper API network call (with retry)
        with trace("Groq Whisper API Call", category="network",
                    model="whisper-large-v3", language=language) as span:
            transcription = _whisper_api_call(file_tuple, language)
            span["meta"]["response_type"] = type(transcription).__name__

        # Step 3: Extract base text
        with trace("Extract Transcription Text", category="compute") as span:
            text = transcription.text or ""
            duration_sec = getattr(transcription, "duration", None)
            span["meta"]["text_chars"] = len(text)
            span["meta"]["text_size"] = sizeof_fmt(sys.getsizeof(text))
            span["meta"]["audio_duration_sec"] = duration_sec

        # Step 4: Calculate word count and WPM
        with trace("Compute WPM", category="compute") as span:
            words = text.split()
            word_count = len(words)
            wpm = 0.0
            if duration_sec and duration_sec > 0:
                wpm = round((word_count / duration_sec) * 60, 1)
            span["meta"]["word_count"] = word_count
            span["meta"]["wpm"] = wpm

        # Step 5: Detect filler words
        with trace("Filler Word Detection", category="compute") as span:
            text_lower = text.lower()
            filler_counts = {}
            total_fillers = 0
            for filler in FILLER_WORDS:
                count = len(re.findall(r'\b' + re.escape(filler) + r'\b', text_lower))
                if count > 0:
                    filler_counts[filler] = count
                    total_fillers += count
            span["meta"]["fillers_found"] = total_fillers
            span["meta"]["unique_fillers"] = len(filler_counts)
            span["meta"]["filler_words_checked"] = len(FILLER_WORDS)

        # Step 6: Extract segments
        with trace("Extract Audio Segments", category="compute") as span:
            segments = []
            if hasattr(transcription, "segments") and transcription.segments:
                for seg in transcription.segments:
                    segments.append({
                        "start": getattr(seg, "start", 0),
                        "end": getattr(seg, "end", 0),
                        "text": getattr(seg, "text", ""),
                    })
            span["meta"]["segment_count"] = len(segments)

        # Step 7: Compute ratings
        with trace("Compute Ratings", category="compute") as span:
            filler_rate = round((total_fillers / word_count * 100), 1) if word_count > 0 else 0.0
            wpm_rating = _rate_wpm(wpm)
            filler_rating = _rate_fillers(filler_rate)
            span["meta"]["wpm_rating"] = wpm_rating
            span["meta"]["filler_rating"] = filler_rating
            span["meta"]["filler_rate_pct"] = filler_rate

        return {
            "success": True,
            "text": text,
            "duration_sec": duration_sec,
            "word_count": word_count,
            "wpm": wpm,
            "wpm_rating": wpm_rating,
            "filler_words": filler_counts,
            "filler_count": total_fillers,
            "filler_rate_pct": filler_rate,
            "filler_rating": filler_rating,
            "segments": segments,
            "message": "Transcription successful.",
        }

    except Exception as e:
        logger.error("Transcription failed: %s", e)
        return {
            "success": False,
            "text": "",
            "duration_sec": None,
            "word_count": 0,
            "wpm": 0,
            "wpm_rating": "unknown",
            "filler_words": {},
            "filler_count": 0,
            "filler_rate_pct": 0,
            "filler_rating": "unknown",
            "segments": [],
            "message": f"Transcription failed: {str(e)}",
        }


async def async_transcribe_audio(
    file_bytes: bytes,
    filename: str,
    language: str = "en",
) -> dict:
    """Async wrapper: runs transcription in a thread pool to avoid blocking the event loop."""
    return await asyncio.to_thread(transcribe_audio, file_bytes, filename, language)


def _rate_wpm(wpm: float) -> str:
    """Categorize speaking pace based on WPM."""
    if wpm == 0:
        return "unknown"
    elif wpm < 110:
        return "too_slow"
    elif wpm < 130:
        return "slow"
    elif wpm < 160:
        return "optimal"
    elif wpm < 180:
        return "fast"
    else:
        return "too_fast"


def _rate_fillers(filler_rate: float) -> str:
    """Categorize filler word usage."""
    if filler_rate < 2:
        return "excellent"
    elif filler_rate < 5:
        return "good"
    elif filler_rate < 10:
        return "needs_work"
    else:
        return "excessive"
