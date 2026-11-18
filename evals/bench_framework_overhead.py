"""
bench_framework_overhead.py — what does LangChain cost per request, in milliseconds?
======================================================================================
The app calls Groq through LangChain (`ChatGroq.astream`, `ChatPromptTemplate`). This measures how much time that adds
compared with calling Groq without it, with NO network, NO model and NO tokens: a fake Groq server on localhost sends a
realistic streamed reply the instant it receives a request, so everything left in the timings is the client's own work.

Three ways of making the same call (the real writer prompt, about 2,300 tokens, and a reply of the same shape as a script):

  langchain   what the app does today: ChatGroq(...).astream(prompt_value)
  groq-sdk    Groq's own official async client, called directly
  raw-httpx   a plain streaming POST and a parse of the server-sent events: the leanest custom client

For each, per request: the time to the first text chunk and to the end of the stream, plus how many HTTP requests the
server saw per call (so "does it make extra requests?" is answered by counting, not guessing). Also the time to build the
prompt from its template, which is the other place the app uses LangChain.

No API key is needed; nothing leaves this machine.
  python -m evals.bench_framework_overhead [--runs 80] [--chunks 600]
"""

import os

os.environ.setdefault("AUTH_REQUIRED", "false")
os.environ.setdefault("DB_BACKEND", "sqlite")
os.environ.setdefault("CADENCE_SKIP_DOTENV", "1")

import argparse  # noqa: E402
import asyncio  # noqa: E402
import json  # noqa: E402
import random  # noqa: E402
import socket  # noqa: E402
import statistics as st  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from groq import AsyncGroq  # noqa: E402
from langchain_groq import ChatGroq  # noqa: E402
from starlette.applications import Starlette  # noqa: E402
from starlette.responses import StreamingResponse  # noqa: E402
from starlette.routing import Route  # noqa: E402

from app.prompts.voxis import writer_prompt  # noqa: E402

MODEL = "qwen/qwen3.8-27b"
REQUESTS_SEEN = []


def make_app(chunks: int):
    words = ("[voice drops] We started this team with three people and a shared whiteboard. Today there are forty of us, "
             "and every one of you carried part of the load. ").split()

    async def completions(request):
        REQUESTS_SEEN.append(time.perf_counter())
        await request.body()

        async def stream():
            for i in range(chunks):
                piece = words[i % len(words)] + " "
                yield ("data: " + json.dumps({"id": "chatcmpl-x", "object": "chat.completion.chunk", "created": 1, "model": MODEL,
                                              "system_fingerprint": "fp_x", "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}]})
                       + "\n\n").encode()
            yield ("data: " + json.dumps({"id": "chatcmpl-x", "object": "chat.completion.chunk", "created": 1, "model": MODEL,
                                          "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                                          "x_groq": {"id": "req_x", "usage": {"queue_time": 0.05, "prompt_tokens": 2319, "prompt_time": 0.18,
                                                                                "completion_tokens": chunks, "completion_time": 1.0,
                                                                                "total_tokens": 2319 + chunks, "total_time": 1.2}}}) + "\n\n").encode()
            yield b"data: [DONE]\n\n"
        return StreamingResponse(stream(), media_type="text/event-stream")

    return Starlette(routes=[Route("/openai/v1/chat/completions", completions, methods=["POST"])])


def start_server(chunks: int) -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(make_app(chunks), host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return port
        except OSError:
            time.sleep(0.05)
    raise RuntimeError("the fake server did not start")


async def via_langchain(llm, prompt_value) -> tuple:
    t0, first = time.perf_counter(), None
    async for chunk in llm.astream(prompt_value):
        if first is None and isinstance(chunk.content, str) and chunk.content:
            first = time.perf_counter() - t0
    return first, time.perf_counter() - t0


async def via_groq_sdk(client, messages) -> tuple:
    t0, first = time.perf_counter(), None
    stream = await client.chat.completions.create(model=MODEL, messages=messages, stream=True, temperature=0.4, max_tokens=2500)
    async for chunk in stream:
        if first is None and chunk.choices and chunk.choices[0].delta.content:
            first = time.perf_counter() - t0
    return first, time.perf_counter() - t0


async def via_raw_httpx(client, url, messages) -> tuple:
    t0, first = time.perf_counter(), None
    body = {"model": MODEL, "messages": messages, "stream": True, "temperature": 0.4, "max_tokens": 2500}
    async with client.stream("POST", url, json=body, headers={"Authorization": "Bearer x"}) as response:
        async for line in response.aiter_lines():
            if not line.startswith("data:") or line.endswith("[DONE]"):
                continue
            chunk = json.loads(line[5:])
            if first is None and chunk["choices"] and chunk["choices"][0]["delta"].get("content"):
                first = time.perf_counter() - t0
    return first, time.perf_counter() - t0


def p95(values):
    values = sorted(values)
    return values[min(len(values) - 1, round(0.95 * len(values)) - 1)]


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=int, default=80)
    parser.add_argument("--chunks", type=int, default=600, help="chunks in the fake reply (a 150-word script is about 250)")
    args = parser.parse_args()
    port = start_server(args.chunks)
    base = f"http://127.0.0.1:{port}"

    prompt_value = await writer_prompt.ainvoke({"user_text": "we won the contract", "target_style": "Confident"})
    messages = [{"role": "system" if m.type == "system" else "user", "content": m.content} for m in prompt_value.to_messages()]

    # 1. the template: LangChain's other job in the app
    t0 = time.perf_counter()
    for _ in range(500):
        await writer_prompt.ainvoke({"user_text": "we won the contract", "target_style": "Confident"})
    template_ms = (time.perf_counter() - t0) / 500 * 1000
    t0 = time.perf_counter()
    for _ in range(500):
        [{"role": "system", "content": messages[0]["content"].replace("{x}", "y")}, {"role": "user", "content": messages[1]["content"]}]
    plain_ms = (time.perf_counter() - t0) / 500 * 1000

    llm = ChatGroq(model=MODEL, temperature=0.4, max_tokens=2500, api_key="x", max_retries=0, groq_api_base=base)
    sdk = AsyncGroq(api_key="x", base_url=base, max_retries=0)
    results = {"langchain": [], "groq-sdk": [], "raw-httpx": []}
    calls_seen = {}
    async with httpx.AsyncClient(timeout=30) as raw:
        runners = {"langchain": lambda: via_langchain(llm, prompt_value), "groq-sdk": lambda: via_groq_sdk(sdk, messages),
                   "raw-httpx": lambda: via_raw_httpx(raw, f"{base}/openai/v1/chat/completions", messages)}
        for name, run in runners.items():            # warm-up and the "how many requests per call" count
            before = len(REQUESTS_SEEN)
            for _ in range(5):
                await run()
            calls_seen[name] = (len(REQUESTS_SEEN) - before) / 5
        order = list(runners)
        for _ in range(args.runs):
            random.shuffle(order)                     # interleaved, so a slow moment does not land on one of them
            for name in order:
                results[name].append(await runners[name]())
    print(f"fake server: {args.chunks} streamed chunks per reply, answered instantly; {args.runs} timed calls per client, interleaved\n")
    print(f"{'client':10} | HTTP requests per call | first chunk (ms): median  p95   | whole stream (ms): median  p95")
    base_first = st.median(r[0] for r in results["raw-httpx"]) * 1000
    base_total = st.median(r[1] for r in results["raw-httpx"]) * 1000
    for name, rows in results.items():
        firsts, totals = [r[0] * 1000 for r in rows], [r[1] * 1000 for r in rows]
        print(f"{name:10} | {calls_seen[name]:>22.0f} | {'':27}{st.median(firsts):6.1f} {p95(firsts):6.1f} | {'':25}{st.median(totals):6.1f} {p95(totals):6.1f}"
              f"   (vs raw-httpx: first {st.median(firsts) - base_first:+.1f} ms, whole {st.median(totals) - base_total:+.1f} ms)")
    print(f"\nbuilding the writer prompt from its LangChain template: {template_ms:.2f} ms a call (a plain string replace of the same size: {plain_ms:.3f} ms)")
    per_chunk = (st.median(r[1] - r[0] for r in results["langchain"]) - st.median(r[1] - r[0] for r in results["raw-httpx"])) * 1000 / args.chunks
    print(f"extra work per streamed chunk, langchain over raw httpx: {per_chunk * 1000:.0f} microseconds")


if __name__ == "__main__":
    asyncio.run(main())
