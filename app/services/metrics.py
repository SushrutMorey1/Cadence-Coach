"""
metrics.py — Lightweight Observability Layer (Async)
======================================================
Logs every LLM and TTS call for cost, latency and usage analytics, and
aggregates them for the Latency Lab. Rows carry the user that caused
them so the Lab is per-user.

All functions are async and never raise — observability must not break
the feature it observes.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from app.services.database import async_execute, async_fetchone, async_fetchall

logger = logging.getLogger(__name__)

# Approximate cost per 1M tokens. These are the original Llama 3.3 70B rates, kept as a
# stand-in estimate — update them to the active model's published Groq pricing.
_COST_PER_1M_INPUT = 0.59    # USD
_COST_PER_1M_OUTPUT = 0.79   # USD


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _scope(user_key: Optional[str], hours: Optional[float], ts_column: str = "timestamp"):
    """WHERE clause + params restricting rows to one user and/or a trailing window."""
    clauses, params = [], []
    if user_key:
        clauses.append("user_key = %s")
        params.append(user_key)
    if hours and hours > 0:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        clauses.append(f"{ts_column} >= %s")
        params.append(cutoff)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, tuple(params)


def _numeric(row: dict) -> dict:
    """MySQL returns Decimal for SUM/AVG; make every number JSON-friendly."""
    for k, v in list(row.items()):
        if hasattr(v, "quantize"):  # Decimal
            row[k] = float(v) if "." in str(v) else int(v)
    return row


def percentile(values: List[float], pct: float) -> Optional[float]:
    """Nearest-rank percentile; None for an empty list."""
    if not values:
        return None
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, round(pct / 100 * (len(ordered) - 1))))
    return round(float(ordered[k]), 1)


def summarize(values: List[float]) -> dict:
    values = [float(v) for v in values if v is not None]
    if not values:
        return {"count": 0, "avg": None, "p50": None, "p95": None, "min": None, "max": None}
    return {
        "count": len(values),
        "avg": round(sum(values) / len(values), 1),
        "p50": percentile(values, 50),
        "p95": percentile(values, 95),
        "min": round(min(values), 1),
        "max": round(max(values), 1),
    }


# ═══════════════════════════════════════════════════════════════
# Writers
# ═══════════════════════════════════════════════════════════════

async def log_llm_call(
    endpoint: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    latency_ms: float,
    status: str = "success",
    user_key: Optional[str] = None,
):
    """Record an LLM call to the metrics database."""
    total_tokens = prompt_tokens + completion_tokens
    cost_usd = (
        (prompt_tokens / 1_000_000) * _COST_PER_1M_INPUT
        + (completion_tokens / 1_000_000) * _COST_PER_1M_OUTPUT
    )
    try:
        await async_execute(
            """
            INSERT INTO llm_metrics
                (timestamp, endpoint, model, prompt_tokens, completion_tokens,
                 total_tokens, latency_ms, cost_usd, status, user_key)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                _now(), endpoint, model, prompt_tokens, completion_tokens, total_tokens,
                round(latency_ms, 1), round(cost_usd, 6), status, user_key,
            ),
        )
    except Exception as e:
        logger.warning("Failed to log LLM metrics: %s", e)


async def log_tts_call(
    voice: str,
    char_count: int,
    duration_sec: Optional[float] = None,
    status: str = "success",
    user_key: Optional[str] = None,
):
    """Record a TTS synthesis call."""
    try:
        await async_execute(
            """
            INSERT INTO tts_metrics (timestamp, voice, char_count, duration_sec, status, user_key)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (_now(), voice, char_count, duration_sec, status, user_key),
        )
    except Exception as e:
        logger.warning("Failed to log TTS metrics: %s", e)


async def log_cycle(
    cycle_type: str,
    total_latency_ms: float,
    tasks: list,
    user_key: Optional[str] = None,
):
    """Log a complete cycle (one user action) and its span tree."""
    try:
        await async_execute(
            """
            INSERT INTO cycle_metrics (timestamp, cycle_type, total_latency_ms, tasks_json, user_key)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (_now(), cycle_type, total_latency_ms, json.dumps(tasks), user_key),
        )
    except Exception as e:
        logger.warning("Failed to log cycle metrics: %s", e)


# ═══════════════════════════════════════════════════════════════
# Readers (all scoped to a user and optional trailing window)
# ═══════════════════════════════════════════════════════════════

async def get_llm_summary(user_key: Optional[str] = None, hours: Optional[float] = None) -> dict:
    """Aggregate statistics from the LLM metrics table, with latency percentiles."""
    where, params = _scope(user_key, hours)
    try:
        row = await async_fetchone(
            f"""
            SELECT
                COUNT(*)                              AS total_calls,
                COALESCE(SUM(prompt_tokens), 0)       AS total_prompt_tokens,
                COALESCE(SUM(completion_tokens), 0)   AS total_completion_tokens,
                COALESCE(SUM(total_tokens), 0)         AS total_tokens,
                COALESCE(ROUND(AVG(latency_ms), 1), 0) AS avg_latency_ms,
                COALESCE(MIN(latency_ms), 0)           AS min_latency_ms,
                COALESCE(MAX(latency_ms), 0)           AS max_latency_ms,
                COALESCE(SUM(cost_usd), 0)             AS total_cost_usd,
                SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success_count,
                SUM(CASE WHEN status != 'success' THEN 1 ELSE 0 END) AS error_count
            FROM llm_metrics{where}
            """,
            params, dictionary=True,
        )
        summary = _numeric(dict(row)) if row else {}
        latencies = await async_fetchall(
            f"SELECT latency_ms FROM llm_metrics{where} ORDER BY id DESC LIMIT 2000", params,
        )
        stats = summarize([r[0] for r in latencies])
        summary["p50_latency_ms"] = stats["p50"]
        summary["p95_latency_ms"] = stats["p95"]
        return summary
    except Exception as e:
        logger.warning("Failed to get LLM summary: %s", e)
        return {}


async def get_tts_summary(user_key: Optional[str] = None, hours: Optional[float] = None) -> dict:
    """Aggregate statistics from the TTS metrics table."""
    where, params = _scope(user_key, hours)
    try:
        row = await async_fetchone(
            f"""
            SELECT
                COUNT(*)                                 AS total_calls,
                COALESCE(SUM(char_count), 0)             AS total_characters,
                COALESCE(ROUND(AVG(duration_sec), 1), 0) AS avg_duration_sec,
                COALESCE(SUM(duration_sec), 0)           AS total_duration_sec,
                SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success_count,
                SUM(CASE WHEN status != 'success' THEN 1 ELSE 0 END) AS error_count
            FROM tts_metrics{where}
            """,
            params, dictionary=True,
        )
        return _numeric(dict(row)) if row else {}
    except Exception as e:
        logger.warning("Failed to get TTS summary: %s", e)
        return {}


async def get_recent_calls(limit: int = 20, user_key: Optional[str] = None, hours: Optional[float] = None) -> list:
    """The most recent LLM calls for the dashboard timeline."""
    where, params = _scope(user_key, hours)
    try:
        rows = await async_fetchall(
            f"""
            SELECT timestamp, endpoint, model, total_tokens, latency_ms, cost_usd, status
            FROM llm_metrics{where}
            ORDER BY id DESC
            LIMIT %s
            """,
            params + (limit,), dictionary=True,
        )
        return [_numeric(dict(r)) for r in rows]
    except Exception as e:
        logger.warning("Failed to get recent calls: %s", e)
        return []


async def get_recent_cycles(limit: int = 20, user_key: Optional[str] = None, hours: Optional[float] = None) -> list:
    """Recent cycles with their span trees (for the waterfall)."""
    where, params = _scope(user_key, hours)
    try:
        rows = await async_fetchall(
            f"SELECT * FROM cycle_metrics{where} ORDER BY id DESC LIMIT %s",
            params + (limit,), dictionary=True,
        )
        result = []
        for d in rows:
            d = dict(d)
            d["tasks"] = _safe_json(d.pop("tasks_json", None), [])
            result.append(d)
        return result
    except Exception as e:
        logger.warning("Failed to get recent cycles: %s", e)
        return []


async def get_endpoint_breakdown(user_key: Optional[str] = None, hours: Optional[float] = None) -> list:
    """Per-endpoint aggregated stats, with p50/p95 latency."""
    where, params = _scope(user_key, hours)
    try:
        rows = await async_fetchall(
            f"""
            SELECT
                endpoint,
                COUNT(*) AS calls,
                COALESCE(ROUND(AVG(latency_ms), 1), 0) AS avg_latency_ms,
                COALESCE(SUM(total_tokens), 0)           AS total_tokens,
                COALESCE(SUM(cost_usd), 0)               AS total_cost_usd,
                SUM(CASE WHEN status != 'success' THEN 1 ELSE 0 END) AS errors
            FROM llm_metrics{where}
            GROUP BY endpoint
            ORDER BY calls DESC
            """,
            params, dictionary=True,
        )
        out = [_numeric(dict(r)) for r in rows]
        if out:
            lat_rows = await async_fetchall(
                f"SELECT endpoint, latency_ms FROM llm_metrics{where} ORDER BY id DESC LIMIT 4000", params,
            )
            by_endpoint: dict = {}
            for endpoint, latency in lat_rows:
                by_endpoint.setdefault(endpoint, []).append(latency)
            for row in out:
                stats = summarize(by_endpoint.get(row["endpoint"], []))
                row["p50_latency_ms"] = stats["p50"]
                row["p95_latency_ms"] = stats["p95"]
        return out
    except Exception as e:
        logger.warning("Failed to get endpoint breakdown: %s", e)
        return []


async def export_llm_rows(user_key: Optional[str], hours: Optional[float] = None, limit: int = 5000) -> list:
    """Raw LLM call rows for CSV/JSON export."""
    where, params = _scope(user_key, hours)
    try:
        rows = await async_fetchall(
            f"""
            SELECT timestamp, endpoint, model, prompt_tokens, completion_tokens, total_tokens,
                   latency_ms, cost_usd, status
            FROM llm_metrics{where}
            ORDER BY id DESC
            LIMIT %s
            """,
            params + (limit,), dictionary=True,
        )
        return [_numeric(dict(r)) for r in rows]
    except Exception as e:
        logger.warning("Failed to export metrics: %s", e)
        return []


def _safe_json(raw, default):
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default
