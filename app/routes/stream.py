"""
stream route — /api/rewrite/stream, /api/rewrite-and-speak/stream, /api/coach/stream
=======================================================================================
Server-Sent Events for the Speech Director and the Communication Coach. The
event sequences are documented in services/director.py and services/coach.py.
"""

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.models.schemas import RewriteRequest, CoachRequest, RewriteAndSpeakRequest
from app.services.coach import stream_coach
from app.services.director import stream_rewrite
from app.services.rate_limiter import get_user_key

router = APIRouter()

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",  # Disable Nginx buffering for SSE
}


@router.post("/rewrite/stream")
async def rewrite_stream(request: RewriteRequest, raw_request: Request):
    """Stream the rewritten script as it is written."""
    return StreamingResponse(
        stream_rewrite(
            user_text=request.user_text,
            target_style=request.target_style,
            speak=False,
            user_key=get_user_key(raw_request),
        ),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.post("/rewrite-and-speak/stream")
async def rewrite_and_speak_stream(request: RewriteAndSpeakRequest, raw_request: Request):
    """Stream the script and, while it is still being written, the voice performing it."""
    return StreamingResponse(
        stream_rewrite(
            user_text=request.user_text,
            target_style=request.target_style,
            voice=request.voice,
            speak=True,
            user_key=get_user_key(raw_request),
        ),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.post("/coach/stream")
async def coach_stream(request: CoachRequest, raw_request: Request):
    """Stream the coach's analysis; the validated result arrives in a final `result` event."""
    return StreamingResponse(
        stream_coach(
            user_text=request.user_text,
            context=request.context or "Not provided — infer from the text.",
            speech_metrics=request.speech_metrics,
            user_key=get_user_key(raw_request),
            source=request.source or ("audio" if request.speech_metrics else "text"),
        ),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )
