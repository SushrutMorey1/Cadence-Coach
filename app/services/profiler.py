"""
profiler.py — Deep Latency Inspection Engine
================================================
Hierarchical, nested span tracing with memory and size metadata.
Each span captures wall-clock time, optional payload sizes, and
supports parent/child nesting for waterfall-style visualization.
"""

import time
import sys
import contextvars
from contextlib import contextmanager

# Context var holds the trace state for the current async request
_trace_ctx = contextvars.ContextVar("_trace_ctx", default=None)


class _TraceState:
    __slots__ = ("spans", "stack", "request_start")

    def __init__(self):
        self.spans = []          # flat list of completed spans
        self.stack = []          # active span stack for nesting
        self.request_start = time.perf_counter()


def init_traces():
    """Call at the start of every request handler."""
    _trace_ctx.set(_TraceState())


def get_traces() -> list:
    """Return the completed span list for the current request."""
    state = _trace_ctx.get()
    if state is None:
        return []

    request_elapsed = (time.perf_counter() - state.request_start) * 1000
    # Compute untracked overhead
    tracked = sum(
        s["latency_ms"] for s in state.spans if s["depth"] == 0
    )
    overhead = max(0, request_elapsed - tracked)

    result = list(state.spans)
    if overhead > 0.05:
        result.append({
            "name": "⚙️ Framework / Routing Overhead",
            "latency_ms": round(overhead, 3),
            "depth": 0,
            "parent": None,
            "category": "overhead",
            "meta": {},
        })
    return result


@contextmanager
def trace(name: str, category: str = "general", **meta):
    """
    Context manager that records a timed span.

    Usage:
        with trace("Groq LLM API", category="network", input_bytes=1234):
            result = await call_api()

    After the block, you can attach output metadata:
        with trace("Groq LLM API", category="network") as span:
            result = await call_api()
            span["output_chars"] = len(result)
    """
    state = _trace_ctx.get()
    if state is None:
        # Tracing not initialized — run the block without tracking
        yield {}
        return

    parent_name = state.stack[-1] if state.stack else None
    depth = len(state.stack)
    span = {"meta": dict(meta)}

    state.stack.append(name)
    start = time.perf_counter()
    try:
        yield span
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000
        state.stack.pop()
        state.spans.append({
            "name": name,
            "latency_ms": round(elapsed_ms, 3),
            "depth": depth,
            "parent": parent_name,
            "category": category,
            "meta": span.get("meta", {}),
        })


def trace_sync(name: str, category: str = "general", **meta):
    """Decorator for synchronous functions."""
    def decorator(fn):
        def wrapper(*args, **kwargs):
            with trace(name, category=category, **meta):
                return fn(*args, **kwargs)
        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        return wrapper
    return decorator


def sizeof_fmt(num_bytes):
    """Human-readable size string."""
    for unit in ("B", "KB", "MB"):
        if abs(num_bytes) < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} GB"
