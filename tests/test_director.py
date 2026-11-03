"""
test_director.py — The Speech Director
=========================================
Voice direction (sanitising VOXIS's annotations for Azure), the incremental
stream parser, the piece cutter, the parallel speech pipeline, and the
property the whole design exists for: speech starts while the model is
still writing. Everything runs offline against fakes.
"""

import asyncio
import os
import re
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.config import AUDIO_OUTPUT_DIR
from app.main import app
from app.services import tts as tts_service
from app.services.director import ScriptStream
from app.services.profiler import get_traces, init_traces
from app.services.tts import (
    PARAGRAPH_BREAK, PieceCutter, build_ssml, cleanup_audio_output, direct_tts_script, infer_speaking_style,
    split_script, strip_all_ssml, synthesize_pieces,
)
from tests.helpers import FakeAzureResult, FakeStreamLLM, remove_audio, sse_events

client = TestClient(app)


def run(coro):
    return asyncio.run(coro)


# ══════════════════════════════════════════════
# Voice direction
# ══════════════════════════════════════════════

class TestDirectTtsScript:
    def test_keeps_whitelisted_tags(self):
        frag, stats = direct_tts_script(
            '<prosody rate="-8%" pitch="-3%">Hello there, friends.</prosody> <break time="450ms"/> '
            '<emphasis level="strong">Go</emphasis>.'
        )
        assert '<prosody pitch="-3%" rate="-8%">' in frag or '<prosody rate="-8%" pitch="-3%">' in frag
        assert '<break time="450ms"/>' in frag
        assert '<emphasis level="strong">Go</emphasis>' in frag
        assert stats["tags_kept"] == 3 and stats["fallback"] is False

    def test_clamps_extreme_values(self):
        frag, stats = direct_tts_script('<prosody rate="+60%" pitch="x-low">Fast words here now.</prosody> <break time="1500ms"/>')
        assert 'rate="+30%"' in frag
        assert 'pitch="-15%"' in frag
        assert '<break time="500ms"/>' in frag
        assert stats["clamped"] >= 2

    def test_drops_unknown_tags_but_keeps_text(self):
        frag, stats = direct_tts_script('<speak><voice name="x">Hi <say-as interpret-as="date">2025</say-as> there</voice></speak>')
        assert "<" not in frag
        assert frag == "Hi 2025 there"
        assert stats["tags_dropped"] >= 3

    def test_director_notes_become_prosody(self):
        frag, stats = direct_tts_script("[voice drops] The room went quiet. [let this land] Nobody moved. Then she spoke.")
        assert '<prosody pitch="-4%" rate="-6%">The room went quiet.</prosody>' in frag
        assert 'Nobody moved. <break time="450ms"/>' in frag
        assert frag.endswith("Then she spoke.")
        assert "[" not in frag and stats["notes"] == 2 and stats["notes_dropped"] == 0

    def test_unknown_note_is_removed(self):
        frag, stats = direct_tts_script("[wiggle ears] Plain sentence here.")
        assert frag == "Plain sentence here."
        assert stats["notes_dropped"] == 1

    def test_repairs_unclosed_tag(self):
        frag, stats = direct_tts_script('<prosody rate="-8%">Unclosed sentence here')
        assert frag.endswith("</prosody>")
        assert stats["repaired"] == 1 and stats["fallback"] is False

    def test_drops_stray_closing_tag(self):
        frag, stats = direct_tts_script("Hello </prosody> world")
        assert "<" not in frag and "Hello" in frag and "world" in frag
        assert stats["repaired"] == 1

    def test_plain_text_is_escaped_not_altered(self):
        frag, stats = direct_tts_script("Tom & Jerry <3 forever")
        assert frag == "Tom &amp; Jerry &lt;3 forever"
        assert stats["fallback"] is False

    def test_no_op_prosody_is_dropped(self):
        frag, _ = direct_tts_script('<prosody rate="medium">Just normal text here.</prosody>')
        assert frag == "Just normal text here."

    def test_only_tags_falls_back_to_empty(self):
        frag, stats = direct_tts_script('<break time="300ms"/>')
        assert frag == "" and stats["fallback"] is True


class TestVoicePacing:
    """Sentence gaps stay even across pieces, and volume words keep their meaning."""

    def test_volume_words_become_gentle_relative_values(self):
        assert 'volume="-15%"' in direct_tts_script('<prosody volume="soft">A quiet line for the room.</prosody>')[0]
        assert 'volume="+15%"' in direct_tts_script('<prosody volume="loud">A strong line for the room.</prosody>')[0]
        assert 'volume="-25%"' in direct_tts_script('<prosody volume="-80%">Nothing dips further than this.</prosody>')[0]
        assert "volume" not in direct_tts_script('<prosody rate="-5%" volume="silent">Never a silent line.</prosody>')[0]

    def test_an_intimate_note_is_no_longer_a_jarring_dip(self):
        frag, _ = direct_tts_script("[intimate tone] Carry that spirit with you today.")
        assert 'volume="-15%"' in frag and 'volume="soft"' not in frag

    def test_a_routine_break_after_a_sentence_is_dropped(self):
        frag, _ = direct_tts_script('We ship on Monday. <break time="350ms"/> Three customers already pay us.')
        assert "<break" not in frag and "We ship on Monday. Three customers" in re.sub(r"\s+", " ", frag)
        frag, _ = direct_tts_script('<prosody rate="-5%">We ship on Monday.</prosody> <break time="300ms"/> Then we rest.')
        assert "<break" not in frag

    def test_a_deliberate_beat_and_a_clause_pause_stay(self):
        assert '<break time="500ms"/>' in direct_tts_script('We ship on Monday. <break time="500ms"/> Then we rest.')[0]
        assert '<break time="200ms"/>' in direct_tts_script('On Monday, <break time="200ms"/> we ship it.')[0]

    def test_a_paragraph_pause_at_the_start_of_a_piece_stays(self):
        assert '<break time="400ms"/>' in direct_tts_script('<break time="400ms"/> The next paragraph begins here.')[0]

    def test_every_piece_carries_even_silence(self):
        ssml = build_ssml("Hello there.", "en-US-DavisNeural", speaking_style="friendly")
        assert 'type="Sentenceboundary-exact" value="700ms"' in ssml and 'type="Tailing-exact" value="600ms"' in ssml
        assert 'type="Leading-exact" value="0ms"' in ssml
        assert ssml.index("mstts:silence") < ssml.index("mstts:express-as")      # must come first inside <voice>

    def test_the_setting_can_hand_pacing_back_to_azure(self):
        with patch.object(tts_service, "TTS_SENTENCE_GAP_MS", None):
            assert "mstts:silence" not in build_ssml("Hello there.", "en-US-DavisNeural")
            assert "<break" in direct_tts_script('We ship on Monday. <break time="350ms"/> Then we rest.')[0]


class TestStylePresets:
    """The Director's preset chips are plain sentences matched by keyword — a stray word picks the wrong voice."""

    EXPECTED = {            # preset label → the Azure speaking style it must resolve to
        "YC pitch": "narration-professional",
        "Keynote": "narration-professional",
        "Calm CEO": "narration-professional",
        "Warm teacher": "friendly",
        "Sales close": "empathetic",
        "Storyteller": "friendly",
        "Newscast": "newscast",
    }

    def _presets(self):
        import re
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent / "frontend" / "app.js").read_text(encoding="utf-8")
        block = re.search(r"const STYLE_PRESETS = \[(.*?)\n\];", src, re.S)
        assert block, "STYLE_PRESETS not found in app.js"
        return dict(re.findall(r"\['([^']+)',\s*'([^']+)'\]", block.group(1)))

    def test_every_preset_resolves_to_its_intended_style(self):
        presets = self._presets()
        assert set(presets) == set(self.EXPECTED)
        for label, text in presets.items():
            assert infer_speaking_style(text)[0] == self.EXPECTED[label], f"{label!r} picks the wrong voice style"

    def test_presets_fit_the_request_limit(self):
        assert all(len(t) <= 500 for t in self._presets().values())    # RewriteRequest.target_style max_length


# ══════════════════════════════════════════════
# ScriptStream — reading the model's output as it arrives
# ══════════════════════════════════════════════

def feed_all(stream: ScriptStream, text: str, step: int = 1):
    script, display = "", ""
    for i in range(0, len(text), step):
        s, d = stream.feed(text[i:i + step])
        script, display = script + s, display + d
    s, d = stream.finish()
    return script + s, display + d


class TestScriptStream:
    SCRIPT = ('[voice drops] <prosody rate="-8%" pitch="-3%">Welcome back, everyone.</prosody> <break time="350ms"/> '
              'We ship on <emphasis level="strong">Monday</emphasis>.\n\n[let this land] Thank you.')

    def test_display_strips_annotations_whatever_the_chunking(self):
        for step in (1, 3, 7, 1000):
            script, display = feed_all(ScriptStream(), self.SCRIPT, step)
            assert script == self.SCRIPT, step
            assert display == "Welcome back, everyone. We ship on Monday.\n\nThank you.", step

    def test_streamed_display_matches_the_final_strip(self):
        _, display = feed_all(ScriptStream(), self.SCRIPT, 5)
        assert " ".join(display.split()) == " ".join(strip_all_ssml(self.SCRIPT).split())

    def test_tag_split_across_chunks(self):
        stream = ScriptStream()
        parts = ['Hello <pros', 'ody rate="-8%">wor', 'ld</pro', 'sody> again friends.']
        display = "".join(stream.feed(p)[1] for p in parts) + stream.finish()[1]
        assert display == "Hello world again friends."

    @pytest.mark.parametrize("text", ["ERROR: Please give me some text to work with.", "error - nothing to rewrite here",
                                      "  Error: the style is unrecognisable."])
    def test_error_line_is_captured_and_nothing_is_spoken(self, text):
        stream = ScriptStream()
        script, display = feed_all(stream, text, 4)
        assert script == "" and display == ""
        assert stream.error and "ERROR" not in stream.error.upper()[:6]

    def test_code_fence_and_think_block_are_dropped(self):
        stream = ScriptStream()
        script, _ = feed_all(stream, "<think>let me plan this</think>\n```text\nHello there, friends. We ship.\n```", 3)
        assert script.strip() == "Hello there, friends. We ship."
        assert "`" not in script and "think" not in script

    def test_short_output_is_still_a_script(self):
        stream = ScriptStream()
        script, display = feed_all(stream, "Hi all.", 2)
        assert script == "Hi all." and display == "Hi all." and not stream.error

    def test_empty_output(self):
        stream = ScriptStream()
        assert feed_all(stream, "", 1) == ("", "") and not stream.script and not stream.error


# ══════════════════════════════════════════════
# PieceCutter — script → speakable pieces, incrementally
# ══════════════════════════════════════════════

class TestPieceCutter:
    SCRIPT = ('<prosody rate="-8%">We ship on Monday. That is the promise we made.</prosody> Then we grow. '
              'Next, the numbers: three customers pay us today. Our margin is forty percent and rising fast, and it keeps going '
              'higher every single month. We expect that to continue as the product matures and the team grows.\n\n'
              'Second paragraph starts here. It has two sentences.')

    def test_first_piece_is_short_later_ones_grow(self):
        pieces = split_script(self.SCRIPT)
        assert len(pieces) >= 3
        first = strip_all_ssml(pieces[0])
        assert 40 <= len(first) < 140, first
        assert all(len(strip_all_ssml(p)) >= 40 for p in pieces[:-1])

    def test_a_tag_never_straddles_two_pieces(self):
        for p in split_script(self.SCRIPT):
            assert p.count("<prosody") == p.count("</prosody>"), p
        assert split_script(self.SCRIPT)[0].startswith("<prosody") and "promise we made.</prosody>" in split_script(self.SCRIPT)[0]

    def test_paragraphs_end_a_piece_and_open_the_next_with_a_pause(self):
        pieces = split_script(self.SCRIPT)
        assert sum(p.startswith(PARAGRAPH_BREAK) for p in pieces) == 1
        assert pieces[-1].startswith(PARAGRAPH_BREAK) and "Second paragraph" in pieces[-1]

    def test_no_words_lost_or_added(self):
        joined = " ".join(strip_all_ssml(p) for p in split_script(self.SCRIPT))
        assert joined.split() == strip_all_ssml(self.SCRIPT).split()

    def test_short_opening_sentences_merge_until_long_enough(self):
        pieces = split_script("Hi. Welcome back everyone, it is good to see you all. Now to business.")
        assert pieces[0] == "Hi. Welcome back everyone, it is good to see you all."

    def test_streaming_character_by_character_gives_the_same_pieces(self):
        for script in (self.SCRIPT, "[voice drops] One. Two sentences here and more words follow. Three more words after that, at length.",
                       "Single sentence only."):
            cutter, out = PieceCutter(), []
            for ch in script:
                out += cutter.feed(ch)
            out += cutter.flush()
            assert out == split_script(script)

    def test_pieces_become_ready_before_the_text_is_finished(self):
        cutter = PieceCutter()
        ready = cutter.feed("Hello there, friends and colleagues of mine. We ship on Monday. And then we")
        assert ready == ["Hello there, friends and colleagues of mine."]       # released at once, text still arriving
        assert cutter.feed(" grow.") == [] and cutter.flush() == ["We ship on Monday. And then we grow."]

    def test_a_tag_split_across_chunks_is_never_spoken_as_text(self):
        """Models stream tags in fragments; losing the '<' would make the voice read the markup aloud."""
        cutter, out = PieceCutter(), []
        for chunk in ['[voice drops] <pros', 'ody rate="-8%" pi', 'tch="-3%">We ship on Monday, and three customers pay us.</pro',
                      'sody> Then <br', 'eak time="300ms"/> we grow, and we keep growing every month.']:
            out += cutter.feed(chunk)
        out += cutter.flush()
        text = " ".join(out)
        assert '<prosody rate="-8%" pitch="-3%">' in text and "</prosody>" in text and '<break time="300ms"/>' in text
        assert not any(strip_all_ssml(p).startswith(("prosody", "break")) or "pitch=" in strip_all_ssml(p) for p in out)

    def test_a_literal_less_than_sign_is_kept_and_does_not_stall_the_cutter(self):
        cutter = PieceCutter()
        out = cutter.feed("Revenue is 3 < 5 times the cost of the sale, which is great news for us all. Then more words follow here.")
        out += cutter.flush()
        assert "3 < 5 times" in " ".join(out)

    def test_empty(self):
        assert split_script("") == [] and split_script("   \n\n  ") == []


# ══════════════════════════════════════════════
# The parallel speech pipeline
# ══════════════════════════════════════════════

def fake_azure(calls, *, slow_first=0.0, reject_directed_once=False, fail_marker=None):
    state = {"rejected": False}

    def speak(ssml):
        calls.append(ssml)
        if slow_first and "ONE" in ssml:
            time.sleep(slow_first)          # a later piece will finish first
        if fail_marker and fail_marker in ssml:
            return FakeAzureResult(ok=False)
        if reject_directed_once and "<prosody" in ssml and not state["rejected"]:
            state["rejected"] = True
            return FakeAzureResult(ok=False)
        return FakeAzureResult(ok=True, data=b"MP3-" + str(len(calls)).encode())
    return speak


async def source_of(pieces, pause=0.0):
    for piece in pieces:
        if pause:
            await asyncio.sleep(pause)
        yield piece


def drive(pieces, speak, pause=0.0, **kwargs):
    async def go():
        init_traces()
        out = []
        async for kind, info in synthesize_pieces(source_of(pieces, pause), voice="en-US-DavisNeural",
                                                  target_style="calm", **kwargs):
            out.append((kind, info))
        return out, get_traces()
    with patch.object(tts_service, "_speak_ssml", side_effect=speak):
        return run(go())


class TestPipeline:
    def test_a_piece_with_only_a_pause_is_skipped_not_reported_as_failed(self):
        base, calls = f"t_pause_{int(time.time() * 1000)}", []
        try:
            events, _ = drive(["First sentence is here now.", '<break time="400ms"/> <break time="500ms"/>', "Second sentence is here now."],
                              fake_azure(calls), base_name=base)
            segments = [info for kind, info in events if kind == "segment"]
            assert [s["index"] for s in segments] == [0, 1] and all(s["ok"] for s in segments) and len(calls) == 2
            assert events[-1][1]["failed_segments"] == 0
        finally:
            remove_audio(f"{base}*.mp3")

    def test_pieces_arrive_in_order_even_when_a_later_one_finishes_first(self):
        base, calls = f"t_order_{int(time.time() * 1000)}", []
        try:
            events, _ = drive(["ONE first piece here.", "TWO second.", "THREE third."], fake_azure(calls, slow_first=0.25), base_name=base)
            segs = [i for k, i in events if k == "segment"]
            assert [s["index"] for s in segs] == [0, 1, 2] and all(s["ok"] for s in segs)
            done = events[-1][1]
            assert done["success"] and done["segments"] == 3 and done["duration"] == pytest.approx(3.6)
            assert (AUDIO_OUTPUT_DIR / done["filename"]).read_bytes() == b"".join((AUDIO_OUTPUT_DIR / s["filename"]).read_bytes() for s in segs)
        finally:
            remove_audio(f"{base}*.mp3")

    def test_the_first_piece_is_delivered_while_the_source_is_still_producing(self):
        base, calls, state = f"t_early_{int(time.time() * 1000)}", [], {"source_finished": False, "first_seen_after_finish": None}

        async def slow_source():
            for piece in ["Opening sentence goes here.", "Middle one.", "Final one."]:
                await asyncio.sleep(0.15)
                yield piece
            state["source_finished"] = True

        async def go():
            init_traces()
            async for kind, info in synthesize_pieces(slow_source(), voice="en-US-DavisNeural", base_name=base):
                if kind == "segment" and state["first_seen_after_finish"] is None:
                    state["first_seen_after_finish"] = state["source_finished"]

        try:
            with patch.object(tts_service, "_speak_ssml", side_effect=fake_azure(calls)):
                run(go())
            assert state["first_seen_after_finish"] is False, "audio must start before the script is complete"
        finally:
            remove_audio(f"{base}*.mp3")

    def test_a_failed_piece_is_skipped_the_rest_still_play(self):
        base, calls = f"t_fail_{int(time.time() * 1000)}", []
        try:
            events, _ = drive(["Good one here.", "BAD piece here.", "Good three."], fake_azure(calls, fail_marker="BAD"), base_name=base)
            segs = [i for k, i in events if k == "segment"]
            assert [s["ok"] for s in segs] == [True, False, True]
            done = events[-1][1]
            assert done["success"] and done["failed_segments"] == 1
        finally:
            remove_audio(f"{base}*.mp3")

    def test_everything_failing_reports_the_error(self):
        events, _ = drive(["Anything at all."], fake_azure([], fail_marker="Anything"))
        done = events[-1][1]
        assert done["success"] is False and done["message"]

    def test_no_pieces_is_a_clean_failure(self):
        events, _ = drive([], fake_azure([]))
        assert events == [("done", {"success": False, "segments": 0, "message": "Text is empty after processing."})]

    def test_azure_rejecting_the_direction_retries_plain(self):
        base, calls = f"t_plain_{int(time.time() * 1000)}", []
        try:
            events, _ = drive(['[voice drops] <prosody rate="-8%">We ship on Monday.</prosody>'], fake_azure(calls, reject_directed_once=True), base_name=base)
            done = events[-1][1]
            assert done["success"] and done["directed"] is False and len(calls) == 2
            assert "<prosody" in calls[0] and "<prosody" not in calls[1]
        finally:
            remove_audio(f"{base}*.mp3")

    def test_direction_reaches_azure_and_notes_do_not(self):
        base, calls = f"t_dir_{int(time.time() * 1000)}", []
        try:
            events, _ = drive(['[voice drops] <prosody rate="-8%">We ship on Monday.</prosody> <break time="300ms"/>'], fake_azure(calls), base_name=base)
            assert events[-1][1]["directed"] is True
            assert "<prosody" in calls[0] and "mstts:express-as" in calls[0] and "[voice drops]" not in calls[0]
        finally:
            remove_audio(f"{base}*.mp3")

    def test_plain_mode_sends_no_direction_and_no_style(self):
        base, calls = f"t_flat_{int(time.time() * 1000)}", []
        try:
            events, _ = drive(['<prosody rate="-8%">We ship on Monday.</prosody>'], fake_azure(calls), base_name=base,
                              annotate=False, use_style=False)
            assert events[-1][1]["directed"] is False
            assert "<prosody" not in calls[0] and "express-as" not in calls[0] and "We ship on Monday." in calls[0]
        finally:
            remove_audio(f"{base}*.mp3")

    def test_each_piece_is_traced_under_the_pipeline_span(self):
        base = f"t_span_{int(time.time() * 1000)}"
        try:
            _, spans = drive(["First piece here.", "Second piece."], fake_azure([]), base_name=base)
            pieces = [s for s in spans if s["name"].startswith("TTS piece")]
            pipeline = next(s for s in spans if s["name"] == "Speech Synthesis Pipeline")
            assert len(pieces) == 2 and all(p["parent"] == pipeline["name"] and p["depth"] == 1 for p in pieces)
            assert all(p["start_ms"] is not None for p in pieces) and pipeline["depth"] == 0
        finally:
            remove_audio(f"{base}*.mp3")


# ══════════════════════════════════════════════
# The stream: model → script → voice
# ══════════════════════════════════════════════

SCRIPT_TEXT = ('[voice drops] <prosody rate="-8%" pitch="-3%">Welcome back, everyone, it is good to see you all.</prosody> '
               '<break time="300ms"/> We ship on <emphasis level="strong">Monday</emphasis>, and three customers already pay us. '
               'The margin is forty percent and it keeps rising every single month.\n\n'
               '[let this land] Thank you all for being here today, and for everything you have done.')


def post_rewrite(path, llm, speak_fn=None, **extra):
    body = {"user_text": "welcome back we ship monday", "target_style": "calm CEO", **extra}
    with patch("app.services.llm.get_llm", return_value=llm):
        if speak_fn is None:
            return sse_events(client.post(path, json=body).text)
        with patch.object(tts_service, "_speak_ssml", side_effect=speak_fn):
            return sse_events(client.post(path, json=body).text)


def cleanup_stream_audio(events):
    names = [e["url"].split("/")[-1] for e in events if e["type"] == "audio_segment" and e.get("ok")]
    done = next((e for e in events if e["type"] == "audio_done"), None)
    remove_audio(*(names + ([done["audio"]["filename"]] if done else [])))


class TestRewriteStream:
    def test_speech_starts_before_the_script_is_finished(self):
        """The reason this pipeline exists: the first sentence is spoken while the model is still writing."""
        calls = []
        events = post_rewrite("/api/rewrite-and-speak/stream", FakeStreamLLM(SCRIPT_TEXT, step=12, delay=0.02),
                              fake_azure(calls), voice="davis")
        try:
            kinds = [e["type"] for e in events]
            assert kinds.index("audio_segment") < kinds.index("script_done"), kinds
            assert kinds.index("token") < kinds.index("audio_segment")
            assert kinds.count("audio_segment") >= 2 and "audio_done" in kinds and kinds[-1] == "done"
        finally:
            cleanup_stream_audio(events)

    def test_reader_sees_clean_text_the_voice_gets_the_annotated_script(self):
        calls = []
        events = post_rewrite("/api/rewrite-and-speak/stream", FakeStreamLLM(SCRIPT_TEXT, step=9), fake_azure(calls))
        try:
            shown = "".join(e["text"] for e in events if e["type"] == "token")
            assert "<" not in shown and "[" not in shown and shown.startswith("Welcome back, everyone")
            script = next(e for e in events if e["type"] == "script_done")
            assert script["tts_script"] == SCRIPT_TEXT.strip() and "<prosody" in script["tts_script"]
            assert script["display_script"] == strip_all_ssml(SCRIPT_TEXT)
            assert "<" not in script["display_script"] and "[" not in script["display_script"]
            assert any("<prosody" in c for c in calls) and any("<break" in c for c in calls)
            assert all("[voice drops]" not in c and "[let this land]" not in c for c in calls)
        finally:
            cleanup_stream_audio(events)

    def test_audio_done_reports_the_pipeline(self):
        events = post_rewrite("/api/rewrite-and-speak/stream", FakeStreamLLM(SCRIPT_TEXT, step=15), fake_azure([]))
        try:
            audio = next(e for e in events if e["type"] == "audio_done")["audio"]
            segments = [e for e in events if e["type"] == "audio_segment"]
            assert audio["directed"] is True and audio["segments"] == len(segments) and audio["first_audio_ms"] is not None
            assert all(s["ok"] is True and s["url"].startswith("/audio/") and "total" not in s for s in segments)
            done = next(e for e in events if e["type"] == "done")
            assert done["token_source"] == "api" and done["prompt_tokens"] == 900 and done["script_ms"] > 0
            names = [t["name"] for t in done["detailed_latencies"]]
            assert "Speech Synthesis Pipeline" in names and any(n.startswith("TTS piece") for n in names)
            assert "Time to First Token (TTFT)" in names
        finally:
            cleanup_stream_audio(events)

    def test_rewrite_only_never_touches_azure(self):
        calls = []
        events = post_rewrite("/api/rewrite/stream", FakeStreamLLM(SCRIPT_TEXT, step=15), fake_azure(calls))
        kinds = [e["type"] for e in events]
        assert calls == [] and "audio_segment" not in kinds and "audio_done" not in kinds
        assert "script_done" in kinds and kinds[-1] == "done"

    def test_the_model_refusing_is_an_error_event_and_nothing_is_spoken(self):
        calls = []
        events = post_rewrite("/api/rewrite-and-speak/stream", FakeStreamLLM("ERROR: Please provide some text or a clear instruction."),
                              fake_azure(calls))
        assert events[-1]["type"] == "error" and "provide some text" in events[-1]["message"]
        assert calls == [] and not any(e["type"] in ("token", "audio_segment", "script_done") for e in events)

    def test_a_refusal_is_retried_once_and_the_second_answer_is_used(self):
        # the model refused "groundnut is the best nut" on one click and wrote the script on the next
        llm = FakeStreamLLM(["ERROR: groundnut is a legume, not a nut.", SCRIPT_TEXT], step=15)
        events = post_rewrite("/api/rewrite/stream", llm)
        kinds = [e["type"] for e in events]
        assert llm.calls == 2 and "script_done" in kinds and "error" not in kinds and kinds[-1] == "done"
        assert "retry" in kinds and kinds.index("retry") < kinds.index("token")
        second = llm.prompts[1]
        assert isinstance(second, list) and "Reconsider" in second[-1].content      # the first attempt got the plain prompt

    def test_a_refusal_that_repeats_is_shown_after_one_retry(self):
        llm = FakeStreamLLM("ERROR: Please provide some text or a clear instruction.")
        events = post_rewrite("/api/rewrite/stream", llm)
        assert llm.calls == 2 and events[-1]["type"] == "error" and "provide some text" in events[-1]["message"]

    def test_a_script_is_never_asked_for_twice(self):
        llm = FakeStreamLLM(SCRIPT_TEXT, step=15)
        post_rewrite("/api/rewrite/stream", llm)
        assert llm.calls == 1

    def test_a_voice_failure_does_not_lose_the_script(self):
        def broken(ssml):
            raise RuntimeError("azure down")
        events = post_rewrite("/api/rewrite-and-speak/stream", FakeStreamLLM(SCRIPT_TEXT, step=15), broken)
        kinds = [e["type"] for e in events]
        assert "script_done" in kinds and "audio_error" in kinds and "audio_done" not in kinds and kinds[-1] == "done"

    def test_an_empty_reply_is_reported(self):
        events = post_rewrite("/api/rewrite/stream", FakeStreamLLM(""))
        assert events[-1]["type"] == "error" and "empty" in events[-1]["message"].lower()

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

        llm = RateLimitedOnce("Hello there, friends. We ship on Monday.", step=10)
        events = post_rewrite("/api/rewrite/stream", llm)
        kinds = [e["type"] for e in events]
        assert kinds[0] == "rate_limited" and events[0]["wait_s"] == 1.0       # clamped to the 1 s floor
        assert "script_done" in kinds and llm.calls == 2

    def test_the_daily_token_limit_is_reported_at_once_not_waited_out(self):
        import groq as groq_sdk
        import httpx

        class OutOfDailyTokens(FakeStreamLLM):
            async def astream(self, prompt_value):
                self.calls += 1
                resp = httpx.Response(429, headers={"retry-after": "426"}, request=httpx.Request("POST", "https://api.groq.com/x"))
                raise groq_sdk.RateLimitError(
                    "Rate limit reached for model `m` on tokens per day (TPD): Limit 200000, Used 199047, Requested 1937. "
                    "Please try again in 7m5.08s.", response=resp, body=None)
                yield  # pragma: no cover

        llm = OutOfDailyTokens("unused")
        events = post_rewrite("/api/rewrite/stream", llm)
        kinds = [e["type"] for e in events]
        assert llm.calls == 1 and "rate_limited" not in kinds and "retry" not in kinds
        assert events[-1]["type"] == "error" and "daily token allowance" in events[-1]["message"] and "7 min" in events[-1]["message"]

    def test_a_stream_that_dies_before_any_output_is_retried_once(self):
        class DiesOnce(FakeStreamLLM):
            async def astream(self, prompt_value):
                if self.calls == 0:
                    self.calls += 1
                    raise ConnectionError("reset")
                    yield  # pragma: no cover  (makes this an async generator)
                async for chunk in super().astream(prompt_value):
                    yield chunk

        llm = DiesOnce("Hello there, friends. We ship on Monday.", step=10)
        events = post_rewrite("/api/rewrite/stream", llm)
        kinds = [e["type"] for e in events]
        assert "retry" in kinds and "script_done" in kinds and llm.calls == 2


# ══════════════════════════════════════════════
# Routes: /api/synthesize and /api/warm
# ══════════════════════════════════════════════

class TestSynthesizeRoutes:
    def test_synthesize_speaks_the_whole_script_through_the_pipeline(self):
        calls = []
        with patch.object(tts_service, "_speak_ssml", side_effect=fake_azure(calls)):
            res = client.post("/api/synthesize", json={"tts_script": SCRIPT_TEXT, "voice": "davis", "target_style": "calm"})
        try:
            assert res.status_code == 200 and res.headers["content-type"] == "audio/mpeg"
            assert res.headers["X-Audio-Directed"] == "1" and float(res.headers["X-Audio-Duration"]) > 0
            assert len(calls) >= 2 and res.content.startswith(b"MP3-")
        finally:
            remove_audio(res.headers.get("X-Audio-Filename", "none").replace(".mp3", "") + "*.mp3")

    def test_plain_flag_disables_direction_and_style(self):
        calls = []
        with patch.object(tts_service, "_speak_ssml", side_effect=fake_azure(calls)):
            res = client.post("/api/synthesize", json={"tts_script": "Hello there.", "voice": "davis", "plain": True,
                                                       "target_style": "calm CEO"})
        try:
            assert res.status_code == 200 and res.headers["X-Audio-Directed"] == "0"
            assert "<prosody" not in calls[0] and "express-as" not in calls[0]
        finally:
            remove_audio(res.headers.get("X-Audio-Filename", "none").replace(".mp3", "") + "*.mp3")

    def test_azure_failure_is_a_502(self):
        with patch.object(tts_service, "_speak_ssml", side_effect=fake_azure([], fail_marker="Hello")):
            res = client.post("/api/synthesize", json={"tts_script": "Hello there."})
        assert res.status_code == 502

    def test_warm_opens_the_pool(self):
        with patch("app.routes.synthesize._last_warm", 0.0),                 patch("app.routes.synthesize._synth_pool.warm", return_value=3) as warm:
            res = client.post("/api/warm")
        assert res.status_code == 200 and res.json() == {"warmed": 3} and warm.called

    def test_warm_runs_at_most_once_per_cooldown_for_everyone(self):
        """Each warm-up is billed by Azure, so repeated calls inside the window do nothing."""
        with patch("app.routes.synthesize._last_warm", 0.0),                 patch("app.routes.synthesize._synth_pool.warm", return_value=3) as warm:
            first = client.post("/api/warm").json()
            again = [client.post("/api/warm").json() for _ in range(5)]
        assert first == {"warmed": 3} and warm.call_count == 1
        assert all(r["warmed"] == 0 for r in again)


class TestCleanupAudio:
    def test_removes_stale_mp3_keeps_fresh(self):
        old = AUDIO_OUTPUT_DIR / "test_cleanup_old.mp3"
        fresh = AUDIO_OUTPUT_DIR / "test_cleanup_fresh.mp3"
        old.write_bytes(b"x")
        fresh.write_bytes(b"x")
        stale = time.time() - 48 * 3600
        os.utime(old, (stale, stale))
        try:
            removed = cleanup_audio_output(max_age_hours=24)
            assert removed >= 1
            assert not old.exists() and fresh.exists()
        finally:
            remove_audio("test_cleanup_*.mp3")
