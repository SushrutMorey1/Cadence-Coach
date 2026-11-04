"""
synthesize route — /api/synthesize, /api/warm
================================================
Speak arbitrary text (the Director's edit-and-re-speak and the flat-read
comparison) through the same parallel pipeline the stream uses, and pre-open
the Azure connection so the first performance does not pay for the handshake.
"""

import asyncio
import os
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from app.config import AZURE_SPEECH_KEY, TTS_POOL_SIZE
from app.models.schemas import SynthesizeRequest
from app.services.metrics import log_tts_call
from app.services.profiler import init_traces
from app.services.rate_limiter import get_user_key
from app.services.tts import _synth_pool, resolve_voice, speak_script

router = APIRouter()


@router.post("/synthesize")
async def synthesize_speech(request: SynthesizeRequest, raw_request: Request):
    """
    Synthesize text into audio via Azure TTS. Returns the MP3 file.

    `tts_script` may carry VOXIS voice direction (prosody, breaks, emphasis,
    bracketed notes); `plain=true` ignores all of it for a flat A/B read.
    """
    init_traces()
    voice = resolve_voice(request.voice)
    try:
        result = await speak_script(
            request.tts_script,
            voice=voice,
            target_style=None if request.plain else request.target_style,
            annotate=not request.plain,
            use_style=not request.plain,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"TTS synthesis failed: {e}")

    await log_tts_call(voice=voice, char_count=result.get("char_count", 0), duration_sec=result.get("duration"),
                       status="success" if result.get("success") else f"failed: {result.get('message', 'unknown')}",
                       user_key=get_user_key(raw_request))
    if not result.get("success"):
        raise HTTPException(status_code=502, detail=result.get("message", "Speech synthesis failed."))

    return FileResponse(
        path=result["file_path"],
        media_type="audio/mpeg",
        filename=os.path.basename(result["file_path"]),
        headers={
            "X-Audio-Duration": str(result.get("duration", "unknown")),
            "X-Audio-Filename": os.path.basename(result["file_path"]),
            "X-Audio-Directed": "1" if result.get("directed") else "0",
        },
    )


# Each warm-up plays a short sound on every pooled synthesizer, which Azure bills.
# One warm-up per window serves every user, however often the endpoint is called.
WARM_COOLDOWN_SECONDS = 30
_warm_lock = asyncio.Lock()
_last_warm = 0.0


@router.post("/warm")
async def warm_voice():
    """Open the Azure connections ahead of use (the page calls this when the user starts typing)."""
    global _last_warm
    if not AZURE_SPEECH_KEY:
        return {"warmed": 0}
    async with _warm_lock:
        if time.monotonic() - _last_warm < WARM_COOLDOWN_SECONDS:
            return {"warmed": 0, "skipped": "recently warmed"}
        _last_warm = time.monotonic()
    return {"warmed": await asyncio.to_thread(_synth_pool.warm, TTS_POOL_SIZE)}
