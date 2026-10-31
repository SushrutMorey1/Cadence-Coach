"""
test_unit.py — Unit tests for Cadence Coach
==============================================
Core helpers: speaking-style inference, SSML building, voice aliases, speech ratings and request schemas.
Run with:  pytest tests/test_unit.py -v
"""

# pyrefly: ignore [missing-import]
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.tts import (
    infer_speaking_style,
    strip_all_ssml,
    get_valid_style_for_voice,
    build_ssml,
    resolve_voice,
)
from app.services.stt import _rate_wpm, _rate_fillers
from app.models.schemas import (
    RewriteRequest,
    CoachRequest,
    SynthesizeRequest,
)

client = TestClient(app)


# ══════════════════════════════════════════════
# TTS Utility Tests
# ══════════════════════════════════════════════

class TestInferSpeakingStyle:
    """Test the keyword-to-style mapping logic."""

    def test_ted_talk_maps_to_friendly(self):
        style, degree = infer_speaking_style("TED Talk speaker")
        assert style == "friendly"
        assert degree == 1.0

    def test_newscast_maps_correctly(self):
        style, _ = infer_speaking_style("newscast anchor")
        assert style == "newscast"

    def test_whisper_maps_correctly(self):
        style, degree = infer_speaking_style("soft whisper")
        assert style == "whispering"
        assert degree == 1.0

    def test_unknown_defaults_to_friendly(self):
        style, degree = infer_speaking_style("xyzzy random gibberish")
        assert style == "friendly"
        assert degree == 0.8

    def test_none_input(self):
        style, degree = infer_speaking_style(None)
        assert style == "friendly"
        assert degree == 0.8

    def test_empty_string(self):
        style, degree = infer_speaking_style("")
        assert style == "friendly"
        assert degree == 0.8

    def test_obama_maps_to_hopeful(self):
        style, _ = infer_speaking_style("Like Obama giving a commencement")
        # "commencement" matches first → hopeful
        assert style == "hopeful"


class TestStripAllSSML:
    """Test SSML stripping logic."""

    def test_strips_prosody_tags(self):
        text = '<prosody pitch="-5%">Hello world</prosody>'
        assert strip_all_ssml(text) == "Hello world"

    def test_strips_bracketed_notes(self):
        text = "[voice drops] Hello there"
        assert strip_all_ssml(text) == "Hello there"

    def test_strips_mixed_content(self):
        text = '[intimate tone] <prosody rate="-8%">This is deep.</prosody> <break time="300ms"/> Normal text.'
        result = strip_all_ssml(text)
        assert "<" not in result
        assert "[" not in result
        assert "This is deep" in result

    def test_empty_input(self):
        assert strip_all_ssml("") == ""


class TestValidStyleForVoice:
    """Test voice-style compatibility checking."""

    def test_supported_style_returns_itself(self):
        result = get_valid_style_for_voice("en-US-DavisNeural", "cheerful")
        assert result == "cheerful"

    def test_unsupported_style_falls_back(self):
        result = get_valid_style_for_voice("en-US-DavisNeural", "narration-professional")
        # narration-professional not supported by Davis, should fallback
        assert result in ["friendly", "chat", "cheerful", "hopeful"]

    def test_unknown_voice_returns_none(self):
        result = get_valid_style_for_voice("en-US-UnknownVoice", "cheerful")
        assert result is None


class TestBuildSSML:
    """Test SSML document construction."""

    def test_basic_ssml_structure(self):
        ssml = build_ssml("Hello world", "en-US-DavisNeural")
        assert '<speak version="1.0"' in ssml
        assert '<voice name="en-US-DavisNeural">' in ssml
        assert "Hello world" in ssml

    def test_with_speaking_style(self):
        ssml = build_ssml("Hello", "en-US-DavisNeural", speaking_style="cheerful", style_degree=0.8)
        assert 'mstts:express-as style="cheerful"' in ssml
        assert 'styledegree="0.8"' in ssml

    def test_ampersand_escaping(self):
        ssml = build_ssml("Tom & Jerry", "en-US-DavisNeural")
        assert "&amp;" in ssml


class TestResolveVoice:
    """Test voice alias resolution."""

    def test_alias_resolves(self):
        assert resolve_voice("davis") == "en-US-DavisNeural"
        assert resolve_voice("aria") == "en-US-AriaNeural"

    def test_full_name_passthrough(self):
        assert resolve_voice("en-US-CustomVoice") == "en-US-CustomVoice"

    def test_none_returns_default(self):
        from app.config import DEFAULT_VOICE
        assert resolve_voice(None) == DEFAULT_VOICE


# ══════════════════════════════════════════════
# STT Utility Tests
# ══════════════════════════════════════════════

class TestWPMRating:
    """Test WPM categorization."""

    def test_zero_wpm(self):
        assert _rate_wpm(0) == "unknown"

    def test_too_slow(self):
        assert _rate_wpm(90) == "too_slow"

    def test_slow(self):
        assert _rate_wpm(120) == "slow"

    def test_optimal(self):
        assert _rate_wpm(150) == "optimal"

    def test_fast(self):
        assert _rate_wpm(170) == "fast"

    def test_too_fast(self):
        assert _rate_wpm(200) == "too_fast"


class TestFillerRating:
    """Test filler word rate categorization."""

    def test_excellent(self):
        assert _rate_fillers(1.0) == "excellent"

    def test_good(self):
        assert _rate_fillers(3.5) == "good"

    def test_needs_work(self):
        assert _rate_fillers(7.0) == "needs_work"

    def test_excessive(self):
        assert _rate_fillers(15.0) == "excessive"


# ══════════════════════════════════════════════
# Pydantic Schema Tests
# ══════════════════════════════════════════════

class TestSchemas:
    """Test Pydantic model validation."""

    def test_rewrite_request_valid(self):
        req = RewriteRequest(user_text="Hello world", target_style="TED Talk")
        assert req.user_text == "Hello world"
        assert req.target_style == "TED Talk"

    def test_rewrite_request_empty_text_fails(self):
        with pytest.raises(Exception):
            RewriteRequest(user_text="", target_style="TED Talk")

    def test_coach_request_with_context(self):
        req = CoachRequest(user_text="My speech", context="Board meeting")
        assert req.context == "Board meeting"

    def test_coach_request_default_context(self):
        req = CoachRequest(user_text="My speech")
        assert "infer" in req.context.lower()

    def test_synthesize_request_defaults(self):
        req = SynthesizeRequest(tts_script="Hello")
        assert req.voice is None
        assert req.target_style is None


# ══════════════════════════════════════════════
# API Endpoint Tests (with mocked LLM)
# ══════════════════════════════════════════════

class TestVoicesEndpoint:
    """Test the voices listing endpoint."""

    def test_voices_returns_list(self):
        resp = client.get("/api/voices")
        assert resp.status_code == 200
        data = resp.json()
        assert "available_voices" in data
        assert "davis" in data["available_voices"]
        assert "default_voice" in data
