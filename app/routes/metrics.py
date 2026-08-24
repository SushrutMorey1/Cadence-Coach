"""
metrics route — /api/metrics
===============================
Exposes observability data for the analytics dashboard.
"""

from fastapi import APIRouter

from app.services.metrics import (
    get_llm_summary,
    get_tts_summary,
    get_recent_calls,
    get_endpoint_breakdown,
    get_recent_cycles,
    log_cycle
)
from pydantic import BaseModel
from typing import List, Optional, Dict, Any

class CycleTask(BaseModel):
    name: str
    latency_ms: float
    category: Optional[str] = "general"
    depth: Optional[int] = 0
    parent: Optional[str] = None
    meta: Optional[Dict[str, Any]] = {}

class CycleMetricsRequest(BaseModel):
    cycle_type: str
    total_latency_ms: float
    tasks: List[CycleTask]

router = APIRouter()


@router.get("/metrics")
async def get_metrics():
    """Return aggregated metrics for LLM and TTS usage."""
    return {
        "llm": await get_llm_summary(),
        "tts": await get_tts_summary(),
        "recent_calls": await get_recent_calls(limit=50),
        "endpoint_breakdown": await get_endpoint_breakdown(),
        "recent_cycles": await get_recent_cycles(limit=20),
    }

@router.post("/metrics/cycle")
async def record_cycle(request: CycleMetricsRequest):
    """Log a complete cycle from the frontend."""
    await log_cycle(
        cycle_type=request.cycle_type,
        total_latency_ms=request.total_latency_ms,
        tasks=[t.model_dump() for t in request.tasks]
    )
    return {"status": "ok"}


@router.get("/metrics/llm")
async def get_llm_metrics():
    """Return LLM-specific metrics."""
    return await get_llm_summary()


@router.get("/metrics/tts")
async def get_tts_metrics():
    """Return TTS-specific metrics."""
    return await get_tts_summary()


@router.get("/metrics/recent")
async def get_recent():
    """Return the most recent API calls."""
    return await get_recent_calls(limit=50)
