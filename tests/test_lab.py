"""
test_lab.py — The Latency Lab
================================
The profiler's timeline, per-user metric scoping, percentiles, export, and the
static-file serving that decides how fast the page itself loads.
"""

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from app.config import AUDIO_OUTPUT_DIR
from app.main import app
from app.services import metrics as metrics_service
from app.services.profiler import add_span, elapsed_ms, get_traces, init_traces, trace
from tests.helpers import TEST_USER, remove_audio

client = TestClient(app)


def run(coro):
    return asyncio.run(coro)


class TestProfilerTimeline:
    def test_spans_carry_start_offsets(self):
        init_traces()
        with trace("parent", category="compute"):
            time.sleep(0.002)
            with trace("child", category="io"):
                time.sleep(0.001)
        spans = get_traces()
        parent = next(s for s in spans if s["name"] == "parent")
        child = next(s for s in spans if s["name"] == "child")
        assert parent["start_ms"] is not None and child["start_ms"] >= parent["start_ms"]
        assert child["depth"] == 1 and child["parent"] == "parent"
        assert child["start_ms"] + child["latency_ms"] <= parent["start_ms"] + parent["latency_ms"] + 0.5
        assert all(s["start_ms"] is None for s in spans if s["category"] == "overhead")

    def test_add_span_defaults_to_the_current_parent(self):
        init_traces()
        with trace("pipeline", category="io"):
            t0 = time.perf_counter()
            time.sleep(0.001)
            add_span("worker piece", t0, time.perf_counter(), category="network", chars=42)
        piece = next(s for s in get_traces() if s["name"] == "worker piece")
        assert piece["parent"] == "pipeline" and piece["depth"] == 1
        assert piece["latency_ms"] > 0 and piece["start_ms"] >= 0 and piece["meta"]["chars"] == 42

    def test_add_span_can_name_its_own_parent_for_concurrent_work(self):
        init_traces()
        with trace("something else running"):
            add_span("piece", time.perf_counter(), time.perf_counter(), parent="Speech Synthesis Pipeline", depth=1)
            add_span("pipeline", time.perf_counter(), time.perf_counter(), parent=None, depth=0)
        spans = {s["name"]: s for s in get_traces()}
        assert spans["piece"]["parent"] == "Speech Synthesis Pipeline" and spans["piece"]["depth"] == 1
        assert spans["pipeline"]["parent"] is None and spans["pipeline"]["depth"] == 0

    def test_elapsed_ms_needs_an_initialised_trace(self):
        init_traces()
        time.sleep(0.002)
        assert elapsed_ms() >= 2


class TestMetricsScoping:
    def test_summary_is_per_user_with_percentiles(self):
        a, b = "user:lab-a@example.com", "user:lab-b@example.com"
        for ms in (100, 200, 300):
            run(metrics_service.log_llm_call("/api/rewrite/stream", "m", 10, 20, ms, user_key=a))
        run(metrics_service.log_llm_call("/api/rewrite/stream", "m", 10, 20, 999, user_key=b))

        summary_a = run(metrics_service.get_llm_summary(a))
        assert summary_a["total_calls"] == 3
        assert summary_a["p50_latency_ms"] == 200 and summary_a["p95_latency_ms"] == 300
        assert run(metrics_service.get_llm_summary(b))["total_calls"] == 1

        breakdown = run(metrics_service.get_endpoint_breakdown(a))
        assert breakdown[0]["endpoint"] == "/api/rewrite/stream" and breakdown[0]["p95_latency_ms"] == 300

    def test_hours_window(self):
        user = "user:lab-window@example.com"
        run(metrics_service.log_llm_call("/api/coach/stream", "m", 1, 1, 50, user_key=user))
        assert run(metrics_service.get_llm_summary(user, hours=1))["total_calls"] == 1
        assert run(metrics_service.get_llm_summary(user, hours=0.0000001))["total_calls"] == 0

    def test_percentile_helper(self):
        assert metrics_service.percentile([], 50) is None
        assert metrics_service.percentile([5], 95) == 5
        assert metrics_service.percentile([10, 20, 30, 40, 50], 50) == 30

    def test_cycles_round_trip_with_their_spans(self):
        user = "user:lab-cycle@example.com"
        run(metrics_service.log_cycle("Rewrite & Speak", 1234.5, [{"name": "x", "latency_ms": 5, "start_ms": 1.0}], user_key=user))
        cycles = run(metrics_service.get_recent_cycles(user_key=user))
        assert cycles[0]["cycle_type"] == "Rewrite & Speak" and cycles[0]["tasks"][0]["start_ms"] == 1.0


class TestMetricsEndpoints:
    def test_metrics_scoped_to_caller(self):
        run(metrics_service.log_llm_call("/api/rewrite/stream", "m", 5, 5, 120, user_key=TEST_USER))
        run(metrics_service.log_llm_call("/api/rewrite/stream", "m", 5, 5, 120, user_key="user:someone-else"))
        data = client.get("/api/metrics").json()
        assert data["scope"]["user_key"] == TEST_USER
        assert data["llm"]["total_calls"] >= 1
        assert {"llm", "tts", "recent_calls", "endpoint_breakdown", "recent_cycles"} <= set(data)
        assert all(row.get("endpoint") for row in data["recent_calls"])

    def test_export_csv(self):
        res = client.get("/api/metrics/export?format=csv")
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/csv")
        assert res.text.splitlines()[0].startswith("timestamp,endpoint,model")

    def test_export_json(self):
        assert "rows" in client.get("/api/metrics/export?format=json").json()

    def test_removed_endpoints_are_gone(self):
        for path in ("/api/metrics/llm", "/api/metrics/tts", "/api/metrics/recent", "/api/metrics/arena"):
            assert client.get(path).status_code in (404, 405), path
        assert client.post("/api/metrics/cycle", json={}).status_code in (404, 405)


class TestServing:
    """How fast the page itself loads: compression and cheap revalidation."""

    def test_text_assets_are_gzipped_and_revalidated(self):
        res = client.get("/app.js", headers={"Accept-Encoding": "gzip"})
        assert res.status_code == 200 and res.headers["content-encoding"] == "gzip"
        assert res.headers["cache-control"] == "no-cache" and res.headers.get("etag")
        again = client.get("/app.js", headers={"Accept-Encoding": "gzip", "If-None-Match": res.headers["etag"]})
        assert again.status_code == 304 and not again.content

    def test_the_index_page_is_served(self):
        res = client.get("/")
        assert res.status_code == 200 and "Speech Director" in res.text and "Latency Lab" in res.text
        assert "Arena" not in res.text

    def test_audio_is_not_recompressed(self):
        path = AUDIO_OUTPUT_DIR / "test_serving_probe.mp3"
        path.write_bytes(b"ID3" + b"\x00" * 5000)
        try:
            res = client.get("/audio/test_serving_probe.mp3", headers={"Accept-Encoding": "gzip"})
            assert res.status_code == 200 and "content-encoding" not in res.headers
            assert len(res.content) == 5003
        finally:
            remove_audio("test_serving_probe.mp3")

    def test_event_streams_are_not_buffered_by_compression(self):
        # SSE lives under /api, outside the gzipped frontend mount.
        res = client.post("/api/coach/stream", json={"user_text": ""}, headers={"Accept-Encoding": "gzip"})
        assert res.status_code == 422 and "content-encoding" not in res.headers


class TestDatabaseChoice:
    """`auto` must not make every start wait for a MySQL server that is not there."""

    def test_auto_falls_back_to_sqlite_without_calling_the_slow_driver(self):
        from unittest.mock import patch
        from app.services import database
        with patch.object(database, "DB_BACKEND", "auto"), patch.object(database, "_mysql_reachable", return_value=False), \
                patch.object(database, "_init_mysql", side_effect=AssertionError("the driver must not be tried")):
            assert database.init_db() == "sqlite"

    def test_auto_still_uses_mysql_when_it_is_reachable(self):
        from unittest.mock import patch
        from app.services import database
        with patch.object(database, "DB_BACKEND", "auto"), patch.object(database, "_mysql_reachable", return_value=True), \
                patch.object(database, "_init_mysql", side_effect=RuntimeError("down")) as driver:
            assert database.init_db() == "sqlite"        # reachable but the driver failed → still falls back
        assert driver.called

    def test_the_probe_is_fast_and_false_for_a_closed_port(self):
        from app.services import database
        from unittest.mock import patch
        with patch.object(database, "MYSQL_HOST", "127.0.0.1"), patch.object(database, "MYSQL_PORT", 1):
            t = time.perf_counter()
            assert database._mysql_reachable(timeout=0.5) is False
            assert time.perf_counter() - t < 2.0


class TestStartupWarmup:
    """First-request costs are paid at boot, and a failed warm-up never blocks it."""

    def test_warm_llm_builds_the_client_and_makes_one_tiny_request(self):
        from unittest.mock import AsyncMock, MagicMock, patch
        from app.services.llm import warm_llm
        fake = MagicMock()
        fake.ainvoke = AsyncMock()
        with patch("app.services.llm.get_llm", return_value=fake):
            run(warm_llm())
        fake.ainvoke.assert_awaited_once()
        assert fake.ainvoke.await_args.kwargs["max_tokens"] == 1

    def test_warm_llm_swallows_errors(self):
        from unittest.mock import AsyncMock, MagicMock, patch
        from app.services.llm import warm_llm
        fake = MagicMock()
        fake.ainvoke = AsyncMock(side_effect=ConnectionError("offline"))
        with patch("app.services.llm.get_llm", return_value=fake):
            run(warm_llm())          # must not raise

    def test_warm_llm_does_nothing_without_a_key(self):
        from unittest.mock import patch
        from app.services.llm import warm_llm
        with patch("app.services.llm.GROQ_API_KEY", None), patch("app.services.llm.get_llm") as get:
            run(warm_llm())
        get.assert_not_called()

    def test_lifespan_runs_the_warmups_together_and_survives_a_failure(self, monkeypatch):
        from unittest.mock import AsyncMock, patch
        monkeypatch.setenv("STARTUP_CHECKS", "1")
        with patch("app.main.warm_llm", new_callable=AsyncMock) as warm, \
                patch("app.main.check_model_available", new_callable=AsyncMock, side_effect=RuntimeError("boom")) as check, \
                patch("app.services.tts._synth_pool.warm", return_value=3) as azure:
            with TestClient(app) as booted:                 # entering the client runs the lifespan
                assert booted.get("/api/health").status_code == 200
        assert warm.await_count == 1 and check.await_count == 1 and azure.called

    def test_a_hung_warmup_cannot_block_startup(self, monkeypatch):
        import asyncio as aio
        from unittest.mock import AsyncMock, patch

        async def hangs():
            await aio.sleep(3600)

        monkeypatch.setenv("STARTUP_CHECKS", "1")
        real_wait_for = aio.wait_for

        async def quick_wait_for(awaitable, timeout):         # the real limit is 20 s; don't make the suite wait
            return await real_wait_for(awaitable, timeout=0.3)

        with patch("app.main.warm_llm", new=hangs), patch("app.main.check_model_available", new_callable=AsyncMock), \
                patch("app.services.tts._synth_pool.warm", return_value=3), patch("app.main.asyncio.wait_for", new=quick_wait_for):
            t0 = time.perf_counter()
            with TestClient(app) as booted:
                assert booted.get("/api/health").status_code == 200
        assert time.perf_counter() - t0 < 10


@pytest.mark.parametrize("path,status", [("/api/health", 200), ("/api/voices", 200), ("/api/auth/me", 200), ("/api/auth/usage", 200)])
def test_basic_endpoints_respond(path, status):
    assert client.get(path).status_code == status


def test_health_reports_version_model_and_integrations():
    data = client.get("/api/health").json()
    assert data["status"] == "online" and data["version"] == "3.1.0"
    assert data["llm"]["model"] and "available" in data["llm"]
    assert data["whisper_model"].startswith("whisper")
    assert set(data["integrations"]) == {"groq", "azure_tts", "google_oauth"}
