"""
llm.py — LLM Service Layer (Async + Retry)
=============================================
Provides deep-profiled LLM invocation with per-step tracing:
prompt rendering, network call, output parsing, token estimation.
Includes retry logic for transient Groq API failures and
SSE streaming generators for real-time token delivery.
"""

import os
import re
import json
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
from app.services.profiler import init_traces, get_traces, trace, sizeof_fmt
from app.services.rate_limiter import rate_limiter
from app.utils.retry import async_retry
from app.utils.parsing import parse_llm_json

logger = logging.getLogger(__name__)

# ── LLM instance ──
logger.info("Initializing ChatGroq with model=%s, max_tokens=%s", LLM_MODEL, LLM_MAX_TOKENS)
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

class DisplayScriptStreamExtractor:
    """Extracts unescaped text content from `"display_script": "..."` in real-time."""
    def __init__(self):
        self.buffer = ""
        self.found_start = False
        self.is_done = False
        self.escaped = False

    def feed(self, chunk: str) -> str:
        if self.is_done or not chunk:
            return ""

        if not self.found_start:
            self.buffer += chunk
            if "<think>" in self.buffer:
                if "</think>" in self.buffer:
                    self.buffer = self.buffer.split("</think>", 1)[1]
                else:
                    return ""
            match = re.search(r'["“]display_script["”]\s*:\s*["“]', self.buffer)
            if match:
                self.found_start = True
                content_after = self.buffer[match.end():]
                self.buffer = ""
                return self._process_chars(content_after)
            return ""
        return self._process_chars(chunk)

    def _process_chars(self, text: str) -> str:
        output_chars = []
        for ch in text:
            if self.is_done:
                break
            if self.escaped:
                if ch == 'n':
                    output_chars.append('\n')
                elif ch == 't':
                    output_chars.append('\t')
                elif ch in ('"', '”'):
                    output_chars.append('"')
                elif ch == '\\':
                    output_chars.append('\\')
                else:
                    output_chars.append(ch)
                self.escaped = False
            elif ch == '\\':
                self.escaped = True
            elif ch in ('"', '”'):
                self.is_done = True
                break
            else:
                output_chars.append(ch)
        return "".join(output_chars)


async def stream_rewrite(
    user_text: str,
    target_style: str,
    voice: Optional[str] = None,
    speak: bool = False,
    user_key: str = "unknown"
) -> AsyncGenerator[str, None]:
    """Stream the VOXIS rewrite response token-by-token via SSE.

    When speak=True, automatically triggers Azure TTS upon script completion
    and streams the audio payload progressively without waiting for a full page refresh.
    Emits deep latency inspection traces in the 'done' event.
    """
    from app.services.metrics import log_llm_call
    from app.services.tts import async_synthesize_to_file, resolve_voice

    init_traces()
    start = time.time()
    collected_output = []
    extractor = DisplayScriptStreamExtractor()
    endpoint = "/api/rewrite-and-speak/stream" if speak else "/api/rewrite/stream"

    try:
        # Step 1: Render the prompt template
        with trace("Prompt Template Render", category="compute", template="voxis_prompt") as span:
            prompt_value = await voxis_prompt.ainvoke({
                "user_text": user_text,
                "target_style": target_style,
            })
            prompt_str = prompt_value.to_string()
            span["meta"]["prompt_chars"] = len(prompt_str)
            span["meta"]["prompt_size"] = sizeof_fmt(sys.getsizeof(prompt_str))

        # Step 2: Stream tokens from Groq
        with trace("Groq LLM Stream Generation", category="network", model=LLM_MODEL) as stream_span:
            stream_iter = llm.astream(prompt_value).__aiter__()

            # Sub-span: Time to First Token
            with trace("Time to First Token (TTFT)", category="network") as ttft_span:
                try:
                    first_chunk = await stream_iter.__anext__()
                    token = first_chunk.content
                    if token:
                        collected_output.append(token)
                        delta = extractor.feed(token)
                        if delta:
                            yield f"data: {json.dumps({'type': 'token', 'text': delta})}\n\n"
                    ttft_span["meta"]["first_token_chars"] = len(token) if token else 0
                except StopAsyncIteration:
                    pass

            # Sub-span: Remaining Token Generation & Emission
            with trace("Token Stream & Emission", category="network") as token_span:
                async for chunk in stream_iter:
                    token = chunk.content
                    if token:
                        collected_output.append(token)
                        delta = extractor.feed(token)
                        if delta:
                            payload = json.dumps({"type": "token", "text": delta})
                            yield f"data: {payload}\n\n"
                token_span["meta"]["token_chunks"] = len(collected_output)

            stream_span["meta"]["total_chunks"] = len(collected_output)
            stream_span["meta"]["output_chars"] = len("".join(collected_output))

        # Stream complete — parse final scripts
        full_output = "".join(collected_output)
        latency_ms = (time.time() - start) * 1000
        prompt_tokens = _estimate_tokens(user_text + target_style)
        completion_tokens = _estimate_tokens(full_output)

        # Step 3: Parse output
        with trace("Parse LLM JSON Output", category="compute") as span:
            try:
                parsed = parse_llm_json(full_output)
            except Exception as parse_err:
                logger.warning("Failed to parse JSON from streamed output: %s", parse_err)
                parsed = {"display_script": full_output, "tts_script": full_output}
            span["meta"]["json_keys"] = list(parsed.keys())
            span["meta"]["raw_output_chars"] = len(full_output)

        if parsed.get("error"):
            err_msg = parsed.get("message", "Model rejected the input.")
            yield f"data: {json.dumps({'type': 'error', 'message': err_msg})}\n\n"
            return

        display_script = parsed.get("display_script", "")
        tts_script = parsed.get("tts_script", display_script)

        # Emit the finalized scripts
        yield f"data: {json.dumps({'type': 'script_done', 'display_script': display_script, 'tts_script': tts_script})}\n\n"

        # If speak requested, synthesize audio progressively
        if speak and display_script:
            yield f"data: {json.dumps({'type': 'audio_status', 'status': 'synthesizing', 'message': 'Synthesizing voice audio with Azure Neural TTS...'})}\n\n"
            resolved_voice = resolve_voice(voice)

            with trace("Speech Synthesis Pipeline", category="io", voice=resolved_voice) as synth_span:
                tts_result = await async_synthesize_to_file(
                    tts_script=display_script,
                    voice=resolved_voice,
                    target_style=target_style,
                )
                synth_span["meta"]["success"] = tts_result.get("success", False)
                synth_span["meta"]["duration"] = tts_result.get("duration")

            if tts_result.get("success"):
                filename = tts_result.get("filename") or os.path.basename(tts_result.get("file_path", ""))
                audio_payload = {
                    "type": "audio_done",
                    "audio": {
                        "success": True,
                        "file_path": tts_result.get("file_path"),
                        "filename": filename,
                        "duration": tts_result.get("duration"),
                        "voice": tts_result.get("voice", resolved_voice),
                    }
                }
                yield f"data: {json.dumps(audio_payload)}\n\n"
            else:
                yield f"data: {json.dumps({'type': 'audio_error', 'message': tts_result.get('message', 'Audio synthesis failed')})}\n\n"

        await log_llm_call(
            endpoint=endpoint,
            model=LLM_MODEL,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
            status="success",
        )
        await rate_limiter.consume(user_key, prompt_tokens + completion_tokens)

        detailed_latencies = get_traces()
        total_elapsed_ms = (time.time() - start) * 1000
        yield f"data: {json.dumps({'type': 'done', 'total_latency_ms': total_elapsed_ms, 'detailed_latencies': detailed_latencies})}\n\n"
        yield f"event: done\ndata: {{}}\n\n"

    except Exception as e:
        latency_ms = (time.time() - start) * 1000
        logger.exception("Error in stream_rewrite: %s", e)
        await log_llm_call(
            endpoint=endpoint,
            model=LLM_MODEL,
            prompt_tokens=0,
            completion_tokens=0,
            latency_ms=latency_ms,
            status=f"error: {type(e).__name__}",
        )
        yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"


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
