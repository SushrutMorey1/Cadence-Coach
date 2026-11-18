"""
test_latency_check.py — the latency runner, offline
=====================================================
The three set-ups really differ (no judge, judge then writer, both at once), a
written request is timed to its first word and a refused one to its error,
and the summary table has one row per case.
"""

import asyncio
from unittest.mock import patch

from evals.run_latency_check import summary, time_one
from tests.helpers import FakeStreamLLM
from tests.test_director import SCRIPT_TEXT
from tests.test_parallel_judge import TimedJudge

CASE = {"id": "c1", "user_text": "welcome back we ship monday", "target_style": "calm CEO"}


def timed(setup, label="WRITE"):
    writer, judge = FakeStreamLLM(SCRIPT_TEXT, step=12, delay=0.05), TimedJudge(label, delay=0.3)
    with patch("app.services.llm.get_llm", return_value=writer), patch("app.services.llm.get_judge_llm", return_value=judge):
        return asyncio.run(time_one(CASE, setup)), writer, judge


def test_today_never_calls_the_judge_and_parallel_beats_series():
    today, _, judge_today = timed("today")
    series, _, _ = timed("series")
    parallel, _, _ = timed("parallel")
    assert judge_today.calls == 0 and today["first_word_s"] < 0.3
    assert series["first_word_s"] > 0.3 + 0.05 and parallel["first_word_s"] < series["first_word_s"]


def test_a_refusal_is_timed_to_its_error_and_the_summary_has_one_row_per_case():
    refused, writer, _ = timed("series", "REFUSE")
    assert refused["refused_s"] is not None and refused["first_word_s"] is None and writer.calls == 0
    lines = summary([refused, dict(refused, setup="today", refused_s=None, first_word_s=0.4)])
    assert lines[2].startswith("| c1 |") and "refused" in lines[2] and "first word" in lines[2]


def test_the_voice_mode_records_the_first_sound_and_the_traces_breakdown_and_deletes_its_audio():
    from unittest.mock import patch as patch_
    from app.config import AUDIO_OUTPUT_DIR
    from app.services import tts as tts_service
    from evals.run_latency_check import time_speaking
    from tests.test_director import fake_azure
    before = set(AUDIO_OUTPUT_DIR.glob("*.mp3"))
    writer, judge = FakeStreamLLM(SCRIPT_TEXT, step=12, delay=0.02), TimedJudge("WRITE", delay=0.3)
    with patch_("app.services.llm.get_llm", return_value=writer), patch_("app.services.llm.get_judge_llm", return_value=judge), \
            patch_.object(tts_service, "_speak_ssml", side_effect=fake_azure([])):
        row = asyncio.run(time_speaking(CASE, "parallel"))
    assert row["first_word_s"] is not None and row["first_audio_s"] is not None and row["first_audio_s"] >= row["first_word_s"]
    assert row["done_s"] >= row["first_audio_s"] and row["error"] is None
    assert row["judge_ms"] >= 250 and row["writer_first_chunk_ms"] is not None and row["writer_first_chunk_ms"] < row["judge_ms"]
    assert row["piece1_chars"] and row["piece1_azure_ms"] is not None and row["piece1_ready_ms"] is not None
    assert set(AUDIO_OUTPUT_DIR.glob("*.mp3")) == before, "the runner left audio files behind"
    text = "\n".join(summary([row, dict(row, setup="today"), dict(row, setup="series")]))
    assert "sound" in text and "Azure render" in text
