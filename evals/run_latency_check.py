"""
run_latency_check.py — How long does the reader wait for the first word?
==========================================================================
Runs the same requests through three set-ups of the Speech Director and times
what the reader sees, from sending the request:

  today      today's full prompt, no judge (SAFETY_JUDGE off)
  series     the safety judge, then the writer (SAFETY_JUDGE on, JUDGE_PARALLEL off)
  parallel   the judge and the writer together (SAFETY_JUDGE on, JUDGE_PARALLEL on)

For a request that is written: the time to the first token on screen, then the
stream is closed (only latency is measured, so no full script is paid for).
For a refused one: the time to the `error` event. Re-asks and rate-limit waits
are counted, because the reader waits through them too. The set-ups take turns
in a rotating order per case, so a slow minute on Groq does not land on one.

Needs GROQ_API_KEY set for this command (the key in .env is not used by accident):
  python -m evals.run_latency_check --cases tiny-win,boxing-pep,technical-explainer,wedding-toast,hate
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

from app.services.database import init_db  # noqa: E402
from app.services.director import stream_rewrite  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CASES = Path(__file__).with_name("director_cases.json")
SETUPS = {"today": (False, False), "series": (True, False), "parallel": (True, True)}     # (SAFETY_JUDGE, JUDGE_PARALLEL)


async def time_one(case: dict, setup: str) -> dict:
    """One request: seconds to the first token (written) or to the error (refused), and what happened on the way."""
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


def summary(rows: list) -> list:
    lines = ["| case | " + " | ".join(SETUPS) + " |", "|---|" + "---|" * len(SETUPS)]
    for case in dict.fromkeys(r["case"] for r in rows):
        cells = []
        for setup in SETUPS:
            r = next((x for x in rows if x["case"] == case and x["setup"] == setup), None)
            if not r:
                cells.append("-")
                continue
            value = r["first_word_s"] if r["first_word_s"] is not None else r["refused_s"]
            what = "first word" if r["first_word_s"] is not None else "refused"
            extra = (f", {r['retries']} re-ask" if r["retries"] else "") + (f", {r['waits']} wait" if r["waits"] else "")
            cells.append(f"{value:.2f} s ({what}{extra})" if value is not None else "no answer")
        lines.append(f"| {case} | " + " | ".join(cells) + " |")
    for setup in SETUPS:
        written = [r["first_word_s"] for r in rows if r["setup"] == setup and r["first_word_s"] is not None]
        if written:
            lines.append(f"- {setup}: first word median {st.median(written):.2f} s, max {max(written):.2f} s over {len(written)} written requests")
    return lines


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cases", required=True, help="comma-separated case ids from director_cases.json")
    parser.add_argument("--gap", type=float, default=25.0, help="seconds between requests (keeps under the per-minute budget)")
    args = parser.parse_args()
    if not _KEY_FROM_ENV:
        sys.exit("GROQ_API_KEY is not set for this command; the key in .env is deliberately not used.")
    sys.stdout.reconfigure(errors="replace")
    by_id = {c["id"]: c for c in json.loads(CASES.read_text(encoding="utf-8"))}
    cases = [by_id[c] for c in args.cases.split(",")]
    init_db()
    out_dir = ROOT / "evals" / "out" / ("latency-" + time.strftime("%Y%m%d-%H%M%S"))
    out_dir.mkdir(parents=True)
    rows, order, first = [], list(SETUPS), True
    for n, case in enumerate(cases):
        turn = order[n % len(order):] + order[:n % len(order)]        # rotate who goes first
        for setup in turn:
            if not first:
                await asyncio.sleep(args.gap)
            first = False
            row = await time_one(case, setup)
            rows.append(row)
            (out_dir / "results.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
            print(f"{case['id']:22} {setup:9} first word {row['first_word_s']}  refused {row['refused_s']}  "
                  f"re-asks {row['retries']}  waits {row['waits']}", flush=True)
    lines = summary(rows)
    (out_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n" + "\n".join(lines))
    print("Results:", out_dir)


if __name__ == "__main__":
    asyncio.run(main())
