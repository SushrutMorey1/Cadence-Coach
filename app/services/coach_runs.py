"""
coach_runs.py — Communication Coach history
==============================================
Every Coach analysis is stored so the user can see whether they are
improving. Dialect-neutral SQL only.
"""

import json
import logging
from datetime import datetime, timezone
from typing import List, Optional

from app.services.database import async_execute, async_fetchall

logger = logging.getLogger(__name__)

CATEGORY_KEYS = (
    "vocabulary", "tone_and_emotion", "voice_modulation",
    "pacing_and_rhythm", "emphasis_and_delivery", "clarity_and_structure",
)


def _loads(raw, default):
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def category_scores(result: dict) -> dict:
    """{category: score} pulled out of a CoachResponse-shaped dict."""
    scores = {}
    for key in CATEGORY_KEYS:
        block = result.get(key) or {}
        if isinstance(block, dict) and block.get("score") is not None:
            scores[key] = round(float(block["score"]), 1)
    return scores


async def save_coach_run(
    user_key: str,
    result: dict,
    text: str,
    source: str = "text",
    context: Optional[str] = None,
    speech_metrics: Optional[dict] = None,
) -> None:
    """Persist one analysis. Never raises — history is a nice-to-have."""
    metrics = None
    if speech_metrics:
        metrics = {k: speech_metrics.get(k) for k in (
            "wpm", "wpm_rating", "duration_sec", "word_count", "filler_count",
            "filler_rate_pct", "long_pauses",
        ) if speech_metrics.get(k) is not None}
    try:
        await async_execute(
            """
            INSERT INTO coach_runs
                (user_key, created_at, source, context, overall_score,
                 scores_json, metrics_json, actions_json, text_excerpt)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                user_key,
                datetime.now(timezone.utc).isoformat(),
                (source or "text")[:16],
                (context or "")[:1000] or None,
                result.get("overall_score"),
                json.dumps(category_scores(result)),
                json.dumps(metrics) if metrics else None,
                json.dumps((result.get("top_3_actions") or [])[:3]),
                " ".join((text or "").split())[:500],
            ),
        )
    except Exception as err:
        logger.warning("save_coach_run failed: %s", err)


async def list_coach_runs(user_key: str, limit: int = 10) -> List[dict]:
    """Most recent analyses first, each with its delta against the one before it."""
    try:
        rows = await async_fetchall(
            """
            SELECT id, created_at, source, context, overall_score,
                   scores_json, metrics_json, actions_json, text_excerpt
            FROM coach_runs
            WHERE user_key = %s
            ORDER BY id DESC
            LIMIT %s
            """,
            (user_key, max(1, min(limit, 50)) + 1),
            dictionary=True,
        )
    except Exception as err:
        logger.warning("list_coach_runs failed: %s", err)
        return []

    runs = []
    for r in rows:
        r = dict(r)
        r["scores"] = _loads(r.pop("scores_json", None), {})
        r["metrics"] = _loads(r.pop("metrics_json", None), {})
        r["actions"] = _loads(r.pop("actions_json", None), [])
        runs.append(r)

    # Delta vs the previous run (rows are newest-first, so "previous" is the next row).
    for i, run in enumerate(runs):
        prev = runs[i + 1] if i + 1 < len(runs) else None
        if prev and run.get("overall_score") is not None and prev.get("overall_score") is not None:
            run["delta"] = round(run["overall_score"] - prev["overall_score"], 1)
        else:
            run["delta"] = None
    return runs[:limit]
