import json

from fastapi import APIRouter, HTTPException, Request
from app.models.schemas import CoachRequest, CoachResponse
from app.services.llm import invoke_coach
from app.services.profiler import init_traces, get_traces, trace
from app.services.rate_limiter import get_user_key
from app.utils.parsing import parse_llm_json

router = APIRouter()


@router.post("/coach", response_model=CoachResponse)
async def coach_speech(request: CoachRequest, raw_request: Request):
    """Analyze text for communication quality and provide coaching feedback."""
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

        with trace("Parse LLM JSON Output", category="compute") as span:
            parsed = parse_llm_json(result["raw_output"])
            span["meta"]["json_keys"] = list(parsed.keys())
            span["meta"]["raw_output_chars"] = len(result["raw_output"])

        if parsed.get("error"):
            raise HTTPException(status_code=422, detail=parsed)

        with trace("Build Response Model", category="compute") as span:
            parsed["detailed_latencies"] = get_traces()
            response = CoachResponse(**parsed)
            span["meta"]["overall_score"] = parsed.get("overall_score")

        # Re-attach the final traces (including this span)
        response.detailed_latencies = get_traces()
        return response

    except json.JSONDecodeError:
        raise HTTPException(status_code=502, detail="LLM returned non-JSON output. Please retry.")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
