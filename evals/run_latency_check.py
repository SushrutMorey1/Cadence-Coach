"""
run_latency_check.py — How long does the reader wait?
=======================================================
Runs the same requests through three set-ups of the Speech Director and times
what the reader sees and hears, from sending the request:

  today      today's full prompt, no judge (SAFETY_JUDGE off)
  series     the safety judge, then the writer (SAFETY_JUDGE on, JUDGE_PARALLEL off)
  parallel   the judge and the writer together (SAFETY_JUDGE on, JUDGE_PARALLEL on)

Default mode: the time to the first token on screen (or to the `error` event for a
refused request), then the stream is closed, so only latency is paid for. Re-asks
and rate-limit waits count, because the reader waits through them too.

--speak: the whole request runs with the voice on (real Azure speech) and the run
records the first word, the first sound and where the time between them goes,
read from the request's own trace: the judge, the writer's own time to its first
text, when the first speech piece was ready, how many characters it held and how
long Azure took to render it. The audio files it makes are deleted afterwards.

The set-ups take turns in a rotating order per case, so a slow minute on Groq
does not land on one of them. Needs GROQ_API_KEY set for this command (the key
in .env is not used by accident); Azure's key comes from .env as in the app:
  python -m evals.run_latency_check --cases tiny-win,boxing-pep,technical-explainer,wedding-toast,hate
  python -m evals.run_latency_check --speak --cases tiny-win,vague-professional,boxing-pep,technical-explainer,wedding-toast
"""

import os

_KEY_FROM_ENV = os.environ.get("GROQ_API_KEY")      # read before app.config loads .env

import argparse  # noqa: E402
import asyncio  # noqa: E402
import json  # noqa: E402
import statistics as st  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest.mock import patch  # noqa: E402

os.environ.setdefault("DB_BACKEND", "sqlite")
os.environ.setdefault("SQLITE_PATH", str(Path(tempfile.gettempdir()) / "cadence-eval.db"))
os.environ.setdefault("AUTH_REQUIRED", "false")

from app.config import AUDIO_OUTPUT_DIR  # noqa: E402
from app.services.database import init_db  # noqa: E402
from app.services.director import stream_rewrite  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CASES = Path(__file__).with_name("director_cases.json")
SETUPS = {"today": (False, False), "series": (True, False), "parallel": (True, True)}     # (SAFETY_JUDGE, JUDGE_PARALLEL)


async def time_one(case: dict, setup: str) -> dict:
    """One request, text only: seconds to the first token (written) or to the error (refused)."""
    judge_on, parallel = SETUPS[setup]
    out = {"setup": setup, "case": case["id"], "first_word_s": None, "refused_s": None, "retries": 0, "waits": 0, "error": None}
    t0 = time.perf_counter()
    with patch("app.services.director.SAFETY_JUDGE", judge_on), patch("app.services.director.JUDGE_PARALLEL", parallel):
        gen = stream_rewrite(case["user_text"], case["target_style"], speak=False, user_key="latency-check")
        try:
            async for raw in gen:
                event = json.loads(raw[len("data:"):])
                kind = event.get("type")
                if kind == "retry":
                    out["retries"] += 1
                elif kind == "rate_limited":
                    out["waits"] += 1
                elif kind == "token":
                    out["first_word_s"] = round(time.perf_counter() - t0, 3)
                    break
                elif kind == "error":
                    out["refused_s"] = round(time.perf_counter() - t0, 3)
                    out["error"] = (event.get("message") or "")[:120]
                    break
        finally:
            await gen.aclose()
    return out


def _span(spans: dict, name: str, key: str):
    value = spans.get(name, {}).get(key)
    return None if value is None else round(value, 1)


async def time_speaking(case: dict, setup: str) -> dict:
    """One request with the voice on, run to the end: the first word, the first sound, and the trace's own breakdown."""
    judge_on, parallel = SETUPS[setup]
    out = {"setup": setup, "case": case["id"], "first_word_s": None, "first_audio_s": None, "done_s": None, "retries": 0, "waits": 0,
           "error": None}
    files = []
    t0 = time.perf_counter()
    with patch("app.services.director.SAFETY_JUDGE", judge_on), patch("app.services.director.JUDGE_PARALLEL", parallel):
        gen = stream_rewrite(case["user_text"], case["target_style"], speak=True, user_key="latency-check")
        try:
            async for raw in gen:
                event = json.loads(raw[len("data:"):])
                kind = event.get("type")
                now = round(time.perf_counter() - t0, 3)
                if kind == "retry":
                    out["retries"] += 1
                elif kind == "rate_limited":
                    out["waits"] += 1
                elif kind == "token" and out["first_word_s"] is None:
                    out["first_word_s"] = now
                elif kind == "audio_segment" and event.get("ok"):
                    files.append(event["url"].rsplit("/", 1)[-1])
                    if out["first_audio_s"] is None:
                        out["first_audio_s"] = now
                elif kind == "audio_done":
                    files.append(event["audio"]["filename"])
                    out["app_first_audio_ms"] = event["audio"].get("first_audio_ms")
                elif kind == "error":
                    out["error"] = (event.get("message") or "")[:120]
                    out["done_s"] = now
                    break
                elif kind == "done":
                    out["done_s"] = now
                    spans = {s["name"]: s for s in event.get("detailed_latencies") or []}
                    out.update(
                        judge_ms=_span(spans, "Safety Judge", "latency_ms"),
                        writer_first_chunk_ms=_span(spans, "Writer first chunk (before the verdict)", "latency_ms"),
                        released_first_token_ms=_span(spans, "Time to First Token (TTFT)", "latency_ms"),
                        piece1_ready_ms=_span(spans, "TTS piece 1", "start_ms"),
                        piece1_azure_ms=_span(spans, "TTS piece 1", "latency_ms"),
                        piece1_chars=(spans.get("TTS piece 1", {}).get("meta") or {}).get("chars"))
        finally:
            await gen.aclose()
            for name in dict.fromkeys(files):
                (AUDIO_OUTPUT_DIR / name).unlink(missing_ok=True)
    return out


def _cell(r, key):
    return "-" if r is None or r.get(key) is None else f"{r[key]:.2f} s"


def summary(rows: list) -> list:
    """Text mode: one row per case. Voice mode: first word and first sound per case."""
    speaking = any("first_audio_s" in r for r in rows)
    lines = ["| case | " + " | ".join(SETUPS) + " |", "|---|" + "---|" * len(SETUPS)]
    for case in dict.fromkeys(r["case"] for r in rows):
        cells = []
        for setup in SETUPS:
            r = next((x for x in rows if x["case"] == case and x["setup"] == setup), None)
            if not r:
                cells.append("-")
                continue
            extra = (f", {r['retries']} re-ask" if r["retries"] else "") + (f", {r['waits']} wait" if r["waits"] else "")
            if speaking:
                cells.append(f"word {_cell(r, 'first_word_s')}, sound {_cell(r, 'first_audio_s')}{extra}" if not r["error"] else "refused")
                continue
            value = r["first_word_s"] if r["first_word_s"] is not None else r["refused_s"]
            what = "first word" if r["first_word_s"] is not None else "refused"
            cells.append(f"{value:.2f} s ({what}{extra})" if value is not None else "no answer")
        lines.append(f"| {case} | " + " | ".join(cells) + " |")
    for setup in SETUPS:
        mine = [r for r in rows if r["setup"] == setup]
        words = [r["first_word_s"] for r in mine if r["first_word_s"] is not None]
        if words:
            lines.append(f"- {setup}: first word median {st.median(words):.2f} s, max {max(words):.2f} s over {len(words)} written requests")
        if speaking:
            sounds = [r["first_audio_s"] for r in mine if r.get("first_audio_s") is not None]
            if sounds:
                lines.append(f"  - first sound median {st.median(sounds):.2f} s, max {max(sounds):.2f} s; "
                             f"{st.median(r['first_audio_s'] - r['first_word_s'] for r in mine if r.get('first_audio_s') and r['first_word_s']):.2f} s "
                             f"between first word and first sound (median)")
    if speaking:
        lines += ["", "Where the time between the first word and the first sound goes (medians, milliseconds, from each request's own trace):", "",
                  "| set-up | judge | writer's own first text | first speech piece ready | its characters | Azure render of it |", "|---|---|---|---|---|---|"]
        for setup in SETUPS:
            def med(key, mine=[r for r in rows if r["setup"] == setup]):
                values = [r[key] for r in mine if r.get(key) is not None]
                return f"{st.median(values):.0f}" if values else "-"
            lines.append(f"| {setup} | {med('judge_ms')} | {med('writer_first_chunk_ms')} | {med('piece1_ready_ms')} | {med('piece1_chars')} | "
                         f"{med('piece1_azure_ms')} |")
    return lines


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cases", required=True, help="comma-separated case ids from director_cases.json")
    parser.add_argument("--speak", action="store_true", help="run each request with the voice on and time the first sound")
    parser.add_argument("--gap", type=float, default=25.0, help="seconds between requests (keeps under the per-minute budget)")
    args = parser.parse_args()
    if not _KEY_FROM_ENV:
        sys.exit("GROQ_API_KEY is not set for this command; the key in .env is deliberately not used.")
    sys.stdout.reconfigure(errors="replace")
    by_id = {c["id"]: c for c in json.loads(CASES.read_text(encoding="utf-8"))}
    cases = [by_id[c] for c in args.cases.split(",")]
    init_db()
    out_dir = ROOT / "evals" / "out" / ("latency-" + ("speak-" if args.speak else "") + time.strftime("%Y%m%d-%H%M%S"))
    out_dir.mkdir(parents=True)
    run = time_speaking if args.speak else time_one
    rows, order, first = [], list(SETUPS), True
    for n, case in enumerate(cases):
        turn = order[n % len(order):] + order[:n % len(order)]        # rotate who goes first
        for setup in turn:
            if not first:
                await asyncio.sleep(args.gap)
            first = False
            row = await run(case, setup)
            rows.append(row)
            (out_dir / "results.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
            print(f"{case['id']:22} {setup:9} " + "  ".join(f"{k} {v}" for k, v in row.items() if k not in ("setup", "case", "error") and v is not None), flush=True)
    lines = summary(rows)
    (out_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n" + "\n".join(lines))
    print("Results:", out_dir)


if __name__ == "__main__":
    asyncio.run(main())
