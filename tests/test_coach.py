"""
test_coach.py — The Communication Coach
==========================================
Measuring delivery (two-tier fillers with positions, pauses, fastest stretch),
feeding it to the prompt, the streamed-and-retried JSON analysis, and the
stored history. Everything runs offline.
"""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.prompts.coach import NO_AUDIO_NOTE, describe_speech_metrics
from app.services.coach_runs import list_coach_runs, save_coach_run
from app.services.stt import analyze_segments, detect_fillers
from tests.helpers import FakeStreamLLM, sse_events

client = TestClient(app)


def run(coro):
    return asyncio.run(coro)


# ══════════════════════════════════════════════
# Measuring delivery
# ══════════════════════════════════════════════

class TestFillerDetection:
    TEXT = "So, um, I think it's, like, huge, right? I like pizza so much. The the end."

    def test_two_tier_counts(self):
        out = detect_fillers(self.TEXT)
        assert out["counts"] == {"so": 1, "um": 1, "like": 1, "right": 1}
        assert out["total"] == 4
        assert out["repeated_words"] == 1

    def test_spans_point_at_the_words(self):
        out = detect_fillers(self.TEXT)
        starts = [s["start"] for s in out["spans"]]
        assert starts == sorted(starts)
        for s in out["spans"]:
            assert self.TEXT[s["start"]:s["end"]].lower() == s["word"]

    def test_content_words_are_not_fillers(self):
        out = detect_fillers("I like the plan. It feels right for us, so we will do it well.")
        assert out["total"] == 0

    def test_always_fillers_anywhere(self):
        out = detect_fillers("we basically you know actually did it")
        assert out["counts"] == {"basically": 1, "you know": 1, "actually": 1}


class TestSegmentAnalysis:
    def test_pauses_and_pace(self):
        segments = [
            {"start": 0.0, "end": 2.0, "text": "one two three four five"},
            {"start": 3.0, "end": 4.0, "text": "six seven"},
            {"start": 4.2, "end": 7.2, "text": "a b c d e f g h i j k l"},
        ]
        out = analyze_segments(segments)
        assert out["long_pauses"] == 1
        assert out["longest_pause_sec"] == 1.0
        assert out["fastest_stretch"]["wpm"] == 240
        assert out["slowest_stretch"]["wpm"] == 150

    def test_empty(self):
        out = analyze_segments([])
        assert out["long_pauses"] == 0 and out["fastest_stretch"] is None


class TestDescribeSpeechMetrics:
    def test_none_means_no_audio(self):
        assert describe_speech_metrics(None) == NO_AUDIO_NOTE
        assert describe_speech_metrics({}) == NO_AUDIO_NOTE

    def test_renders_facts(self):
        text = describe_speech_metrics({
            "wpm": 182.4, "wpm_rating": "too_fast", "duration_sec": 94, "word_count": 285,
            "filler_count": 12, "filler_rate_pct": 4.2, "filler_words": {"um": 6, "like": 4, "you know": 2},
            "long_pauses": 3, "longest_pause_sec": 1.9, "fastest_stretch": {"wpm": 214, "text": "pricing is simple"},
        })
        assert "182 words per minute" in text and "well above" in text
        assert '"um" ×6' in text and "4.2%" in text
        assert "Long pauses (over 0.7 s): 3, longest 1.9 s" in text
        assert "214 wpm" in text


# ══════════════════════════════════════════════
# The analysis stream
# ══════════════════════════════════════════════

COACH_JSON = {
    "overall_score": 7.4,
    "summary": "Clear structure, soft close. The pricing section rushed.",
    "delivery_notes": "You hit 182 wpm exactly where listeners needed time.",
    "vocabulary": {"score": 7.0, "strengths": ["concrete numbers"], "improvements": ["fewer hedges"],
                   "weak_words": [{"word": "very", "suggestion": "sharply", "reason": "vague"},
                                  {"word": "things", "suggestion": "features", "reason": "generic"}],
                   "power_words_used": ["ship"]},
    "tone_and_emotion": {"score": 7.5, "detected_tone": "confident", "emotional_arc": "builds then flattens", "feedback": ["end higher"]},
    "voice_modulation": {"score": 6.5, "suggestions": [{"text_segment": "we ship Monday", "instruction": "Slow down and drop pitch.", "type": "combined"},
                                                        {"text_segment": "three customers", "instruction": "Punch the number.", "type": "volume"}]},
    "pacing_and_rhythm": {"score": 6.8, "avg_sentence_length": "varied", "variation_quality": "good contrast", "feedback": ["pause before the ask"]},
    "emphasis_and_delivery": {"score": 7.2, "key_moments": [{"text_segment": "we ship Monday", "technique": "Pause two beats after.", "reason": "It is the promise."},
                                                             {"text_segment": "three customers", "technique": "Lean in.", "reason": "Proof point."}]},
    "clarity_and_structure": {"score": 8.0, "opening_strength": "strong hook", "closing_strength": "fizzles", "transitions": "smooth", "feedback": ["restate the ask"]},
    "top_3_actions": ["Replace the 7× 'um' with a silent beat.", "Slow the pricing section to 150 wpm.", "End on the ask."],
}


class TestCoachStream:
    def test_streams_tokens_then_validated_result(self):
        fake = FakeStreamLLM(json.dumps(COACH_JSON))
        with patch("app.services.llm.get_llm", return_value=fake):
            res = client.post("/api/coach/stream", json={
                "user_text": "We ship Monday. Three customers already pay us.",
                "speech_metrics": {"wpm": 182, "filler_count": 7, "filler_rate_pct": 4.1, "filler_words": {"um": 7}},
            })
        assert res.status_code == 200
        events = sse_events(res.text)
        kinds = [e["type"] for e in events]
        assert kinds.count("token") > 3 and kinds[-2:] == ["result", "done"]
        result = next(e for e in events if e["type"] == "result")
        assert result["overall_score"] == 7.4
        assert result["delivery_notes"].startswith("You hit 182")
        assert result["vocabulary"]["score"] == 7.0
        done = next(e for e in events if e["type"] == "done")
        assert done["token_source"] == "api" and done["prompt_tokens"] == 900
        assert any(t.get("start_ms") is not None for t in done["detailed_latencies"])

    def test_the_measured_delivery_reaches_the_prompt(self):
        seen = {}

        class Spy(FakeStreamLLM):
            async def astream(self, prompt_value):
                seen["prompt"] = prompt_value.to_string()
                async for chunk in super().astream(prompt_value):
                    yield chunk

        with patch("app.services.llm.get_llm", return_value=Spy(json.dumps(COACH_JSON))):
            client.post("/api/coach/stream", json={"user_text": "We ship Monday and three customers pay us.",
                                                   "speech_metrics": {"wpm": 182, "wpm_rating": "too_fast", "filler_count": 7,
                                                                      "filler_rate_pct": 4.1, "filler_words": {"um": 7}}})
        assert "182 words per minute" in seen["prompt"] and '"um" ×7' in seen["prompt"]

    def test_retries_once_on_bad_json(self):
        fake = FakeStreamLLM(["{this is not json", json.dumps(COACH_JSON)])
        with patch("app.services.llm.get_llm", return_value=fake):
            res = client.post("/api/coach/stream", json={"user_text": "Hello there, team. Today we ship."})
        kinds = [e["type"] for e in sse_events(res.text)]
        assert "retry" in kinds and "result" in kinds and fake.calls == 2

    def test_groq_rate_limit_waits_visibly_then_resumes(self):
        import groq as groq_sdk
        import httpx

        class RateLimitedOnce(FakeStreamLLM):
            async def astream(self, prompt_value):
                if self.calls == 0:
                    self.calls += 1
                    resp = httpx.Response(429, headers={"x-ratelimit-reset-tokens": "0.3s"},
                                          request=httpx.Request("POST", "https://api.groq.com/x"))
                    raise groq_sdk.RateLimitError("Rate limit reached", response=resp, body=None)
                async for chunk in super().astream(prompt_value):
                    yield chunk

        fake = RateLimitedOnce(json.dumps(COACH_JSON))
        with patch("app.services.llm.get_llm", return_value=fake):
            res = client.post("/api/coach/stream", json={"user_text": "Hello there, team. Today we ship."})
        events = sse_events(res.text)
        kinds = [e["type"] for e in events]
        assert kinds[0] == "rate_limited" and events[0]["wait_s"] == 1.0     # clamped to the 1 s floor
        assert "result" in kinds and fake.calls == 2

    def test_model_rejection_is_an_error_event(self):
        fake = FakeStreamLLM(json.dumps({"error": True, "error_code": "INVALID_INPUT", "message": "Not speech."}))
        with patch("app.services.llm.get_llm", return_value=fake):
            res = client.post("/api/coach/stream", json={"user_text": "SELECT * FROM users;"})
        events = sse_events(res.text)
        assert events[-1]["type"] == "error" and "Not speech" in events[-1]["message"]

    def test_missing_text_is_a_validation_error(self):
        assert client.post("/api/coach/stream", json={}).status_code == 422


# ══════════════════════════════════════════════
# History
# ══════════════════════════════════════════════

class TestCoachHistory:
    def test_a_streamed_run_is_stored_with_its_metrics(self):
        fake = FakeStreamLLM(json.dumps(COACH_JSON))
        with patch("app.services.llm.get_llm", return_value=fake):
            client.post("/api/coach/stream", json={
                "user_text": "We ship Monday.",
                "speech_metrics": {"wpm": 182, "wpm_rating": "too_fast", "filler_count": 7, "filler_rate_pct": 4.1},
            })
        hist = client.get("/api/coach/history?limit=5").json()
        assert hist["count"] >= 1
        latest = hist["runs"][0]
        assert latest["source"] == "audio" and latest["overall_score"] == 7.4
        assert latest["metrics"]["wpm"] == 182
        assert latest["scores"]["clarity_and_structure"] == 8.0
        assert len(latest["actions"]) == 3

    def test_delta_against_previous_run(self):
        user = "user:delta-test@example.com"
        run(save_coach_run(user, {**COACH_JSON, "overall_score": 6.0}, "first take", source="text"))
        run(save_coach_run(user, {**COACH_JSON, "overall_score": 7.5}, "second take", source="audio",
                           speech_metrics={"wpm": 150, "filler_count": 2}))
        runs = run(list_coach_runs(user, limit=5))
        assert [r["overall_score"] for r in runs] == [7.5, 6.0]
        assert runs[0]["delta"] == 1.5 and runs[1]["delta"] is None
        assert runs[0]["source"] == "audio" and runs[0]["metrics"]["wpm"] == 150


class TestTranscribe:
    def test_transcribe_returns_delivery_metrics_and_logs_a_cycle(self):
        fake = {
            "success": True, "text": "So, um, hello.", "duration_sec": 2.0, "word_count": 3, "wpm": 90.0,
            "wpm_rating": "too_slow", "filler_words": {"so": 1, "um": 1}, "filler_count": 2, "filler_rate_pct": 66.7,
            "filler_rating": "excessive", "filler_spans": [{"start": 0, "end": 2, "word": "so"}], "repeated_words": 0,
            "long_pauses": 0, "longest_pause_sec": 0.0, "fastest_stretch": None, "slowest_stretch": None,
            "segments": [], "message": "ok",
        }
        with patch("app.routes.transcribe.async_transcribe_audio", new_callable=AsyncMock, return_value=fake):
            res = client.post("/api/transcribe", files={"file": ("clip.webm", b"\x1a\x45\xdf\xa3fake", "audio/webm")})
        assert res.status_code == 200
        body = res.json()
        assert body["filler_spans"][0]["word"] == "so"
        assert any(t.get("start_ms") is not None for t in body["detailed_latencies"])
        cycles = client.get("/api/metrics").json()["recent_cycles"]
        assert any(c["cycle_type"] == "Transcribe" for c in cycles)

    def test_rejects_non_audio_and_empty_files(self):
        assert client.post("/api/transcribe", files={"file": ("a.txt", b"hello", "text/plain")}).status_code == 415
        assert client.post("/api/transcribe", files={"file": ("a.webm", b"", "audio/webm")}).status_code == 400


@pytest.mark.parametrize("wpm,expected", [(0, "unknown"), (100, "too_slow"), (120, "slow"), (150, "optimal"), (170, "fast"), (200, "too_fast")])
def test_pace_bands(wpm, expected):
    from app.services.stt import _rate_wpm
    assert _rate_wpm(wpm) == expected
