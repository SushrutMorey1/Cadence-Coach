"""
test_parallel_judge.py — The judge and the writer at the same time
====================================================================
With SAFETY_JUDGE and JUDGE_PARALLEL on, the writer starts the moment the
request arrives and the judge decides alongside it. The promises:

  * they really overlap: the writer is already producing while the judge thinks;
  * nothing is shown or spoken before the verdict;
  * a written request waits for the slower of the two, not for both;
  * a refused request has its writer cancelled, shows and speaks nothing, and
    leaves no task running, and neither does a request abandoned mid-verdict.

Offline: fakes with timestamps, so overlap and hold-back are measured, not assumed.
"""

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import patch

from app.services import tts as tts_service
from app.services.director import stream_rewrite
from app.services.judge import NOTE_TEXT, REFUSED_MESSAGE
from tests.helpers import FakeStreamLLM
from tests.test_director import SCRIPT_TEXT, cleanup_stream_audio, fake_azure
from tests.test_judge_pipeline import limit_error

JUDGE_DELAY = 0.3


class TimedJudge:
    """Answers `label` after `delay` seconds and records when it started and finished."""

    def __init__(self, label, delay=JUDGE_DELAY):
        self.label, self.delay, self.calls, self.started, self.finished = label, delay, 0, None, None

    async def astream(self, messages):
        self.calls += 1
        self.started = time.perf_counter()
        await asyncio.sleep(self.delay)
        self.finished = time.perf_counter()
        yield SimpleNamespace(content=self.label, usage_metadata={"input_tokens": 740, "output_tokens": 2, "total_tokens": 742},
                              response_metadata={"finish_reason": "stop"})


class TimedWriter(FakeStreamLLM):
    """A writer that records when it produced each chunk, so overlap with the judge can be measured."""

    def __init__(self, *args, fail_first=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.produced, self.fail_first = [], fail_first

    async def astream(self, prompt_value):
        if self.fail_first is not None and self.calls == 0:
            self.calls += 1
            raise self.fail_first
            yield  # pragma: no cover
        async for chunk in super().astream(prompt_value):
            self.produced.append(time.perf_counter())
            yield chunk


async def collect(gen):
    events, stamps = [], []
    async for raw in gen:
        events.append(json.loads(raw[len("data:"):]))
        stamps.append(time.perf_counter())
    return events, stamps


def run(*args, **kwargs):
    """_run_raw() without the `status` cue (it is covered in test_judge_pipeline.py), timestamps kept in step."""
    events, stamps, t0, leftover = _run_raw(*args, **kwargs)
    keep = [i for i, e in enumerate(events) if e["type"] != "status"]
    return [events[i] for i in keep], [stamps[i] for i in keep], t0, leftover


def _run_raw(judge, writer, speak=False, speak_fn=None):
    async def go():
        t0 = time.perf_counter()
        events, stamps = await collect(stream_rewrite("welcome back we ship monday", "calm CEO", speak=speak))
        return events, stamps, t0, [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]

    with patch("app.services.director.SAFETY_JUDGE", True), patch("app.services.director.JUDGE_PARALLEL", True), \
            patch("app.services.llm.get_judge_llm", return_value=judge), patch("app.services.llm.get_llm", return_value=writer), \
            patch("app.services.judge.JUDGE_TIMEOUT_S", 2.0):          # real sleeps: overlap is measured on real time
        if speak_fn:
            with patch.object(tts_service, "_speak_ssml", side_effect=speak_fn):
                return asyncio.run(go())
        return asyncio.run(go())


def kinds(events):
    return [e["type"] for e in events]


def test_parallel_is_the_default():
    from app import config
    assert config.JUDGE_PARALLEL is True


def test_the_writer_runs_while_the_judge_thinks_and_nothing_is_shown_before_the_verdict():
    judge, writer = TimedJudge("WRITE"), TimedWriter(SCRIPT_TEXT, step=12, delay=0.02)
    events, stamps, t0, leftover = run(judge, writer)
    assert writer.produced[0] < judge.finished, "the writer should already be producing while the judge decides"
    first_token = stamps[kinds(events).index("token")]
    assert first_token >= judge.finished, "a token reached the reader before the verdict"
    assert "script_done" in kinds(events) and kinds(events)[-1] == "done" and leftover == []
    # the reader waited for the slower of the two, not for both one after the other
    writer_alone = writer.produced[-1] - writer.produced[0] + 0.02
    assert stamps[-1] - t0 < JUDGE_DELAY + writer_alone, "judge and writer ran one after the other"


def test_write_note_sends_the_note_first_and_holds_the_voice_until_the_verdict():
    judge, writer, calls = TimedJudge("WRITE_NOTE"), TimedWriter(SCRIPT_TEXT, step=12, delay=0.02), []
    events, stamps, _, leftover = run(judge, writer, speak=True, speak_fn=fake_azure(calls))
    try:
        k = kinds(events)
        assert k[0] == "note" and events[0]["text"] == NOTE_TEXT and k.index("note") < k.index("token") < k.index("audio_segment")
        assert stamps[k.index("audio_segment")] >= judge.finished and leftover == []
    finally:
        cleanup_stream_audio(events)


def test_a_refusal_cancels_the_running_writer_and_nothing_is_written_or_spoken():
    judge, writer, calls = TimedJudge("REFUSE"), TimedWriter(SCRIPT_TEXT, step=4, delay=0.02), []
    events, _, _, leftover = run(judge, writer, speak=True, speak_fn=fake_azure(calls))
    total_chunks = -(-len(SCRIPT_TEXT) // 4)
    assert kinds(events) == ["error"] and events[0]["message"] == REFUSED_MESSAGE
    assert writer.calls == 1 and 0 < len(writer.produced) < total_chunks, "the writer should start and then be cancelled"
    assert calls == [] and leftover == []


def test_a_rate_limit_on_the_writer_while_the_judge_thinks_is_handled_after_the_verdict():
    judge, writer = TimedJudge("WRITE"), TimedWriter(SCRIPT_TEXT, step=15, fail_first=limit_error())
    events, _, _, leftover = run(judge, writer)
    k = kinds(events)
    assert k[0] == "rate_limited" and "script_done" in k and writer.calls == 2 and leftover == []


def test_a_request_abandoned_while_the_judge_thinks_leaves_nothing_running():
    judge, writer = TimedJudge("WRITE", delay=1.0), TimedWriter(SCRIPT_TEXT, step=4, delay=0.02)

    async def go():
        consumer = asyncio.create_task(collect(stream_rewrite("welcome back we ship monday", "calm CEO", speak=False)))
        await asyncio.sleep(0.2)                    # the judge is still thinking, the writer is running
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)
        await asyncio.sleep(0.05)
        return [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]

    with patch("app.services.director.SAFETY_JUDGE", True), patch("app.services.director.JUDGE_PARALLEL", True), \
            patch("app.services.llm.get_judge_llm", return_value=judge), patch("app.services.llm.get_llm", return_value=writer), \
            patch("app.services.judge.JUDGE_TIMEOUT_S", 2.0):
        leftover = asyncio.run(go())
    assert leftover == [] and writer.calls == 1
    total_chunks = -(-len(SCRIPT_TEXT) // 4)
    assert len(writer.produced) < total_chunks, "the writer kept going after the request was abandoned"


def test_the_trace_shows_whether_the_judge_or_the_writer_was_the_slower_one():
    from app.services.director import stream_rewrite

    def spans(parallel):
        judge, writer = TimedJudge("WRITE", delay=0.3), TimedWriter(SCRIPT_TEXT, step=12, delay=0.02)

        async def go():
            events, _ = await collect(stream_rewrite("welcome back we ship monday", "calm CEO", speak=False))
            return {s["name"]: s for s in next(e for e in events if e["type"] == "done")["detailed_latencies"]}

        with patch("app.services.director.SAFETY_JUDGE", True), patch("app.services.director.JUDGE_PARALLEL", parallel), \
                patch("app.services.llm.get_judge_llm", return_value=judge), patch("app.services.llm.get_llm", return_value=writer), \
                patch("app.services.judge.JUDGE_TIMEOUT_S", 2.0):
            return asyncio.run(go())

    parallel = spans(True)
    own = parallel["Writer first chunk (before the verdict)"]["latency_ms"]
    assert own < parallel["Safety Judge"]["latency_ms"] - 100, "the writer's own first chunk came well before the verdict"
    assert parallel["Time to First Token (TTFT)"]["latency_ms"] >= parallel["Safety Judge"]["latency_ms"] - 20
    assert "Writer first chunk (before the verdict)" not in spans(False)
