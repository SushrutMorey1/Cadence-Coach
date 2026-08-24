"""
transcribe route — /api/transcribe
======================================
Accepts audio file uploads, runs Whisper transcription via Groq,
and returns text + speaking analytics (WPM, filler words).
Deep profiled at every step.
"""

from fastapi import APIRouter, UploadFile, File, HTTPException

from app.services.stt import async_transcribe_audio
from app.services.profiler import init_traces, get_traces, trace, sizeof_fmt

router = APIRouter()

# Accepted audio formats
ALLOWED_TYPES = {
    "audio/mpeg", "audio/mp3", "audio/wav", "audio/x-wav",
    "audio/mp4", "audio/m4a", "audio/x-m4a", "audio/ogg",
    "audio/webm", "audio/flac",
    "video/mp4", "video/webm",  # sometimes browsers tag audio as video
}
MAX_SIZE_MB = 25


@router.post("/transcribe")
async def transcribe_audio_endpoint(
    file: UploadFile = File(..., description="Audio file to transcribe (.mp3, .wav, .m4a, .ogg, .webm, .flac)"),
):
    """
    Upload an audio file → Groq Whisper transcription → speaking analytics.
    Every sub-step is individually profiled.
    """
    init_traces()

    # Step 1: Validate content type
    with trace("Validate Content Type", category="compute") as span:
        content_type = file.content_type or ""
        span["meta"]["content_type"] = content_type
        span["meta"]["filename"] = file.filename
        if content_type not in ALLOWED_TYPES and not content_type.startswith("audio/"):
            raise HTTPException(
                status_code=415,
                detail=f"Unsupported file type: {content_type}. Upload an audio file (.mp3, .wav, .m4a, etc.)",
            )

    # Step 2: Read file bytes from upload stream
    with trace("Read Upload Stream", category="io") as span:
        file_bytes = await file.read()
        span["meta"]["bytes_read"] = len(file_bytes)
        span["meta"]["file_size"] = sizeof_fmt(len(file_bytes))

    # Step 3: Validate size
    with trace("Validate File Size", category="compute") as span:
        size_mb = len(file_bytes) / (1024 * 1024)
        span["meta"]["size_mb"] = round(size_mb, 2)
        span["meta"]["max_mb"] = MAX_SIZE_MB
        if size_mb > MAX_SIZE_MB:
            raise HTTPException(
                status_code=413,
                detail=f"File too large ({size_mb:.1f} MB). Maximum is {MAX_SIZE_MB} MB.",
            )
        if len(file_bytes) == 0:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    # Step 4: Transcribe (internally deeply profiled)
    result = await async_transcribe_audio(
        file_bytes=file_bytes,
        filename=file.filename or "audio.mp3",
    )

    if not result["success"]:
        raise HTTPException(status_code=502, detail=result["message"])

    result["detailed_latencies"] = get_traces()
    return result
