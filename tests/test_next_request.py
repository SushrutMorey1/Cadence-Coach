"""
test_next_request.py — a bad request must not hurt the one after it
=====================================================================
A refusal, a request the reader abandons halfway, and a Groq rate limit are each
followed by a normal request. The second request must

  * reach its first token quickly (nothing inherited from the first),
  * wait silently for nothing (every long sleep is announced by a `rate_limited` event),
  * start from a clean slate (no task of the first request is still running), and
  * come out exactly like the same request sent on a fresh server.

Offline: a fake model, a fake Azure, a stubbed sleep. No Groq tokens are spent.
"""

import asyncio
import json
import time
from unittest.mock import patch

import groq as groq_sdk
import httpx
import pytest

from app.config import AUDIO_OUTPUT_DIR
from app.services import tts as tts_service
from app.services.director import stream_rewrite
from tests.helpers import FakeStreamLLM, remove_audio
from tests.test_director import SCRIPT_TEXT, fake_azure

REFUSAL = "ERROR: Please provide some text or a clear instruction."
FIRST_TOKEN_BUDGET_S = 1.0      # the fake model answers at once, so anything near this is a wait the request inherited
LONG_SLEEP_S = 0.5              # a sleep this long is a wait the reader can feel


class RateLimitedOnce(FakeStreamLLM):
    """Groq's per-minute 429 on the first call, a normal answer afterwards."""

    async def astream(self, prompt_value):
        if self.calls == 0:
            self.calls += 1
            self.prompts.append(prompt_value)
            resp = httpx.Response(429, headers={"retry-after": "2"}, request=httpx.Request("POST", "https://api.groq.com/x"))
            raise groq_sdk.RateLimitError("Rate limit reached for tokens per minute (TPM)", response=resp, body=None)
        async for chunk in super().astream(prompt_value):
            yield chunk


class Session:
    """One server's worth of requests against one model: events, first-token time and sleeps per request."""

    def __init__(self, llm, speak=False):
        self.llm, self.speak = llm, speak
        self.sleeps = []            # every asyncio.sleep() asked for, in order
        self.leftover = []          # tasks still running after each request

    async def _sleep(self, seconds, *args):
        self.sleeps.append(seconds)
        await real_sleep(min(seconds, 0.01))        # never really wait, but remember that we would have

    async def request(self, stop_after=None):
        """Run one request to its end, or abandon it after `stop_after` events. Returns (events, first_token_s, long_sleeps)."""
        events, first_token, started = [], None, time.perf_counter()
        mark = len(self.sleeps)
        gen = stream_rewrite("welcome back we ship monday", "calm CEO", speak=self.speak, user_key="test-next-request")
        try:
            async for raw in gen:
                event = json.loads(raw[len("data:"):])
                events.append(event)
                if event["type"] == "token" and first_token is None:
                    first_token = time.perf_counter() - started
                if stop_after and len(events) >= stop_after:
                    break
        finally:
            await gen.aclose()          # what Starlette does when the reader goes away
        self.leftover.append([t for t in asyncio.all_tasks() if t is not asyncio.current_task()])
        return events, first_token, [s for s in self.sleeps[mark:] if s >= LONG_SLEEP_S]


real_sleep = asyncio.sleep


def run_session(llm, scenario, speak=False):
    """Run `scenario(session)` on a fresh loop with sleeps stubbed and Azure faked; clean up any audio it made."""
    before = set(AUDIO_OUTPUT_DIR.glob("*.mp3"))
    session = Session(llm, speak=speak)

    async def go():
        with patch("app.services.llm.get_llm", return_value=llm), \
                patch("app.services.director.asyncio.sleep", session._sleep), \
                patch.object(tts_service, "_speak_ssml", side_effect=fake_azure([])):
            await scenario(session)

    try:
        asyncio.run(go())
    finally:
        remove_audio(*[p.name for p in set(AUDIO_OUTPUT_DIR.glob("*.mp3")) - before])
    return session


def kinds(events):
    return [e["type"] for e in events]


def assert_healthy(events, first_token, long_sleeps):
    """The second request: a complete, ordinary run that started at once and waited for nothing."""
    assert kinds(events)[-1] == "done" and "script_done" in kinds(events), kinds(events)
    assert "error" not in kinds(events) and "retry" not in kinds(events) and "rate_limited" not in kinds(events)
    assert first_token is not None and first_token < FIRST_TOKEN_BUDGET_S, first_token
    assert long_sleeps == [], f"the next request slept silently for {long_sleeps}"


def strip_timings(events):
    """An event stream without anything that legitimately differs between two runs."""
    drop = {"total_latency_ms", "script_ms", "detailed_latencies", "first_audio_ms", "ready_ms", "url", "filename"}
    return [{k: v for k, v in e.items() if k not in drop} for e in events]


@pytest.fixture(scope="module")
def baseline():
    """The same normal request on a fresh server, to compare the follow-ups with."""
    out = {}

    async def scenario(s):
        out["events"] = (await s.request())[0]

    run_session(FakeStreamLLM(SCRIPT_TEXT, step=15), scenario)
    return strip_timings(out["events"])


class TestAfterARefusal:
    def test_the_next_request_is_unaffected(self, baseline):
        # the refused request is asked twice (one visible re-ask); the follow-up gets the plain prompt, not the re-ask note
        llm = FakeStreamLLM([REFUSAL, REFUSAL, SCRIPT_TEXT], step=15)
        got = {}

        async def scenario(s):
            got["refused"] = await s.request()
            got["next"] = await s.request()

        session = run_session(llm, scenario)
        refused, (events, first_token, long_sleeps) = got["refused"][0], got["next"]
        assert kinds(refused) == ["retry", "error"] and "provide some text" in refused[-1]["message"]      # terminal, after one re-ask
        assert llm.calls == 3
        assert not isinstance(llm.prompts[2], list), "the follow-up inherited the re-ask note"
        assert_healthy(events, first_token, long_sleeps)
        assert strip_timings(events) == baseline
        assert session.leftover == [[], []]

    def test_a_refusal_ends_the_stream_nothing_more_arrives(self):
        llm = FakeStreamLLM(REFUSAL)

        async def scenario(s):
            events, _, _ = await s.request()
            assert kinds(events)[-1] == "error" and kinds(events).count("error") == 1
            assert not {"token", "script_done", "audio_segment", "done"} & set(kinds(events))

        run_session(llm, scenario, speak=True)


class TestAfterAnAbandonedRequest:
    @pytest.mark.parametrize("speak", [False, True], ids=["rewrite-only", "rewrite-and-speak"])
    def test_the_next_request_is_unaffected(self, speak, baseline):
        # slow enough that the first request is still being written when the reader leaves
        llm = FakeStreamLLM(SCRIPT_TEXT, step=6, delay=0.01)
        served, got = [], {}
        total_chunks = -(-len(SCRIPT_TEXT) // 6)
        plain_astream = llm.astream

        async def counting_astream(prompt_value):
            async for chunk in plain_astream(prompt_value):
                served.append(chunk)
                yield chunk
        llm.astream = counting_astream

        async def scenario(s):
            got["abandoned"] = await s.request(stop_after=3)
            assert s.leftover[0] == [], f"the abandoned request left tasks running: {s.leftover[0]}"
            assert len(served) < total_chunks / 2, "the model kept writing after the reader left"
            llm.delay, llm.step = 0.0, 15       # the baseline's chunking, so the two runs can be compared event for event
            got["next"] = await s.request()

        session = run_session(llm, scenario, speak=speak)
        abandoned, (events, first_token, long_sleeps) = got["abandoned"][0], got["next"]
        assert "done" not in kinds(abandoned) and llm.calls == 2
        assert_healthy(events, first_token, long_sleeps)
        if not speak:
            assert strip_timings(events) == baseline
        assert session.leftover == [[], []]


class TestAfterARateLimit:
    def test_the_wait_is_announced_and_the_next_request_does_not_inherit_it(self, baseline):
        llm = RateLimitedOnce(SCRIPT_TEXT, step=15)
        got = {}

        async def scenario(s):
            got["limited"] = await s.request()
            got["next"] = await s.request()

        session = run_session(llm, scenario)
        (limited, _, limited_sleeps), (events, first_token, long_sleeps) = got["limited"], got["next"]
        # visible: one rate_limited event, one matching sleep, and then the script
        assert kinds(limited).count("rate_limited") == 1 and limited[0]["wait_s"] == 2.0
        assert len(limited_sleeps) == 1 and limited_sleeps[0] == pytest.approx(2.5)
        assert kinds(limited)[-1] == "done"
        # the follow-up is an ordinary request: no wait, no rate_limited, nothing left over
        assert llm.calls == 3       # the limited call, its resumption, then the follow-up
        assert_healthy(events, first_token, long_sleeps)
        assert strip_timings(events) == baseline
        assert session.leftover == [[], []]

    def test_giving_up_on_a_rate_limited_wait_leaves_nothing_running(self):
        llm = RateLimitedOnce(SCRIPT_TEXT, step=15)

        async def scenario(s):
            events, _, _ = await s.request(stop_after=1)        # the reader leaves during the countdown
            assert kinds(events) == ["rate_limited"]
            assert s.leftover[0] == [], s.leftover[0]
            assert_healthy(*(await s.request()))

        run_session(llm, scenario)
