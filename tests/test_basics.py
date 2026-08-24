"""
test_basics.py — Basic sanity tests for Cadence Coach
======================================================
A small, beginner-friendly test suite that verifies the app
doesn't blow up and core utilities work as expected.

Run with:  pytest tests/test_basics.py -v
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.config import VOICES, DEFAULT_VOICE
from app.services.stt import _rate_wpm, _rate_fillers
from app.services.llm import _estimate_tokens
from app.services.profiler import sizeof_fmt
from app.models.schemas import RewriteRequest, CoachRequest

client = TestClient(app)


# ── API health check ──

def test_health_endpoint():
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "online"


def test_voices_endpoint():
    resp = client.get("/api/voices")
    assert resp.status_code == 200
    assert "davis" in resp.json()["available_voices"]


# ── Config sanity ──

def test_default_voice_exists():
    assert DEFAULT_VOICE in VOICES.values()


def test_voices_not_empty():
    assert len(VOICES) > 0


# ── WPM rating util ──

def test_optimal_wpm():
    assert _rate_wpm(150) == "optimal"


def test_too_fast_wpm():
    assert _rate_wpm(200) == "too_fast"


def test_zero_wpm():
    assert _rate_wpm(0) == "unknown"


# ── Filler rating util ──

def test_excellent_filler_rate():
    assert _rate_fillers(1.0) == "excellent"


def test_excessive_filler_rate():
    assert _rate_fillers(15.0) == "excessive"


# ── Token estimation ──

def test_estimate_tokens_normal():
    assert _estimate_tokens("Hello world!") == 3  # 12 chars // 4


def test_estimate_tokens_empty():
    assert _estimate_tokens("") == 1  # minimum is 1


# ── sizeof_fmt ──

def test_sizeof_bytes():
    assert sizeof_fmt(500) == "500.0 B"


def test_sizeof_kilobytes():
    assert "KB" in sizeof_fmt(2048)


# ── Pydantic schemas ──

def test_rewrite_request_valid():
    req = RewriteRequest(user_text="Hello", target_style="TED Talk")
    assert req.user_text == "Hello"


def test_coach_request_has_default_context():
    req = CoachRequest(user_text="My speech")
    assert req.context is not None
