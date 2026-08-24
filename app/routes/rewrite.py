import json

from fastapi import APIRouter, HTTPException, Request
from app.models.schemas import RewriteRequest, RewriteResponse
from app.services.llm import invoke_rewrite
from app.services.profiler import init_traces, get_traces, trace
from app.services.rate_limiter import get_user_key
from app.utils.parsing import parse_llm_json

router = APIRouter()


@router.post("/rewrite", response_model=RewriteResponse)
async def rewrite_text(request: RewriteRequest, raw_request: Request):
    """Transform raw text into a polished speech script."""
    try:
        init_traces()
        user_key = get_user_key(raw_request)

        with trace("Request Validation", category="compute",
                    text_chars=len(request.user_text),
                    style=request.target_style):
            pass  # FastAPI already validated via Pydantic

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

        return RewriteResponse(
            display_script=parsed["display_script"],
            tts_script=parsed["tts_script"],
            detailed_latencies=get_traces(),
        )

    except json.JSONDecodeError:
        raise HTTPException(status_code=502, detail="LLM returned non-JSON output. Please retry.")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
