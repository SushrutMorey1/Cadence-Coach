"""
stream route — /api/rewrite/stream, /api/coach/stream
========================================================
Server-Sent Events (SSE) endpoints for real-time LLM
token streaming. Additive to the existing batch endpoints.
"""

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.models.schemas import RewriteRequest, CoachRequest
from app.services.llm import stream_rewrite, stream_coach
from app.services.rate_limiter import get_user_key

router = APIRouter()


@router.post("/rewrite/stream")
async def rewrite_stream(request: RewriteRequest, raw_request: Request):
    """Stream the rewrite response token-by-token via Server-Sent Events."""
    user_key = get_user_key(raw_request)

    return StreamingResponse(
        stream_rewrite(
            user_text=request.user_text,
            target_style=request.target_style,
            user_key=user_key,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable Nginx buffering for SSE
        },
    )


@router.post("/coach/stream")
async def coach_stream(request: CoachRequest, raw_request: Request):
    """Stream the coach analysis response token-by-token via Server-Sent Events."""
    user_key = get_user_key(raw_request)

    return StreamingResponse(
        stream_coach(
            user_text=request.user_text,
            context=request.context or "Not provided — infer from the text.",
            user_key=user_key,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
