"""
llm.py — LLM Service Layer (Async + Retry)
=============================================
Provides deep-profiled LLM invocation with per-step tracing:
prompt rendering, network call, output parsing, token estimation.
Includes retry logic for transient Groq API failures and
SSE streaming generators for real-time token delivery.
"""

import time
import sys
import logging
from typing import Optional, AsyncGenerator

from langchain_groq import ChatGroq
from langchain_core.output_parsers import StrOutputParser
from langchain_core.messages import HumanMessage

from app.config import LLM_MODEL, LLM_TEMPERATURE, LLM_MAX_TOKENS, GROQ_API_KEY
from app.prompts.voxis import voxis_prompt
from app.prompts.coach import coach_prompt
from app.services.profiler import trace, sizeof_fmt
from app.services.rate_limiter import rate_limiter
from app.utils.retry import async_retry

logger = logging.getLogger(__name__)

# ── LLM instance ──
llm = ChatGroq(
    model=LLM_MODEL,
    temperature=LLM_TEMPERATURE,
    max_tokens=LLM_MAX_TOKENS,
    api_key=GROQ_API_KEY,
)

_str_parser = StrOutputParser()


# ── Retry-wrapped LLM call ──
@async_retry(max_retries=2, base_delay=1.5, backoff_factor=2.0)
async def _llm_invoke(prompt_value):
    """Invoke the LLM with retry on transient failures."""
    return await llm.ainvoke(prompt_value)


async def invoke_rewrite(user_text: str, target_style: str, user_key: str = "unknown") -> dict:
    """Invoke the VOXIS rewriting chain with deep metrics tracking."""
    from app.services.metrics import log_llm_call  # deferred to avoid circular import

    start = time.time()
    try:
        # Step 1: Render the prompt template
        with trace("Prompt Template Render", category="compute",
                    template="voxis_prompt") as span:
            prompt_value = await voxis_prompt.ainvoke({
                "user_text": user_text,
                "target_style": target_style,
            })
            prompt_str = prompt_value.to_string()
            span["meta"]["prompt_chars"] = len(prompt_str)
            span["meta"]["prompt_size"] = sizeof_fmt(sys.getsizeof(prompt_str))

        # Step 2: Send to Groq LLM (with retry)
        with trace("Groq LLM Network Call", category="network",
                    model=LLM_MODEL, endpoint="rewrite") as span:
            llm_response = await _llm_invoke(prompt_value)
            span["meta"]["response_chars"] = len(llm_response.content)
            # Extract real token usage if available
            usage = getattr(llm_response, "usage_metadata", None)
            if usage:
                span["meta"]["prompt_tokens"] = usage.get("input_tokens", 0)
                span["meta"]["completion_tokens"] = usage.get("output_tokens", 0)
                span["meta"]["total_tokens"] = usage.get("total_tokens", 0)

        # Step 3: Parse the output
        with trace("Output String Parsing", category="compute") as span:
            raw_output = _str_parser.invoke(llm_response)
            span["meta"]["output_chars"] = len(raw_output)
            span["meta"]["output_size"] = sizeof_fmt(sys.getsizeof(raw_output))

        latency_ms = (time.time() - start) * 1000

        # Step 4: Token estimation
        with trace("Token Estimation", category="compute") as span:
            if usage:
                prompt_tokens = usage.get("input_tokens", 0)
                completion_tokens = usage.get("output_tokens", 0)
            else:
                prompt_tokens = _estimate_tokens(user_text + target_style)
                completion_tokens = _estimate_tokens(raw_output)
            span["meta"]["prompt_tokens"] = prompt_tokens
            span["meta"]["completion_tokens"] = completion_tokens
            span["meta"]["estimation_method"] = "api_reported" if usage else "heuristic_4chars"

        # Step 5: Log metrics to DB (async)
        with trace("Metrics DB Write", category="io"):
            await log_llm_call(
                endpoint="/api/rewrite",
                model=LLM_MODEL,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                latency_ms=latency_ms,
                status="success",
            )

        # Step 6: Record tokens against the user's rate-limit budget (async)
        total_tokens = prompt_tokens + completion_tokens
        await rate_limiter.consume(user_key, total_tokens)

        return {"raw_output": raw_output, "latency_ms": latency_ms}

    except Exception as e:
        latency_ms = (time.time() - start) * 1000
        await log_llm_call(
            endpoint="/api/rewrite",
            model=LLM_MODEL,
            prompt_tokens=0,
            completion_tokens=0,
            latency_ms=latency_ms,
            status=f"error: {type(e).__name__}",
        )
        raise


async def invoke_coach(user_text: str, context: str, user_key: str = "unknown") -> dict:
    """Invoke the Communication Coach chain with deep metrics tracking."""
    from app.services.metrics import log_llm_call

    start = time.time()
    try:
        # Step 1: Render the prompt template
        with trace("Prompt Template Render", category="compute",
                    template="coach_prompt") as span:
            prompt_value = await coach_prompt.ainvoke({
                "user_text": user_text,
                "context": context,
            })
            prompt_str = prompt_value.to_string()
            span["meta"]["prompt_chars"] = len(prompt_str)
            span["meta"]["prompt_size"] = sizeof_fmt(sys.getsizeof(prompt_str))

        # Step 2: Send to Groq LLM (with retry)
        with trace("Groq LLM Network Call", category="network",
                    model=LLM_MODEL, endpoint="coach") as span:
            llm_response = await _llm_invoke(prompt_value)
            span["meta"]["response_chars"] = len(llm_response.content)
            usage = getattr(llm_response, "usage_metadata", None)
            if usage:
                span["meta"]["prompt_tokens"] = usage.get("input_tokens", 0)
                span["meta"]["completion_tokens"] = usage.get("output_tokens", 0)
                span["meta"]["total_tokens"] = usage.get("total_tokens", 0)

        # Step 3: Parse the output
        with trace("Output String Parsing", category="compute") as span:
            raw_output = _str_parser.invoke(llm_response)
            span["meta"]["output_chars"] = len(raw_output)
            span["meta"]["output_size"] = sizeof_fmt(sys.getsizeof(raw_output))

        latency_ms = (time.time() - start) * 1000

        # Step 4: Token estimation
        with trace("Token Estimation", category="compute") as span:
            if usage:
                prompt_tokens = usage.get("input_tokens", 0)
                completion_tokens = usage.get("output_tokens", 0)
            else:
                prompt_tokens = _estimate_tokens(user_text + context)
                completion_tokens = _estimate_tokens(raw_output)
            span["meta"]["prompt_tokens"] = prompt_tokens
            span["meta"]["completion_tokens"] = completion_tokens
            span["meta"]["estimation_method"] = "api_reported" if usage else "heuristic_4chars"

        # Step 5: Log metrics to DB (async)
        with trace("Metrics DB Write", category="io"):
            await log_llm_call(
                endpoint="/api/coach",
                model=LLM_MODEL,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                latency_ms=latency_ms,
                status="success",
            )

        # Step 6: Record tokens against the user's rate-limit budget (async)
        total_tokens = prompt_tokens + completion_tokens
        await rate_limiter.consume(user_key, total_tokens)

        return {"raw_output": raw_output, "latency_ms": latency_ms}

    except Exception as e:
        latency_ms = (time.time() - start) * 1000
        await log_llm_call(
            endpoint="/api/coach",
            model=LLM_MODEL,
            prompt_tokens=0,
            completion_tokens=0,
            latency_ms=latency_ms,
            status=f"error: {type(e).__name__}",
        )
        raise


# ═══════════════════════════════════════════════════════════════
# SSE Streaming Generators
# ═══════════════════════════════════════════════════════════════

async def stream_rewrite(user_text: str, target_style: str, user_key: str = "unknown") -> AsyncGenerator[str, None]:
    """Stream the VOXIS rewrite response token-by-token via SSE."""
    from app.services.metrics import log_llm_call

    start = time.time()
    collected_output = []

    try:
        prompt_value = await voxis_prompt.ainvoke({
            "user_text": user_text,
            "target_style": target_style,
        })

        async for chunk in llm.astream(prompt_value):
            token = chunk.content
            if token:
                collected_output.append(token)
                yield f"data: {token}\n\n"

        # Stream complete — log metrics
        full_output = "".join(collected_output)
        latency_ms = (time.time() - start) * 1000
        prompt_tokens = _estimate_tokens(user_text + target_style)
        completion_tokens = _estimate_tokens(full_output)

        await log_llm_call(
            endpoint="/api/rewrite/stream",
            model=LLM_MODEL,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
            status="success",
        )
        await rate_limiter.consume(user_key, prompt_tokens + completion_tokens)

        yield f"event: done\ndata: {{}}\n\n"

    except Exception as e:
        latency_ms = (time.time() - start) * 1000
        await log_llm_call(
            endpoint="/api/rewrite/stream",
            model=LLM_MODEL,
            prompt_tokens=0,
            completion_tokens=0,
            latency_ms=latency_ms,
            status=f"error: {type(e).__name__}",
        )
        yield f"event: error\ndata: {str(e)}\n\n"


async def stream_coach(user_text: str, context: str, user_key: str = "unknown") -> AsyncGenerator[str, None]:
    """Stream the coach analysis response token-by-token via SSE."""
    from app.services.metrics import log_llm_call

    start = time.time()
    collected_output = []

    try:
        prompt_value = await coach_prompt.ainvoke({
            "user_text": user_text,
            "context": context,
        })

        async for chunk in llm.astream(prompt_value):
            token = chunk.content
            if token:
                collected_output.append(token)
                yield f"data: {token}\n\n"

        # Stream complete — log metrics
        full_output = "".join(collected_output)
        latency_ms = (time.time() - start) * 1000
        prompt_tokens = _estimate_tokens(user_text + context)
        completion_tokens = _estimate_tokens(full_output)

        await log_llm_call(
            endpoint="/api/coach/stream",
            model=LLM_MODEL,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
            status="success",
        )
        await rate_limiter.consume(user_key, prompt_tokens + completion_tokens)

        yield f"event: done\ndata: {{}}\n\n"

    except Exception as e:
        latency_ms = (time.time() - start) * 1000
        await log_llm_call(
            endpoint="/api/coach/stream",
            model=LLM_MODEL,
            prompt_tokens=0,
            completion_tokens=0,
            latency_ms=latency_ms,
            status=f"error: {type(e).__name__}",
        )
        yield f"event: error\ndata: {str(e)}\n\n"


def _estimate_tokens(text: str) -> int:
    """Rough token estimation (~4 chars per token for English)."""
    return max(1, len(text) // 4)
