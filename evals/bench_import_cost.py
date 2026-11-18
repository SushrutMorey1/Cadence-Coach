"""
bench_import_cost.py — how long does it take to import LangChain, against the Groq SDK and plain httpx?
=========================================================================================================
Each case runs in a fresh Python process (median of 7), so it is the cost of starting the app, not of a request: a one-time
cost at boot that matters most on a slow host that sleeps and wakes (a free Render instance). Time only: memory is not
measured here. No network, no tokens.

  python -m evals.bench_import_cost
"""

import statistics as st
import subprocess
import sys

CASES = {
    "httpx only": "import httpx",
    "groq SDK only": "import groq",
    "langchain_core.prompts only": "import langchain_core.prompts",
    "langchain_groq (what the app imports)": "import langchain_groq, langchain_core.prompts, langchain_core.messages",
}
PROBE = "import time; t = time.perf_counter(); {code}; print(round((time.perf_counter() - t) * 1000))"

if __name__ == "__main__":
    for name, code in CASES.items():
        times = [int(subprocess.run([sys.executable, "-I", "-c", PROBE.format(code=code)], capture_output=True, text=True).stdout)
                 for _ in range(7)]
        print(f"{name:40} median {st.median(times):5.0f} ms (range {min(times)} to {max(times)})")
