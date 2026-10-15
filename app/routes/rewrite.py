import json
import logging

from fastapi import APIRouter, HTTPException, Request
from app.models.schemas import RewriteRequest, RewriteResponse
from app.services.llm import invoke_rewrite
from app.services.profiler import init_traces, get_traces, trace
from app.services.rate_limiter import get_user_key
from app.utils.parsing import parse_llm_json

logger = logging.getLogger(__name__)
router = APIRouter()

_MAX_RETRIES = 2


@router.post("/rewrite", response_model=RewriteResponse)
async def rewrite_text(request: RewriteRequest, raw_request: Request):
    """Transform raw text into a polished speech script."""
    for attempt in range(_MAX_RETRIES + 1):
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
                error_msg = parsed.get("message", "LLM rejected the input")
                raise HTTPException(status_code=422, detail=error_msg)

            return RewriteResponse(
                display_script=parsed["display_script"],
                tts_script=parsed["tts_script"],
                detailed_latencies=get_traces(),
            )

        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as err:
            logger.warning(
                "Rewrite attempt %d/%d failed (%s): %s",
                attempt + 1, _MAX_RETRIES + 1, type(err).__name__, err,
            )
            if attempt < _MAX_RETRIES:
                continue
            raise HTTPException(
                status_code=502,
                detail=f"LLM output could not be parsed after {_MAX_RETRIES + 1} attempts: {err}",
            )

        except HTTPException:
            raise

        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
