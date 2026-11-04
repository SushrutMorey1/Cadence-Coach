"""
director.py — The Speech Director's stream
=============================================
Model → script → voice, pipelined.

The model writes one annotated script (see prompts/voxis.py). As its tokens
arrive they go three places at once:

    Groq stream ──► ScriptStream ──┬─► `token` events   (annotations stripped: what the reader sees)
                                   └─► PieceCutter ──► Azure TTS, pieces rendered in parallel
                                                         └─► `audio_segment` events

so the first sentence is being spoken while the rest is still being written.
The stream is one SSE response; the two sides talk to it through a queue.

Events:  token · rate_limited · retry · script_done · audio_segment · audio_done · done · error
"""

import asyncio
import logging
import re
import time
from typing import AsyncGenerator, Optional

from langchain_core.messages import HumanMessage

from app.config import LLM_MODEL
from app.prompts.voxis import voxis_prompt
from app.services import llm
from app.services.llm import daily_limit_message, estimate_tokens, rate_limit_wait_seconds, rate_limited_event, sse, usage_from
from app.services.metrics import log_cycle, log_llm_call, log_tts_call
from app.services.profiler import add_span, get_traces, init_traces, trace
from app.services.rate_limiter import rate_limiter
from app.services.tts import PieceCutter, resolve_voice, strip_all_ssml, synthesize_pieces

logger = logging.getLogger(__name__)

_ERROR_RE = re.compile(r"(?i)^\s*error\s*[:\-–—]\s*")
_LETTER_RE = re.compile(r"[^\W\d_]")        # a letter in any alphabet

# The model sometimes refuses a perfectly good input ("groundnut is a legume, not a nut") on one run and
# writes the script on the next. A refusal changes nothing the reader has seen, so it is retried once with
# this reminder; real errors (empty, gibberish, harmful) are refused again and then shown.
_REFUSAL_RETRY_NOTE = ("Reconsider. Refuse only if user_text is empty, gibberish, not speech content, or promotes "
                       "hate or violence, or target_style is empty or gibberish. Opinions, loose wording, one-sided "
                       "claims, combined styles, a style that clashes with the content, missing details and a request "
                       "for another language are valid: "
                       "write the complete annotated script now, at full length, with an opening, development and "
                       "closing line.")


class ScriptStream:
    """
    Incremental reader for the model's output: the annotated script as plain
    text, or a single `ERROR: …` line.

    feed() returns (script_delta, display_delta): the new script text with its
    annotations (for the voice) and the same text as a reader should see it.
    The first few characters are held back until we know which of the two we
    are looking at; code fences and <think> blocks some models add are dropped.
    """

    _DECIDE_AT = 8          # characters needed to tell ERROR from script

    def __init__(self):
        self._head = ""
        self._mode: Optional[str] = None        # None (undecided) | "script" | "error"
        self.script = ""
        self.error = ""
        self._in_tag = False
        self._in_note = False
        self._pending_space = False
        self._newlines = 2                      # start of text counts as after a blank line

    def feed(self, chunk: str) -> tuple:
        if self._mode == "script":
            return self._accept(chunk)
        if self._mode == "error":
            self.error += chunk
            return "", ""
        self._head += chunk
        return self._decide(final=False)

    def finish(self) -> tuple:
        """The model has stopped: settle anything still held back."""
        if self._mode is None:
            return self._decide(final=True)
        return "", ""

    def _decide(self, final: bool) -> tuple:
        text = self._head.lstrip()
        if text.startswith("<think>"):
            end = text.find("</think>")
            if end == -1:
                self._head = text
                return "", ""
            text = text[end + len("</think>"):].lstrip()
        if text.startswith("```"):
            nl = text.find("\n")
            if nl == -1:
                self._head = text
                return "", ""
            text = text[nl + 1:]
        if len(text) < self._DECIDE_AT and "\n" not in text and not final:
            self._head = text
            return "", ""
        self._head = ""
        m = _ERROR_RE.match(text)
        if m:
            self._mode = "error"
            self.error = text[m.end():]
            return "", ""
        self._mode = "script"
        return self._accept(text)

    def _accept(self, text: str) -> tuple:
        text = text.replace("`", "")
        self.script += text
        return text, self._display(text)

    def _display(self, text: str) -> str:
        """Strip <tags> and [notes], collapse spaces, keep paragraph breaks (append-only, so safe to stream)."""
        out = []
        for ch in text:
            if self._in_tag:
                self._in_tag = ch != ">"
                continue
            if self._in_note:
                self._in_note = ch != "]"
                continue
            if ch == "<":
                self._in_tag = True
            elif ch == "[":
                self._in_note = True
            elif ch == "\n":
                self._pending_space = False
                if self._newlines < 2:
                    out.append("\n")
                    self._newlines += 1
            elif ch in " \t":
                if self._newlines == 0:
                    self._pending_space = True
            else:
                if self._pending_space:
                    out.append(" ")
                    self._pending_space = False
                out.append(ch)
                self._newlines = 0
        return "".join(out)


def _unusable_input(user_text: str, target_style: str) -> Optional[str]:
    """Input with no letters at all can be turned down at once, for certain and without spending any tokens."""
    if not _LETTER_RE.search(user_text or ""):
        return "The text has no words in it. Please write some speech content or a clear instruction."
    if not _LETTER_RE.search(target_style or ""):
        return "The target style has no words in it. Please describe a persona, tone or archetype."
    return None


async def stream_rewrite(
    user_text: str,
    target_style: str,
    voice: Optional[str] = None,
    speak: bool = False,
    user_key: str = "unknown",
) -> AsyncGenerator[str, None]:
    """Stream a rewrite as SSE; with speak=True the voice starts while the script is still being written."""
    problem = _unusable_input(user_text, target_style)
    if problem:
        yield sse({"type": "error", "message": problem})
        return
    init_traces()
    started = time.perf_counter()
    endpoint = "/api/rewrite-and-speak/stream" if speak else "/api/rewrite/stream"
    cycle_type = "Rewrite & Speak" if speak else "Rewrite Only"
    resolved_voice = resolve_voice(voice)

    events: asyncio.Queue = asyncio.Queue()       # SSE strings, plus finished tasks as sentinels
    pieces: asyncio.Queue = asyncio.Queue()       # script pieces for the voice (None = end)
    stream = ScriptStream()

    async def write_script() -> Optional[dict]:
        """Groq → ScriptStream → events (+ pieces for the voice). Returns the token usage."""
        nonlocal stream
        with trace("Prompt Template Render", category="compute", template="voxis_prompt") as span:
            prompt_value = await voxis_prompt.ainvoke({"user_text": user_text, "target_style": target_style})
            span["meta"]["prompt_chars"] = len(prompt_value.to_string())

        usage = None
        attempt = waits = 0
        refusal_retried = False
        messages = prompt_value
        while True:
            attempt += 1
            stream = ScriptStream()
            cutter = PieceCutter() if speak else None
            sent = False        # once anything reached the reader or the voice, we cannot start over

            async def deliver(script_delta: str, display_delta: str) -> None:
                nonlocal sent
                if display_delta:
                    sent = True
                    await events.put(sse({"type": "token", "text": display_delta}))
                if cutter and script_delta:
                    for piece in cutter.feed(script_delta):
                        sent = True
                        await pieces.put(piece)

            try:
                with trace("Groq LLM Stream Generation", category="network", model=LLM_MODEL, attempt=attempt) as span:
                    t_stream, first = time.perf_counter(), True
                    async for chunk in llm.get_llm().astream(messages):
                        usage = usage_from(chunk) or usage
                        token = chunk.content if isinstance(chunk.content, str) else ""
                        if not token:
                            continue
                        if first:
                            first = False
                            add_span("Time to First Token (TTFT)", t_stream, time.perf_counter(), category="network")
                        await deliver(*stream.feed(token))
                    await deliver(*stream.finish())
                    span["meta"]["output_chars"] = len(stream.script)
                if stream.error and not sent and not refusal_retried:
                    refusal_retried = True
                    attempt -= 1        # a second opinion is not a failed attempt
                    logger.warning("Model refused the input (%s) — asking once more.", stream.error.strip()[:120])
                    await events.put(sse({"type": "retry", "attempt": 2}))
                    messages = prompt_value.to_messages() + [HumanMessage(_REFUSAL_RETRY_NOTE)]
                    continue
                if cutter:
                    for piece in cutter.flush():
                        await pieces.put(piece)
                await pieces.put(None)
                return usage
            except Exception as err:
                if daily_limit_message(err):
                    raise RuntimeError(daily_limit_message(err))
                wait = rate_limit_wait_seconds(err)
                if wait is not None and not sent and waits < 2:
                    waits += 1
                    attempt -= 1        # waiting out a rate limit is not a failed attempt
                    logger.warning("Groq rate limit hit — waiting %.0fs before resuming the rewrite.", wait)
                    await events.put(rate_limited_event(wait))
                    await asyncio.sleep(wait + 0.5)
                    continue
                if sent or attempt >= 2:
                    raise
                logger.warning("Groq stream failed before any output (%s) — retrying once.", err)
                await events.put(sse({"type": "retry", "attempt": attempt + 1}))
                await asyncio.sleep(0.8)

    async def speak_pieces() -> dict:
        """pieces → Azure (parallel) → `audio_segment` events. Returns the pipeline summary."""
        async def source():
            while True:
                piece = await pieces.get()
                if piece is None:
                    return
                yield piece

        summary: dict = {}
        async for kind, info in synthesize_pieces(source(), voice=resolved_voice, target_style=target_style, content=user_text):
            if kind != "segment":
                summary = info
            elif info.get("ok"):
                await events.put(sse({"type": "audio_segment", "ok": True, "index": info["index"],
                                      "url": f"/audio/{info['filename']}", "duration": info["duration"],
                                      "directed": info["directed"], "ready_ms": info["ready_ms"]}))
            else:
                await events.put(sse({"type": "audio_segment", "ok": False, "index": info["index"],
                                      "error": info.get("error")}))
        return summary

    writer = asyncio.create_task(write_script())
    speaker = asyncio.create_task(speak_pieces()) if speak else None
    tasks = [t for t in (writer, speaker) if t]
    for task in tasks:
        task.add_done_callback(events.put_nowait)       # a finished task lands in the queue as its own sentinel

    script_ms: Optional[float] = None
    prompt_tokens = completion_tokens = 0
    token_source = "estimate"
    try:
        waiting = set(tasks)
        while waiting:
            item = await events.get()
            if isinstance(item, str):
                yield item
                continue
            waiting.discard(item)
            if item.exception():
                raise item.exception()
            if item is not writer:
                continue

            usage = item.result()
            script_ms = (time.perf_counter() - started) * 1000
            if usage:
                prompt_tokens, completion_tokens, token_source = usage.get("input_tokens", 0), usage.get("output_tokens", 0), "api"
            else:
                prompt_tokens, completion_tokens, token_source = estimate_tokens(user_text + target_style), estimate_tokens(stream.script), "estimate"
            if stream.error:
                await log_llm_call(endpoint=endpoint, model=LLM_MODEL, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                                   latency_ms=script_ms, status="rejected: INVALID_INPUT", user_key=user_key)
                yield sse({"type": "error", "message": stream.error.strip() or "The model rejected this input."})
                return
            if not stream.script.strip():
                raise RuntimeError("The model returned an empty script — please try again.")
            script = stream.script.strip()
            yield sse({"type": "script_done", "display_script": strip_all_ssml(script), "tts_script": script})

        audio: dict = speaker.result() if speaker else {}
        if speak:
            if audio.get("success"):
                yield sse({"type": "audio_done", "audio": {
                    "success": True, "filename": audio["filename"], "duration": audio["duration"], "voice": audio["voice"],
                    "style": audio["style"], "directed": audio["directed"], "direction": audio["direction"],
                    "segments": audio["segments"], "failed_segments": audio["failed_segments"],
                    "first_audio_ms": audio["first_audio_ms"]}})
            else:
                yield sse({"type": "audio_error", "message": audio.get("message", "Audio synthesis failed")})

        total_ms = (time.perf_counter() - started) * 1000
        traces = get_traces()
        yield sse({"type": "done", "total_latency_ms": total_ms, "script_ms": script_ms, "detailed_latencies": traces,
                   "token_source": token_source, "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                   "first_audio_ms": audio.get("first_audio_ms")})

        # Bookkeeping runs concurrently and after the reader already has everything.
        writes = [
            log_llm_call(endpoint=endpoint, model=LLM_MODEL, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                         latency_ms=script_ms or total_ms, status="success", user_key=user_key),
            rate_limiter.consume(user_key, prompt_tokens + completion_tokens),
            log_cycle(cycle_type, total_ms, traces, user_key=user_key),
        ]
        if speak:
            writes.append(log_tts_call(
                voice=resolved_voice, char_count=audio.get("char_count", 0), duration_sec=audio.get("duration"),
                status="success" if audio.get("success") else f"failed: {audio.get('message', 'unknown')}", user_key=user_key))
        await asyncio.gather(*writes, return_exceptions=True)

    except Exception as e:
        logger.exception("Error in stream_rewrite: %s", e)
        await log_llm_call(endpoint=endpoint, model=LLM_MODEL, prompt_tokens=0, completion_tokens=0,
                           latency_ms=(time.perf_counter() - started) * 1000, status=f"error: {type(e).__name__}", user_key=user_key)
        yield sse({"type": "error", "message": str(e)})
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
