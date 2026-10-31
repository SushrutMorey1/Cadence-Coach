"""
coach route — /api/coach/history
===================================
The analysis itself streams from /api/coach/stream (routes/stream.py). Every
run is stored, and this returns the caller's recent ones with their deltas so
the page can show a trend.
"""

from fastapi import APIRouter, Query, Request

from app.services.coach_runs import list_coach_runs
from app.services.rate_limiter import get_user_key

router = APIRouter()


@router.get("/coach/history")
async def coach_history(request: Request, limit: int = Query(default=10, ge=1, le=50)):
    """The caller's recent Coach analyses, newest first, each with its delta vs the previous one."""
    runs = await list_coach_runs(get_user_key(request), limit=limit)
    scored = [r["overall_score"] for r in runs if r.get("overall_score") is not None]
    return {
        "runs": runs,
        "count": len(runs),
        "avg_score": round(sum(scored) / len(scored), 1) if scored else None,
        "best_score": round(max(scored), 1) if scored else None,
    }
