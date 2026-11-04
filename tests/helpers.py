"""
helpers.py — Fakes shared by the test files
==============================================
A model that streams a canned reply, an Azure result, and an SSE parser,
so the whole request path can run offline.
"""

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace

import azure.cognitiveservices.speech as speechsdk

from app.config import AUDIO_OUTPUT_DIR

TEST_USER = "ip:testclient"     # what get_user_key() derives for FastAPI's TestClient


def sse_events(body: str) -> list:
    """Parse the `data: {json}` lines of an SSE body."""
    events = []
    for line in body.splitlines():
        if line.startswith("data:"):
            payload = line[5:].strip()
            if payload and payload != "{}":
                events.append(json.loads(payload))
    return events


class FakeStreamLLM:
    """
    Stands in for ChatGroq: `astream` yields the canned text in small chunks (optionally
    with a pause between them, like a model that is still writing); the last chunk carries
    usage and finish_reason the way Groq does. Several payloads are served in order.
    """

    def __init__(self, payloads, step=37, delay=0.0):
        self.payloads = [payloads] if isinstance(payloads, str) else list(payloads)
        self.step, self.delay, self.calls = step, delay, 0
        self.prompts = []           # what each call was given

    async def astream(self, prompt_value):
        text = self.payloads[min(self.calls, len(self.payloads) - 1)]
        self.calls += 1
        self.prompts.append(prompt_value)
        pieces = [text[i:i + self.step] for i in range(0, len(text), self.step)] or [""]
        for i, piece in enumerate(pieces):
            if self.delay:
                await asyncio.sleep(self.delay)
            last = i == len(pieces) - 1
            yield SimpleNamespace(
                content=piece,
                usage_metadata={"input_tokens": 900, "output_tokens": 300, "total_tokens": 1200} if last else None,
                response_metadata={"finish_reason": "stop"} if last else {},
            )


class FakeAzureResult:
    """What `_speak_ssml` returns: success (1.2 s of 'audio') or a cancellation."""

    def __init__(self, ok=True, data=b"ID3fake-mp3-bytes"):
        self.reason = speechsdk.ResultReason.SynthesizingAudioCompleted if ok else speechsdk.ResultReason.Canceled
        self.audio_data = data
        self.audio_duration = timedelta(seconds=1.2)
        self.cancellation_details = SimpleNamespace(
            reason=speechsdk.CancellationReason.Error, error_code="BadRequest", error_details="bad ssml",
        )


def remove_audio(*names) -> None:
    """Delete generated test MP3s (names or glob patterns inside AUDIO_OUTPUT_DIR)."""
    for name in names:
        for path in AUDIO_OUTPUT_DIR.glob(name):
            path.unlink()
