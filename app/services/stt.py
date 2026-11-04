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

from groq import Groq

from app.config import GROQ_API_KEY, WHISPER_MODEL
from app.services.profiler import trace, sizeof_fmt
from app.utils.retry import sync_retry

logger = logging.getLogger(__name__)

# Whisper normally "cleans up" speech and drops the ums and uhs. Priming it with
# a disfluent prompt makes it transcribe them, which is what the Coach measures.
_WHISPER_FILLER_PRIME = "Um, so, uh, I mean, you know, like... we, we basically, actually started it."

# Groq client for Whisper (lazy so the app imports without a key)
_client = None


def _get_client() -> Groq:
    global _client
    if _client is None:
        _client = Groq(api_key=GROQ_API_KEY or "not-configured")
    return _client


# Filler detection
# ─────────────────
# Two tiers, because "so" and "like" are real words most of the time:
#   • FILLER_WORDS are fillers wherever they appear ("um", "you know").
#   • CONTEXTUAL_FILLERS only count as discourse markers — sentence-initial
#     or set off by punctuation ("So, …", "it's, like, huge", "…, right?").
# Whisper punctuates its transcripts, which is what makes the second tier
# reliable. Repeated words ("the the") are reported separately as stutters.
FILLER_WORDS = {
    "um", "umm", "uh", "uhh", "hmm", "erm", "ah", "you know", "i mean",
    "sort of", "kind of", "basically", "literally", "actually",
}
CONTEXTUAL_FILLERS = {"like", "so", "right", "okay", "ok", "well"}

_ALWAYS_RE = re.compile(
    r"\b(" + "|".join(sorted((re.escape(w) for w in FILLER_WORDS), key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)
_CONTEXTUAL_RE = re.compile(
    r"(?:^|(?<=[.!?;:,—–-]\s)|(?<=[.!?;:,—–-]))\s*\b("
    + "|".join(sorted(CONTEXTUAL_FILLERS, key=len, reverse=True))
    + r")\b(?=\s*[,.!?…—–-])",
    re.IGNORECASE | re.MULTILINE,
)
_REPEAT_RE = re.compile(r"\b([A-Za-z']+)\s+\1\b", re.IGNORECASE)

LONG_PAUSE_SEC = 0.7


def detect_fillers(text: str) -> dict:
    """
    Count filler words and return where they are.

    Returns {"counts": {word: n}, "spans": [{"start", "end", "word"}], "total": n,
             "repeated_words": n}. Spans are character offsets into `text` so the
    UI can highlight them in the transcript.
    """
    counts: dict = {}
    spans: list = []
    seen = set()
    for regex in (_ALWAYS_RE, _CONTEXTUAL_RE):
        for m in regex.finditer(text or ""):
            start, end = m.start(1), m.end(1)
            if (start, end) in seen:
                continue
            seen.add((start, end))
            word = m.group(1).lower()
            counts[word] = counts.get(word, 0) + 1
            spans.append({"start": start, "end": end, "word": word})
    spans.sort(key=lambda s: s["start"])
    repeats = sum(1 for m in _REPEAT_RE.finditer(text or "") if m.group(1).lower() not in ("that", "had", "very"))
    return {"counts": counts, "spans": spans, "total": sum(counts.values()), "repeated_words": repeats}


def analyze_segments(segments: list) -> dict:
    """
    Pauses and pace variation from Whisper's timed segments.

    Returns {"long_pauses", "longest_pause_sec", "fastest_stretch": {"wpm", "text"} | None,
             "slowest_stretch": {...} | None}.
    """
    gaps = []
    for prev, cur in zip(segments, segments[1:]):
        try:
            gap = float(cur.get("start", 0)) - float(prev.get("end", 0))
        except (TypeError, ValueError):
            continue
        if gap > 0:
            gaps.append(gap)
    long_pauses = [g for g in gaps if g >= LONG_PAUSE_SEC]

    fastest = slowest = None
    for seg in segments:
        try:
            dur = float(seg.get("end", 0)) - float(seg.get("start", 0))
        except (TypeError, ValueError):
            continue
        words = len(str(seg.get("text", "")).split())
        if dur < 1.5 or words < 4:
            continue
        wpm = words / dur * 60
        entry = {"wpm": round(wpm), "text": str(seg.get("text", "")).strip()}
        if fastest is None or wpm > fastest["wpm"]:
            fastest = entry
        if slowest is None or wpm < slowest["wpm"]:
            slowest = entry
    return {
        "long_pauses": len(long_pauses),
        "longest_pause_sec": round(max(gaps), 1) if gaps else 0.0,
        "fastest_stretch": fastest,
        "slowest_stretch": slowest,
    }


@sync_retry(
    max_retries=2,
    base_delay=1.5,
    retryable_exceptions=(ConnectionError, TimeoutError, OSError),
)
def _whisper_api_call(file_tuple, language):
    """Call Groq Whisper API with retry on transient errors."""
    return _get_client().audio.transcriptions.create(
        model=WHISPER_MODEL,
        file=file_tuple,
        language=language,
        prompt=_WHISPER_FILLER_PRIME,
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
                    model=WHISPER_MODEL, language=language) as span:
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

        # Step 5: Detect filler words (two-tier, with positions for highlighting)
        with trace("Filler Word Detection", category="compute") as span:
            fillers = detect_fillers(text)
            filler_counts = fillers["counts"]
            total_fillers = fillers["total"]
            span["meta"]["fillers_found"] = total_fillers
            span["meta"]["unique_fillers"] = len(filler_counts)
            span["meta"]["repeated_words"] = fillers["repeated_words"]

        # Step 6: Extract segments
        with trace("Extract Audio Segments", category="compute") as span:
            segments = []
            if hasattr(transcription, "segments") and transcription.segments:
                for seg in transcription.segments:
                    if isinstance(seg, dict):
                        segments.append({"start": seg.get("start", 0), "end": seg.get("end", 0), "text": seg.get("text", "")})
                    else:
                        segments.append({
                            "start": getattr(seg, "start", 0),
                            "end": getattr(seg, "end", 0),
                            "text": getattr(seg, "text", ""),
                        })
            span["meta"]["segment_count"] = len(segments)

        # Step 7: Pauses and pace variation from the segment timings
        with trace("Pause & Pace Analysis", category="compute") as span:
            timing = analyze_segments(segments)
            span["meta"]["long_pauses"] = timing["long_pauses"]
            span["meta"]["longest_pause_sec"] = timing["longest_pause_sec"]
            if timing["fastest_stretch"]:
                span["meta"]["fastest_wpm"] = timing["fastest_stretch"]["wpm"]

        # Step 8: Compute ratings
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
            "filler_spans": fillers["spans"],
            "repeated_words": fillers["repeated_words"],
            "long_pauses": timing["long_pauses"],
            "longest_pause_sec": timing["longest_pause_sec"],
            "fastest_stretch": timing["fastest_stretch"],
            "slowest_stretch": timing["slowest_stretch"],
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
            "filler_spans": [],
            "repeated_words": 0,
            "long_pauses": 0,
            "longest_pause_sec": 0.0,
            "fastest_stretch": None,
            "slowest_stretch": None,
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
