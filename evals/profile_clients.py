"""
profile_clients.py — where does each client spend its time per streamed chunk?
================================================================================
cProfile of the three clients in evals/bench_framework_overhead.py (LangChain's ChatGroq, Groq's SDK, plain httpx) against
the same fake Groq server on localhost: no network, no tokens. The profiler slows everything down about threefold, so read
the proportions and the calls per request, not the absolute milliseconds (the benchmark has the real timings).

  python -m evals.profile_clients
"""

import asyncio
import cProfile
import io
import pstats

import httpx
from groq import AsyncGroq
from langchain_groq import ChatGroq

from evals import bench_framework_overhead as bench

CHUNKS, CALLS = 600, 12


def report(title: str, profiler: cProfile.Profile, top: int = 9) -> None:
    stats = pstats.Stats(profiler, stream=io.StringIO()).sort_stats("tottime")
    total = stats.total_tt
    rows = sorted(((tt, nc, func[2]) for func, (cc, nc, tt, ct, callers) in stats.stats.items()), reverse=True)
    print(f"\n== {title}: {total * 1000 / CALLS:.1f} ms per call under the profiler ({total * 1e6 / CALLS / CHUNKS:.0f} microseconds per chunk); "
          "top functions by their own time")
    for tt, nc, name in rows[:top]:
        print(f"   {tt * 1000 / CALLS:7.1f} ms ({100 * tt / total:4.1f}%)  {nc // CALLS:6d} calls per request  {name}")


async def main() -> None:
    port = bench.start_server(CHUNKS)
    base = f"http://127.0.0.1:{port}"
    prompt_value = await bench.writer_prompt.ainvoke({"user_text": "we won the contract", "target_style": "Confident"})
    messages = [{"role": "system" if m.type == "system" else "user", "content": m.content} for m in prompt_value.to_messages()]
    llm = ChatGroq(model=bench.MODEL, temperature=0.4, max_tokens=2500, api_key="x", max_retries=0, groq_api_base=base)
    sdk = AsyncGroq(api_key="x", base_url=base, max_retries=0)
    async with httpx.AsyncClient(timeout=30) as raw:
        jobs = {"raw httpx (hand-written)": lambda: bench.via_raw_httpx(raw, f"{base}/openai/v1/chat/completions", messages),
                "Groq SDK": lambda: bench.via_groq_sdk(sdk, messages),
                "LangChain ChatGroq": lambda: bench.via_langchain(llm, prompt_value)}
        for name, run in jobs.items():
            for _ in range(3):
                await run()                                    # warm up
            profiler = cProfile.Profile()
            profiler.enable()
            for _ in range(CALLS):
                await run()
            profiler.disable()
            report(name, profiler)


if __name__ == "__main__":
    asyncio.run(main())
