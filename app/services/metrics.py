"""
metrics.py — Lightweight Observability Layer (Async)
======================================================
Logs every LLM and TTS call to MySQL for cost,
latency, and usage analytics. All functions are async
to avoid blocking the FastAPI event loop.
"""

import logging
from datetime import datetime, timezone
from typing import Optional
import json

from app.services.database import async_execute, async_fetchone, async_fetchall

logger = logging.getLogger(__name__)

# Approximate cost per 1M tokens for Groq Llama 3.3 70B
_COST_PER_1M_INPUT = 0.59    # USD
_COST_PER_1M_OUTPUT = 0.79   # USD


async def log_llm_call(
    endpoint: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    latency_ms: float,
    status: str = "success",
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
                 total_tokens, latency_ms, cost_usd, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                endpoint,
                model,
                prompt_tokens,
                completion_tokens,
                total_tokens,
                round(latency_ms, 1),
                round(cost_usd, 6),
                status,
            ),
        )
    except Exception as e:
        logger.warning("Failed to log LLM metrics: %s", e)


async def log_tts_call(
    voice: str,
    char_count: int,
    duration_sec: Optional[float] = None,
    status: str = "success",
):
    """Record a TTS synthesis call."""
    try:
        await async_execute(
            """
            INSERT INTO tts_metrics (timestamp, voice, char_count, duration_sec, status)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                voice,
                char_count,
                duration_sec,
                status,
            ),
        )
    except Exception as e:
        logger.warning("Failed to log TTS metrics: %s", e)


async def get_llm_summary() -> dict:
    """Return aggregate statistics from the LLM metrics table."""
    try:
        row = await async_fetchone(
            """
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
            FROM llm_metrics
            """,
            dictionary=True,
        )
        if row:
            for k, v in row.items():
                if hasattr(v, 'quantize'):  # is decimal
                    row[k] = float(v) if '.' in str(v) else int(v)
        return dict(row) if row else {}
    except Exception as e:
        logger.warning("Failed to get LLM summary: %s", e)
        return {}


async def get_tts_summary() -> dict:
    """Return aggregate statistics from the TTS metrics table."""
    try:
        row = await async_fetchone(
            """
            SELECT
                COUNT(*)                                 AS total_calls,
                COALESCE(SUM(char_count), 0)             AS total_characters,
                COALESCE(ROUND(AVG(duration_sec), 1), 0) AS avg_duration_sec,
                COALESCE(SUM(duration_sec), 0)           AS total_duration_sec,
                SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success_count,
                SUM(CASE WHEN status != 'success' THEN 1 ELSE 0 END) AS error_count
            FROM tts_metrics
            """,
            dictionary=True,
        )
        if row:
            for k, v in row.items():
                if hasattr(v, 'quantize'):  # is decimal
                    row[k] = float(v) if '.' in str(v) else int(v)
        return dict(row) if row else {}
    except Exception as e:
        logger.warning("Failed to get TTS summary: %s", e)
        return {}


async def get_recent_calls(limit: int = 20) -> list:
    """Return the most recent LLM calls for the dashboard timeline."""
    try:
        rows = await async_fetchall(
            """
            SELECT timestamp, endpoint, model, total_tokens,
                   latency_ms, cost_usd, status
            FROM llm_metrics
            ORDER BY id DESC
            LIMIT %s
            """,
            (limit,),
            dictionary=True,
        )
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning("Failed to get recent calls: %s", e)
        return []


async def log_cycle(cycle_type: str, total_latency_ms: float, tasks: list):
    """Log a complete cycle and its sub-task latencies."""
    try:
        await async_execute(
            """
            INSERT INTO cycle_metrics (timestamp, cycle_type, total_latency_ms, tasks_json)
            VALUES (%s, %s, %s, %s)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                cycle_type,
                total_latency_ms,
                json.dumps(tasks)
            )
        )
    except Exception as e:
        logger.warning("Failed to log cycle metrics: %s", e)


async def get_recent_cycles(limit: int = 20) -> list:
    """Return recent cycles and their tasks breakdown."""
    try:
        rows = await async_fetchall(
            "SELECT * FROM cycle_metrics ORDER BY id DESC LIMIT %s",
            (limit,),
            dictionary=True,
        )
        
        result = []
        for d in rows:
            try:
                d["tasks"] = json.loads(d["tasks_json"])
            except Exception:
                d["tasks"] = []
            result.append(dict(d))
        return result
    except Exception as e:
        logger.warning("Failed to get recent cycles: %s", e)
        return []


async def get_endpoint_breakdown() -> list:
    """Return per-endpoint aggregated stats."""
    try:
        rows = await async_fetchall(
            """
            SELECT
                endpoint,
                COUNT(*) AS calls,
                COALESCE(ROUND(AVG(latency_ms), 1), 0) AS avg_latency_ms,
                COALESCE(SUM(total_tokens), 0)           AS total_tokens,
                COALESCE(SUM(cost_usd), 0)               AS total_cost_usd
            FROM llm_metrics
            GROUP BY endpoint
            ORDER BY calls DESC
            """,
            dictionary=True,
        )
        for row in rows:
            for k, v in row.items():
                if hasattr(v, 'quantize'):  # is decimal
                    row[k] = float(v) if '.' in str(v) else int(v)
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning("Failed to get endpoint breakdown: %s", e)
        return []
