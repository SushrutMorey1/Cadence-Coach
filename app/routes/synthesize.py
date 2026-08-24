import os
import json
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from app.models.schemas import SynthesizeRequest, RewriteAndSpeakRequest
from app.services.tts import async_synthesize_to_file, resolve_voice
from app.services.llm import invoke_rewrite
from app.services.profiler import init_traces, get_traces, trace
from app.services.rate_limiter import get_user_key
from app.utils.parsing import parse_llm_json

router = APIRouter()


@router.post("/synthesize")
async def synthesize_speech(request: SynthesizeRequest):
    """Synthesize text into audio via Azure TTS. Returns the MP3 file."""
    try:
        init_traces()
        voice = resolve_voice(request.voice)
        result = await async_synthesize_to_file(
            tts_script=request.tts_script,
            voice=voice,
            target_style=request.target_style,
        )

        if not result["success"]:
            raise HTTPException(status_code=502, detail=result["message"])

        return FileResponse(
            path=result["file_path"],
            media_type="audio/mpeg",
            filename=os.path.basename(result["file_path"]),
            headers={"X-Audio-Duration": str(result.get("duration", "unknown"))},
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"TTS synthesis failed: {e}")


@router.post("/rewrite-and-speak")
async def rewrite_and_speak(request: RewriteAndSpeakRequest, raw_request: Request):
    """End-to-end: rewrite text then synthesize audio. Deep profiled."""
    try:
        init_traces()
        user_key = get_user_key(raw_request)

        with trace("Request Validation", category="compute",
                    text_chars=len(request.user_text),
                    style=request.target_style):
            pass

        # Step 1: Rewrite via LLM
        result = await invoke_rewrite(
            user_text=request.user_text,
            target_style=request.target_style,
            user_key=user_key,
        )

        with trace("Parse LLM JSON Output", category="compute") as span:
            parsed = parse_llm_json(result["raw_output"])
            span["meta"]["json_keys"] = list(parsed.keys())
            span["meta"]["raw_output_chars"] = len(result["raw_output"])

        if parsed.get("error"):
            raise HTTPException(status_code=422, detail=parsed)

        display_script = parsed["display_script"]
        tts_script = parsed["tts_script"]

        # Step 2: Resolve voice
        with trace("Voice Resolution", category="compute",
                    voice_input=request.voice or "(default)") as span:
            voice = resolve_voice(request.voice)
            span["meta"]["resolved_voice"] = voice

        # Step 3: Synthesize
        tts_result = await async_synthesize_to_file(
            tts_script=display_script,
            voice=voice,
            target_style=request.target_style,
        )

        return {
            "display_script": display_script,
            "tts_script": tts_script,
            "audio": {
                "success": tts_result["success"],
                "file_path": tts_result.get("file_path"),
                "filename": tts_result.get("filename"),
                "duration": tts_result.get("duration"),
                "voice": voice,
                "message": tts_result["message"],
            },
            "detailed_latencies": get_traces(),
        }

    except json.JSONDecodeError:
        raise HTTPException(status_code=502, detail="LLM returned non-JSON output. Please retry.")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
