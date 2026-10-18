import json
import logging

from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)
from app.models.schemas import CoachRequest, CoachResponse
from app.services.llm import invoke_coach
from app.services.profiler import init_traces, get_traces, trace
from app.services.rate_limiter import get_user_key
from app.utils.parsing import parse_llm_json

router = APIRouter()

_MAX_RETRIES = 1  # retry at most once on non-length JSON parse failure


@router.post("/coach", response_model=CoachResponse)
async def coach_speech(request: CoachRequest, raw_request: Request):
    """Analyze text for communication quality and provide coaching feedback."""
    last_error = None

    for attempt in range(_MAX_RETRIES + 1):
        try:
            init_traces()
            user_key = get_user_key(raw_request)

            with trace("Request Validation", category="compute",
                        text_chars=len(request.user_text)):
                pass  # FastAPI already validated via Pydantic

            result = await invoke_coach(
                user_text=request.user_text,
                context=request.context or "Not provided — infer from the text.",
                user_key=user_key,
            )

            raw_output = result.get("raw_output", "")
            finish_reason = result.get("finish_reason")
            raw_tail = raw_output[-200:] if len(raw_output) >= 200 else raw_output

            logger.info(
                "Coach attempt %d/%d: finish_reason=%s. Last 200 chars: %r",
                attempt + 1, _MAX_RETRIES + 1, finish_reason, raw_tail,
            )

            if finish_reason == "length":
                logger.error(
                    "Coach response truncated due to token limit (finish_reason='length'). Aborting retry. Last 200 chars: %r",
                    raw_tail,
                )
                raise HTTPException(
                    status_code=502,
                    detail="The communication coach response exceeded the maximum token limit and was truncated. Please try again with a shorter section of text.",
                )

            with trace("Parse LLM JSON Output", category="compute") as span:
                parsed = parse_llm_json(raw_output)
                span["meta"]["json_keys"] = list(parsed.keys())
                span["meta"]["raw_output_chars"] = len(raw_output)

            if parsed.get("error"):
                error_msg = parsed.get("message", "LLM rejected the input")
                raise HTTPException(status_code=422, detail=error_msg)

            with trace("Build Response Model", category="compute") as span:
                parsed["detailed_latencies"] = get_traces()
                response = CoachResponse(**parsed)
                span["meta"]["overall_score"] = parsed.get("overall_score")

            # Re-attach the final traces (including this span)
            response.detailed_latencies = get_traces()
            return response

        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as err:
            last_error = err
            raw_output = result.get("raw_output", "") if "result" in locals() else ""
            finish_reason = result.get("finish_reason") if "result" in locals() else "unknown"
            raw_tail = raw_output[-200:] if len(raw_output) >= 200 else raw_output
            logger.warning(
                "Coach attempt %d/%d failed (%s): %s. finish_reason=%s. Last 200 chars: %r",
                attempt + 1, _MAX_RETRIES + 1,
                type(err).__name__, err, finish_reason, raw_tail,
            )
            if finish_reason == "length":
                raise HTTPException(
                    status_code=502,
                    detail="The communication coach response exceeded the maximum token limit and was truncated. Please try again with a shorter section of text.",
                )
            if attempt < _MAX_RETRIES:
                continue  # retry at most once for other JSON errors
            raise HTTPException(
                status_code=502,
                detail=f"LLM output could not be parsed after {_MAX_RETRIES + 1} attempts: {err}",
            )

        except HTTPException:
            raise

        except Exception as e:
            logger.error("Unexpected error in coach route: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
