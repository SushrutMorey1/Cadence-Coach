"""
coach.py — The Communication Coach's stream
==============================================
One LLM call that returns a JSON object. The tokens are streamed so the page
can light up each dimension as its score appears; the validated result arrives
in a final `result` event, and nothing is committed before that, so a bad
parse is retried silently.

Events:  token · rate_limited · retry · result · done · error
"""

import asyncio
import json
import logging
import time
from typing import AsyncGenerator, Optional

from app.config import LLM_MODEL
from app.models.schemas import CoachResponse
from app.prompts.coach import coach_prompt, describe_speech_metrics
from app.services import llm
from app.services.coach_runs import save_coach_run
from app.services.llm import daily_limit_message, estimate_tokens, rate_limit_wait_seconds, rate_limited_event, sse, usage_from
from app.services.metrics import log_cycle, log_llm_call
from app.services.profiler import get_traces, init_traces, trace
from app.services.rate_limiter import rate_limiter
from app.utils.parsing import parse_llm_json

logger = logging.getLogger(__name__)

ENDPOINT = "/api/coach/stream"


async def stream_coach(
    user_text: str,
    context: str,
    speech_metrics: Optional[dict] = None,
    user_key: str = "unknown",
    source: str = "text",
) -> AsyncGenerator[str, None]:
    """Stream the analysis as SSE; see the module docstring for the events."""
    init_traces()
    started = time.perf_counter()
    metrics_text = describe_speech_metrics(speech_metrics)
    attempts = 2
    attempt, waits = 1, 0

    async def failed(status: str) -> None:
        await log_llm_call(endpoint=ENDPOINT, model=LLM_MODEL, prompt_tokens=0, completion_tokens=0,
                           latency_ms=(time.perf_counter() - started) * 1000, status=status, user_key=user_key)

    while attempt <= attempts:
        collected: list = []
        usage = None
        finish_reason = None
        try:
            with trace("Prompt Template Render", category="compute", template="coach_prompt") as span:
                prompt_value = await coach_prompt.ainvoke({
                    "user_text": user_text, "context": context, "speech_metrics": metrics_text,
                })
                span["meta"]["prompt_chars"] = len(prompt_value.to_string())
                span["meta"]["measured_delivery"] = speech_metrics is not None

            with trace("Groq LLM Stream Generation", category="network", model=LLM_MODEL, attempt=attempt) as span:
                async for chunk in llm.get_llm().astream(prompt_value):
                    usage = usage_from(chunk) or usage
                    finish_reason = (getattr(chunk, "response_metadata", None) or {}).get("finish_reason") or finish_reason
                    token = chunk.content if isinstance(chunk.content, str) else ""
                    if token:
                        collected.append(token)
                        yield sse({"type": "token", "text": token})
                span["meta"]["chunks"] = len(collected)
                span["meta"]["finish_reason"] = finish_reason or "stop"

            full_output = "".join(collected)
            if finish_reason == "length":
                raise ValueError("response truncated by the token limit")

            with trace("Parse LLM JSON Output", category="compute") as span:
                parsed = parse_llm_json(full_output)
                span["meta"]["json_keys"] = list(parsed.keys())
                span["meta"]["raw_output_chars"] = len(full_output)

            latency_ms = (time.perf_counter() - started) * 1000
            if usage:
                prompt_tokens, completion_tokens, token_source = usage.get("input_tokens", 0), usage.get("output_tokens", 0), "api"
            else:
                prompt_tokens, completion_tokens, token_source = estimate_tokens(user_text + context), estimate_tokens(full_output), "estimate"

            if parsed.get("error"):
                await log_llm_call(endpoint=ENDPOINT, model=LLM_MODEL, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                                   latency_ms=latency_ms, status=f"rejected: {parsed.get('error_code', 'INVALID_INPUT')}", user_key=user_key)
                yield sse({"type": "error", "message": parsed.get("message", "The coach could not analyse this input.")})
                return

            with trace("Build Response Model", category="compute") as span:
                response = CoachResponse(**parsed)
                span["meta"]["overall_score"] = response.overall_score

            result = response.model_dump()
            traces = get_traces()
            total_ms = (time.perf_counter() - started) * 1000
            yield sse({"type": "result", **result, "detailed_latencies": traces})
            yield sse({"type": "done", "total_latency_ms": total_ms, "detailed_latencies": traces,
                       "token_source": token_source, "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens})

            # Bookkeeping runs concurrently and after the reader already has the result.
            await asyncio.gather(
                log_llm_call(endpoint=ENDPOINT, model=LLM_MODEL, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                             latency_ms=latency_ms, status="success", user_key=user_key),
                rate_limiter.consume(user_key, prompt_tokens + completion_tokens),
                save_coach_run(user_key, result, user_text, source=source, context=context, speech_metrics=speech_metrics),
                log_cycle("Analyze (audio)" if speech_metrics else "Analyze", total_ms, traces, user_key=user_key),
                return_exceptions=True,
            )
            return

        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as err:
            truncated = finish_reason == "length"
            logger.warning("Coach stream attempt %d/%d failed (%s): %s", attempt, attempts, type(err).__name__, err)
            attempt += 1
            if attempt <= attempts and not truncated:
                yield sse({"type": "retry", "attempt": attempt, "reason": type(err).__name__})
                continue
            await failed(f"error: {type(err).__name__}")
            yield sse({"type": "error", "message": (
                "The coach's answer was cut off by the token limit — try a shorter passage." if truncated else
                f"The coach's answer could not be read after {attempts} attempts ({type(err).__name__}).")})
            return

        except Exception as e:
            wait = rate_limit_wait_seconds(e)
            if wait is not None and waits < 2:
                waits += 1
                logger.warning("Groq rate limit hit — waiting %.0fs before resuming the coach.", wait)
                yield rate_limited_event(wait)
                await asyncio.sleep(wait + 0.5)
                continue
            logger.exception("Error in stream_coach: %s", e)
            await failed(f"error: {type(e).__name__}")
            yield sse({"type": "error", "message": daily_limit_message(e) or str(e)})
            return
