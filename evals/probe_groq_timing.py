"""
probe_groq_timing.py — How much of the wait for the first word is Groq, and how much is the network?
=====================================================================================================
Sends a few real writer-prompt requests straight to Groq (streaming) and records, for each:

  first_text_s     what this machine observes: the request sent -> the first text chunk back
  queue_ms         Groq's own report: time the request waited in its queue
  prompt_ms        Groq's own report: time to read the prompt (the ~2,300-token writer prompt)
  server_first_ms  queue + prompt + about one token of generation: roughly when Groq itself could have sent the first text
  network_ms       first_text_s minus server_first_ms: what is left, i.e. the round trip and connection overhead

and, with no tokens spent, the plain round trip to Groq (a GET of /models) a few times. The output is capped at 40 tokens:
only the start of a reply is needed. This measures this machine to Groq, not a deployed server to Groq.

Needs GROQ_API_KEY set for this command (the second account's key; the key in .env is not used):
  python -m evals.probe_groq_timing [--runs 6]
"""

import os

_KEY_FROM_ENV = os.environ.get("GROQ_API_KEY")      # read before app.config loads .env

import argparse  # noqa: E402
import json  # noqa: E402
import statistics as st  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402

import httpx  # noqa: E402

os.environ.setdefault("AUTH_REQUIRED", "false")
os.environ.setdefault("DB_BACKEND", "sqlite")

from app.config import LLM_MODEL, LLM_TEMPERATURE  # noqa: E402
from app.prompts.voxis import writer_prompt  # noqa: E402

API = "https://api.groq.com/openai/v1"
TEXTS = [("we won the contract", "Confident"), ("thank you volunteers", "Heartfelt"), ("new office opens monday", "Cheerful")]


def one_request(client: httpx.Client, headers: dict, text: str, style: str) -> dict:
    messages = [{"role": "system" if m.type == "system" else "user", "content": m.content}
                for m in writer_prompt.format_messages(user_text=text, target_style=style)]
    body = {"model": LLM_MODEL, "messages": messages, "stream": True, "temperature": LLM_TEMPERATURE, "max_tokens": 40}
    t0, first, usage = time.perf_counter(), None, {}
    with client.stream("POST", f"{API}/chat/completions", headers=headers, json=body, timeout=30) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line.startswith("data:") or line.endswith("[DONE]"):
                continue
            chunk = json.loads(line[5:])
            if first is None and any((c.get("delta") or {}).get("content") for c in chunk.get("choices") or []):
                first = time.perf_counter() - t0
            usage = (chunk.get("x_groq") or {}).get("usage") or usage
    out = {"first_text_s": round(first, 3) if first else None, "prompt_tokens": usage.get("prompt_tokens")}
    if usage.get("queue_time") is not None or usage.get("prompt_time") is not None:
        queue, prompt = (usage.get("queue_time") or 0) * 1000, (usage.get("prompt_time") or 0) * 1000
        tokens, comp = usage.get("completion_tokens") or 1, (usage.get("completion_time") or 0) * 1000
        out.update(queue_ms=round(queue, 1), prompt_ms=round(prompt, 1), server_first_ms=round(queue + prompt + comp / tokens, 1))
        if first:
            out["network_ms"] = round(first * 1000 - out["server_first_ms"], 1)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=int, default=6)
    parser.add_argument("--gap", type=float, default=25.0, help="seconds between requests: each one uses about 2,300 tokens of the 8,000-a-minute budget")
    args = parser.parse_args()
    if not _KEY_FROM_ENV:
        sys.exit("GROQ_API_KEY is not set for this command; the key in .env is deliberately not used.")
    headers = {"Authorization": f"Bearer {_KEY_FROM_ENV}"}
    with httpx.Client() as client:
        pings = []
        for _ in range(6):
            t0 = time.perf_counter()
            client.get(f"{API}/models", headers=headers, timeout=15).raise_for_status()
            pings.append((time.perf_counter() - t0) * 1000)
        print(f"plain round trip to Groq (GET /models, no tokens): median {st.median(pings):.0f} ms, min {min(pings):.0f}, max {max(pings):.0f} "
              f"(first call includes the connection setup: {pings[0]:.0f} ms)")
        rows = []
        for i in range(args.runs):
            text, style = TEXTS[i % len(TEXTS)]
            if i:
                time.sleep(args.gap)
            row = one_request(client, headers, text, style)
            rows.append(row)
            print(f"run {i + 1}: " + "  ".join(f"{k} {v}" for k, v in row.items() if v is not None), flush=True)
    for key in ("first_text_s", "queue_ms", "prompt_ms", "server_first_ms", "network_ms"):
        values = [r[key] for r in rows if r.get(key) is not None]
        if values:
            print(f"{key:16} median {st.median(values):8.3f}   min {min(values):8.3f}   max {max(values):8.3f}   (n={len(values)})")


if __name__ == "__main__":
    main()
