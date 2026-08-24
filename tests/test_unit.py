"""
test_unit.py — Unit tests for Cadence Coach
==============================================
Tests core logic with mocked LLM and TTS services.
Run with:  pytest tests/test_unit.py -v
"""

import json
# pyrefly: ignore [missing-import]
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient

from app.main import app
from app.services.tts import (
    infer_speaking_style,
    strip_all_ssml,
    get_valid_style_for_voice,
    add_paragraph_pauses,
    build_ssml,
    resolve_voice,
)
from app.services.stt import _rate_wpm, _rate_fillers
from app.models.schemas import (
    RewriteRequest,
    RewriteResponse,
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


class TestAddParagraphPauses:
    """Test paragraph pause insertion."""

    def test_single_paragraph_unchanged(self):
        text = "Just one paragraph."
        assert add_paragraph_pauses(text) == text

    def test_multiple_paragraphs_get_breaks(self):
        text = "Paragraph one.\n\nParagraph two."
        result = add_paragraph_pauses(text)
        assert '<break time="350ms"/>' in result


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

    def test_rewrite_response_valid(self):
        resp = RewriteResponse(display_script="Clean text", tts_script="Annotated text")
        assert resp.display_script == "Clean text"

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

class TestHealthEndpoint:
    """Test the health check endpoint."""

    def test_health_returns_ok(self):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "online"
        assert data["version"] == "2.0.0"


class TestVoicesEndpoint:
    """Test the voices listing endpoint."""

    def test_voices_returns_list(self):
        resp = client.get("/api/voices")
        assert resp.status_code == 200
        data = resp.json()
        assert "available_voices" in data
        assert "davis" in data["available_voices"]
        assert "default_voice" in data


class TestMetricsEndpoint:
    """Test metrics endpoints."""

    def test_metrics_returns_structure(self):
        resp = client.get("/api/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert "llm" in data
        assert "tts" in data
        assert "recent_calls" in data

    def test_llm_metrics(self):
        resp = client.get("/api/metrics/llm")
        assert resp.status_code == 200

    def test_tts_metrics(self):
        resp = client.get("/api/metrics/tts")
        assert resp.status_code == 200


class TestRewriteEndpoint:
    """Test the rewrite endpoint with mocked LLM."""

    @patch("app.services.llm.invoke_rewrite")
    def test_rewrite_success(self, mock_invoke):
        mock_invoke.return_value = {
            "raw_output": json.dumps({
                "display_script": "A polished script.",
                "tts_script": '<prosody rate="-5%">A polished script.</prosody>',
            }),
            "latency_ms": 150.0,
        }

        resp = client.post("/api/rewrite", json={
            "user_text": "Hello world, this is a test.",
            "target_style": "TED Talk speaker",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "display_script" in data
        assert "tts_script" in data

    def test_rewrite_missing_fields(self):
        resp = client.post("/api/rewrite", json={"user_text": "Hello"})
        assert resp.status_code == 422  # Pydantic validation error


class TestCoachEndpoint:
    """Test the coach endpoint with mocked LLM."""

    @patch("app.services.llm.invoke_coach")
    def test_coach_success(self, mock_invoke):
        mock_invoke.return_value = {
            "raw_output": json.dumps({
                "overall_score": 7.5,
                "summary": "Good speech with room to improve.",
                "vocabulary": {
                    "score": 7,
                    "strengths": ["Strong verbs"],
                    "improvements": ["Reduce filler words"],
                    "weak_words": [{"word": "very", "suggestion": "extremely", "reason": "more impactful"}],
                    "power_words_used": ["ignite"],
                },
                "tone_and_emotion": {
                    "score": 8,
                    "detected_tone": "inspirational",
                    "emotional_arc": "Rising",
                    "feedback": ["Great energy"],
                },
                "voice_modulation": {
                    "score": 6,
                    "suggestions": [
                        {"text_segment": "Hello world", "instruction": "Slow down", "type": "pace"},
                    ],
                },
                "pacing_and_rhythm": {
                    "score": 7,
                    "avg_sentence_length": "medium",
                    "variation_quality": "Good variety",
                    "feedback": ["Nice rhythm"],
                },
                "emphasis_and_delivery": {
                    "score": 7,
                    "key_moments": [
                        {"text_segment": "the future", "technique": "Pause before", "reason": "builds anticipation"},
                    ],
                },
                "clarity_and_structure": {
                    "score": 8,
                    "opening_strength": "Strong hook",
                    "closing_strength": "Memorable finale",
                    "transitions": "Smooth",
                    "feedback": ["Well organized"],
                },
                "top_3_actions": ["Add pauses", "Vary pitch", "Stronger close"],
            }),
            "latency_ms": 200.0,
        }

        resp = client.post("/api/coach", json={
            "user_text": "Today we launch a product that will change the world. We have worked hard.",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["overall_score"] == 7.5
        assert len(data["top_3_actions"]) == 3

    def test_coach_missing_text(self):
        resp = client.post("/api/coach", json={})
        assert resp.status_code == 422
