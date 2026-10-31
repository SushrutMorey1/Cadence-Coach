"""
metrics route — /api/metrics
===============================
Observability data for the Latency Lab. Everything is scoped to the
calling user (so one person's Lab never shows another's calls) and can
be narrowed to a trailing window with `?hours=`.

  GET /api/metrics?hours=24        summaries, breakdown, recent calls + cycles
  GET /api/metrics/export?format=  the raw LLM rows as CSV or JSON
"""

import csv
import io
from typing import Optional

from fastapi import APIRouter, Request, Query
from fastapi.responses import StreamingResponse

from app.services.metrics import (
    get_llm_summary,
    get_tts_summary,
    get_recent_calls,
    get_endpoint_breakdown,
    get_recent_cycles,
    export_llm_rows,
)
from app.services.rate_limiter import get_user_key

router = APIRouter()


def _hours(hours: Optional[float]) -> Optional[float]:
    return hours if hours and hours > 0 else None


@router.get("/metrics")
async def get_metrics(request: Request, hours: Optional[float] = Query(default=None, ge=0, le=24 * 365)):
    """Aggregated metrics for LLM and TTS usage, scoped to the caller."""
    user_key = get_user_key(request)
    h = _hours(hours)
    return {
        "scope": {"user_key": user_key, "hours": h},
        "llm": await get_llm_summary(user_key, h),
        "tts": await get_tts_summary(user_key, h),
        "recent_calls": await get_recent_calls(limit=50, user_key=user_key, hours=h),
        "endpoint_breakdown": await get_endpoint_breakdown(user_key, h),
        "recent_cycles": await get_recent_cycles(limit=20, user_key=user_key, hours=h),
    }


@router.get("/metrics/export")
async def export_metrics(
    request: Request,
    format: str = Query(default="csv", pattern="^(csv|json)$"),
    hours: Optional[float] = Query(default=None, ge=0, le=24 * 365),
):
    """Download the caller's LLM call log."""
    rows = await export_llm_rows(get_user_key(request), _hours(hours))
    if format == "json":
        return {"rows": rows, "count": len(rows)}

    buf = io.StringIO()
    fields = ["timestamp", "endpoint", "model", "prompt_tokens", "completion_tokens", "total_tokens",
              "latency_ms", "cost_usd", "status"]
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="cadence-latency-lab.csv"'},
    )
