"""
test_judge_pipeline.py — The safety judge in front of the writer
==================================================================
With SAFETY_JUDGE on, one short judge call decides before anything is written:
WRITE and WRITE_NOTE run the writer-only prompt (WRITE_NOTE also sends the fixed
note first); REFUSE, UNSURE twice, or a judge that is too slow twice end the
stream with one `error` event and nothing written or spoken. A forced-choice
retry follows UNSURE or a slow judge. Offline: fake judge, fake writer, fake Azure.
"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import groq as groq_sdk
import httpx
from fastapi.testclient import TestClient

from app.main import app
from app.prompts.judge import FORCED_CHOICE_NOTE, JUDGE_HUMAN_PROMPT, JUDGE_SYSTEM_PROMPT
from app.prompts.voxis import HUMAN_PROMPT, SYSTEM_PROMPT, WRITER_HUMAN_PROMPT, WRITER_SYSTEM_PROMPT
from app.services import tts as tts_service
from app.services.judge import (NOTE_TEXT, REFUSED_MESSAGE, SLOW_MESSAGE, TONE_MESSAGE, UNSURE_MESSAGE, UNUSABLE_MESSAGE,
                                parse_label)
from tests.helpers import FakeStreamLLM, sse_events
from tests.test_director import SCRIPT_TEXT, cleanup_stream_audio, fake_azure

client = TestClient(app)
ROOT = Path(__file__).resolve().parent.parent


def limit_error(daily=False):
    resp = httpx.Response(429, headers={"retry-after": "1"}, request=httpx.Request("POST", "https://api.groq.com/x"))
    text = ("Rate limit reached for model `m` on tokens per day (TPD): Limit 200000, Used 199999, Requested 700. Please try again in 7m5s."
            if daily else "Rate limit reached for model `m` on tokens per minute (TPM): Limit 8000, Used 7900, Requested 700")
    return groq_sdk.RateLimitError(text, response=resp, body=None)


class FakeJudge:
    """Answers with the next scripted reply: a label, ("slow", reply) to answer after the timeout, or an exception."""

    def __init__(self, *replies):
        self.replies, self.calls, self.messages = list(replies), 0, []

    async def astream(self, messages):
        reply = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        self.messages.append(messages)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, tuple):
            await asyncio.sleep(1.0)        # longer than the patched timeout
            reply = reply[1]
        yield SimpleNamespace(content=reply, usage_metadata={"input_tokens": 640, "output_tokens": 2, "total_tokens": 642},
                              response_metadata={"finish_reason": "stop"})


def post(judge, writer=None, speak_fn=None, path="/api/rewrite/stream", judge_on=True, parallel=False, **extra):
    """parallel=False runs the judge before the writer (series), so `writer.calls == 0` shows a refused request never
    reached the writer; the parallel tests check the same promises with the writer already running."""
    writer = writer or FakeStreamLLM(SCRIPT_TEXT, step=15)
    body = {"user_text": "welcome back we ship monday", "target_style": "calm CEO", **extra}
    with patch("app.services.director.SAFETY_JUDGE", judge_on), patch("app.services.director.JUDGE_PARALLEL", parallel), \
            patch("app.services.judge.JUDGE_TIMEOUT_S", 0.3), \
            patch("app.services.llm.get_llm", return_value=writer), patch("app.services.llm.get_judge_llm", return_value=judge):
        if speak_fn is None:
            return sse_events(client.post(path, json=body).text), writer
        with patch.object(tts_service, "_speak_ssml", side_effect=speak_fn):
            return sse_events(client.post(path, json=body).text), writer


def kinds(events):
    return [e["type"] for e in events]


# ── the prompts ──

def test_the_app_judge_prompt_is_exactly_the_evaluated_judge_v5():
    system, human = (ROOT / "evals/variants/judge_v5.txt").read_text(encoding="utf-8").split("=== HUMAN ===")
    assert JUDGE_SYSTEM_PROMPT.strip() == system.replace("=== SYSTEM ===", "").strip()
    assert JUDGE_HUMAN_PROMPT.strip() == human.strip()


def test_the_writer_prompt_is_the_app_prompt_without_the_way_to_refuse_and_matches_the_eval():
    from evals.run_safety_eval import build_prompt
    assert "## ERRORS" not in WRITER_SYSTEM_PROMPT and "ERROR:" not in WRITER_SYSTEM_PROMPT and "ERROR" not in WRITER_HUMAN_PROMPT
    assert WRITER_SYSTEM_PROMPT.startswith(SYSTEM_PROMPT.split("## ERRORS")[0].replace(" A style that is unrecognisable or impossible to model: output the error line.", "")) and "Never explain your choices" in WRITER_SYSTEM_PROMPT
    prompt, system = build_prompt(writer_only=True)
    assert system == WRITER_SYSTEM_PROMPT        # the writer-only prompt the eval measured is the one that ships
    assert prompt.messages[1].prompt.template == WRITER_HUMAN_PROMPT


def test_a_label_is_read_even_with_a_preamble():
    assert parse_label("WRITE") == "WRITE" and parse_label("<think>x</think>REFUSE") == "REFUSE" and parse_label("hmm") is None


# ── off by default ──

def test_with_the_judge_off_nothing_changes():
    judge = FakeJudge(RuntimeError("the judge must not be called"))
    events, writer = post(judge, judge_on=False)
    assert judge.calls == 0 and "script_done" in kinds(events) and kinds(events)[-1] == "done"
    assert "## ERRORS" in writer.prompts[0].to_string()          # the full prompt, as before
    assert next(e for e in events if e["type"] == "done")["judge"] is None


# ── the judge decides ──

def test_write_runs_the_writer_only_prompt_and_counts_the_judges_tokens():
    with patch("app.services.director.log_llm_call", new_callable=AsyncMock) as logged:
        events, writer = post(FakeJudge("WRITE"))
    assert "script_done" in kinds(events) and "note" not in kinds(events) and kinds(events)[-1] == "done"
    assert "## ERRORS" not in writer.prompts[0].to_string() and writer.calls == 1
    done = next(e for e in events if e["type"] == "done")
    assert done["judge"]["label"] == "WRITE" and done["judge"]["attempts"] == 1
    assert done["prompt_tokens"] == 900 + 640 and done["completion_tokens"] == 300 + 2
    assert any(c.kwargs.get("endpoint") == "safety-judge" and c.kwargs.get("status") == "judge: WRITE" for c in logged.call_args_list)
    assert any(t["name"] == "Safety Judge" for t in done["detailed_latencies"])


def test_write_note_sends_the_fixed_note_before_anything_is_written_or_spoken():
    calls = []
    events, _ = post(FakeJudge("WRITE_NOTE"), FakeStreamLLM(SCRIPT_TEXT, step=12, delay=0.01), fake_azure(calls),
                     path="/api/rewrite-and-speak/stream", voice="davis")
    try:
        k = kinds(events)
        assert k[0] == "note" and events[0]["text"] == NOTE_TEXT
        assert k.index("note") < k.index("token") < k.index("audio_segment") and k[-1] == "done"
    finally:
        cleanup_stream_audio(events)


def test_refuse_ends_the_stream_with_nothing_written_or_spoken_and_charges_only_the_judge():
    calls, writer = [], FakeStreamLLM(SCRIPT_TEXT)
    with patch("app.services.director.rate_limiter") as limiter:
        limiter.consume = AsyncMock()
        events, _ = post(FakeJudge("REFUSE"), writer, fake_azure(calls), path="/api/rewrite-and-speak/stream")
    assert kinds(events) == ["error"] and events[0]["message"] == REFUSED_MESSAGE
    assert writer.calls == 0 and calls == []
    limiter.consume.assert_awaited_once()
    assert limiter.consume.await_args.args[1] == 642


# ── the retry ──

def test_unsure_gets_one_forced_choice_retry_that_can_decide():
    judge = FakeJudge("UNSURE", "WRITE")
    events, writer = post(judge)
    assert judge.calls == 2 and "script_done" in kinds(events) and writer.calls == 1
    assert FORCED_CHOICE_NOTE not in str(judge.messages[0]) and judge.messages[1][-1].content == FORCED_CHOICE_NOTE
    assert next(e for e in events if e["type"] == "done")["judge"]["attempts"] == 2


def test_unsure_twice_is_refused_with_a_rephrase_message():
    judge, writer = FakeJudge("UNSURE", "UNSURE"), FakeStreamLLM(SCRIPT_TEXT)
    events, _ = post(judge, writer)
    assert judge.calls == 2 and writer.calls == 0
    assert kinds(events) == ["error"] and events[0]["message"] == UNSURE_MESSAGE


def test_a_slow_judge_gets_one_retry():
    judge = FakeJudge(("slow", "WRITE"), "WRITE")
    events, _ = post(judge)
    assert judge.calls == 2 and "script_done" in kinds(events)


def test_a_judge_that_is_slow_twice_ends_with_a_try_again_message_not_a_refusal():
    judge, writer = FakeJudge(("slow", "WRITE"), ("slow", "WRITE")), FakeStreamLLM(SCRIPT_TEXT)
    events, _ = post(judge, writer)
    assert judge.calls == 2 and writer.calls == 0
    assert kinds(events) == ["error"] and events[0]["message"] == SLOW_MESSAGE


def test_a_reply_without_a_label_counts_as_unsure():
    judge = FakeJudge("I think this is fine", "WRITE_NOTE")
    events, _ = post(judge)
    assert judge.calls == 2 and kinds(events)[0] == "note" and "script_done" in kinds(events)


# ── rate limits on the judge's call ──

def test_a_per_minute_limit_on_the_judge_is_waited_out_visibly_and_is_not_an_attempt():
    judge = FakeJudge(limit_error(), "WRITE")
    with patch("app.services.judge.asyncio.sleep", new_callable=AsyncMock):
        events, _ = post(judge)
    assert kinds(events)[0] == "rate_limited" and "script_done" in kinds(events)
    assert next(e for e in events if e["type"] == "done")["judge"]["attempts"] == 1


def test_the_daily_limit_on_the_judge_ends_the_request_at_once():
    judge, writer = FakeJudge(limit_error(daily=True)), FakeStreamLLM(SCRIPT_TEXT)
    events, _ = post(judge, writer)
    assert judge.calls == 1 and writer.calls == 0
    assert kinds(events) == ["error"] and "daily token allowance" in events[0]["message"]


# ── nothing is left running ──

def test_a_refused_request_leaves_no_task_running():
    from app.services.director import stream_rewrite

    async def go():
        events = [json.loads(raw[len("data:"):]) async for raw in stream_rewrite("hello there friends", "calm", speak=True)]
        return events, [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]

    with patch("app.services.director.SAFETY_JUDGE", True), patch("app.services.director.JUDGE_PARALLEL", False), \
            patch("app.services.llm.get_judge_llm", return_value=FakeJudge("REFUSE")), \
            patch("app.services.llm.get_llm", return_value=FakeStreamLLM(SCRIPT_TEXT)):
        events, leftover = asyncio.run(go())
    assert kinds(events) == ["error"] and leftover == []


def test_the_writer_is_todays_prompt_minus_only_the_way_to_refuse():
    import difflib
    removed = [d[2:] for d in difflib.ndiff(SYSTEM_PROMPT.splitlines(), WRITER_SYSTEM_PROMPT.splitlines()) if d.startswith("- ")]
    added = [d[2:] for d in difflib.ndiff(SYSTEM_PROMPT.splitlines(), WRITER_SYSTEM_PROMPT.splitlines()) if d.startswith("+ ")]
    style_error = " A style that is unrecognisable or impossible to model: output the error line."
    last_resort = "Those are the only errors, and an error is a last resort: when in doubt, write the script. "
    assert len(removed) == 5 and removed[0].startswith("2. Resolve vague styles") and style_error in removed[0]
    assert removed[1] == "## ERRORS" and removed[2].startswith("If user_text is empty") and removed[3].startswith("ERROR: <")
    assert removed[4].startswith("Those are the only errors")
    assert added == [removed[0].replace(style_error, ""), "## ALWAYS WRITE THE SCRIPT", removed[4].replace(last_resort, "")]
    # all the writing guidance that sat inside ERRORS is still there, word for word
    assert '"our app saves you time" or "we won the contract" must be written up as a finished piece' in WRITER_SYSTEM_PROMPT
    assert "do not correct, fact-check or refuse it" in WRITER_SYSTEM_PROMPT
    assert WRITER_HUMAN_PROMPT == HUMAN_PROMPT.replace(" (or the single ERROR line)", "")


# ── judge_v5: a tone refusal and an unusable request get their own message ──

def test_judge_v5_is_v3_plus_two_labels_and_v3_is_untouched():
    v3 = (ROOT / "evals/variants/judge_v3.txt").read_text(encoding="utf-8")
    v5 = (ROOT / "evals/variants/judge_v5.txt").read_text(encoding="utf-8")
    assert "REFUSE_TONE" not in v3 and "UNUSABLE" not in v3
    assert "REFUSE_TONE:" in v5 and "UNUSABLE:" in v5 and "a short draft that does carry a message" in v5.lower()
    refuse_line = next(line for line in v5.splitlines() if line.startswith("REFUSE:"))
    assert "funeral" not in refuse_line and "Holocaust" not in refuse_line          # moved to REFUSE_TONE
    assert "thin or vague drafts that still carry a message" in v5
    assert FORCED_CHOICE_NOTE.count("REFUSE_TONE") == 1 and "UNUSABLE" in FORCED_CHOICE_NOTE


def test_parse_label_tells_refuse_tone_from_refuse():
    assert parse_label("REFUSE_TONE") == "REFUSE_TONE" and parse_label("REFUSE") == "REFUSE" and parse_label("unusable") == "UNUSABLE"


def test_a_tone_refusal_says_it_is_about_the_style_not_harm():
    writer = FakeStreamLLM(SCRIPT_TEXT)
    events, _ = post(FakeJudge("REFUSE_TONE"), writer)
    assert kinds(events) == ["error"] and events[0]["message"] == TONE_MESSAGE and writer.calls == 0
    assert "harm" not in TONE_MESSAGE and "hatred" not in TONE_MESSAGE


def test_an_unusable_request_asks_for_more_and_writes_nothing():
    writer = FakeStreamLLM(SCRIPT_TEXT)
    events, _ = post(FakeJudge("UNUSABLE"), writer)
    assert kinds(events) == ["error"] and events[0]["message"] == UNUSABLE_MESSAGE and writer.calls == 0
    assert REFUSED_MESSAGE != UNUSABLE_MESSAGE != TONE_MESSAGE


def test_the_facts_rule_is_kept_for_later_but_in_no_prompt():
    from app.prompts.voxis import FACTS_RULE
    assert "This rule covers facts only" in FACTS_RULE and "## FACTS" not in WRITER_SYSTEM_PROMPT and "## FACTS" not in SYSTEM_PROMPT


# ── the judge on its own model ──

def test_on_the_writers_model_the_judge_is_a_bound_copy():
    from app.services import llm as llm_service
    with patch.object(llm_service, "JUDGE_MODEL", llm_service.LLM_MODEL):
        judge = llm_service.get_judge_llm()
    assert judge.kwargs == {"temperature": 0, "max_tokens": llm_service.JUDGE_MAX_TOKENS}


def test_a_reasoning_judge_model_gets_room_to_think_and_its_reasoning_hidden():
    from app.services import llm as llm_service
    with patch.object(llm_service, "JUDGE_MODEL", "openai/gpt-oss-20b"), patch.object(llm_service, "_judge_llm", None):
        judge = llm_service.get_judge_llm()
        assert llm_service.get_judge_llm() is judge                         # one client, reused
    assert judge.model_name == "openai/gpt-oss-20b" and judge.temperature < 0.001 and judge.max_retries == 0   # LangChain stores 0 as 1e-08
    assert judge.max_tokens == llm_service.JUDGE_REASONING_MAX_TOKENS and judge.reasoning_effort == "low"
    assert judge.reasoning_format == "hidden"


def test_a_non_reasoning_judge_model_keeps_the_short_label_budget():
    from app.services import llm as llm_service
    with patch.object(llm_service, "JUDGE_MODEL", "some/other-model"), patch.object(llm_service, "_judge_llm", None):
        judge = llm_service.get_judge_llm()
    assert judge.max_tokens == llm_service.JUDGE_MAX_TOKENS and judge.reasoning_effort is None
