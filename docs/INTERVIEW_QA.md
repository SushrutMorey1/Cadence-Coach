# Interview notes: questions this project can answer with measurements

Each entry has a short answer to say out loud, the numbers behind it, the follow-up questions that usually come next, and what is *not* proven. Every number comes from a script in `evals/` that can be re-run; the full record is in `docs/SAFETY_EVAL.md`.

---

## 1. "Frameworks like LangChain are for prototypes; production code is custom and faster. Why not drop it?"

**Short answer (about 30 seconds).** I measured it instead of assuming it. Dropping LangChain would make one request about 0.6 milliseconds faster to the first word, out of a real wait of about 480 ms, so about 0.1%. There are two real costs to the framework, and neither is request latency: it takes about a second longer to start the app, and it uses about four times more CPU per streamed chunk, which only matters with many users at once. So I kept it, and I'd only replace it for start-up time, server capacity or dependency weight.

**What I measured, and how.** `evals/bench_framework_overhead.py` starts a fake Groq server on localhost that answers instantly with a streamed reply of 600 chunks. The same request (the real writer prompt, about 2,300 tokens) goes through three clients, 80 calls each, interleaved. With no network and no model in the way, only the client's own work is left. It costs zero tokens.

| Client | HTTP requests per call | First chunk | Whole 600-chunk stream |
|---|---|---|---|
| LangChain `ChatGroq` (what the app uses) | 1 | 9.9 ms | 185 ms |
| Groq's own SDK | 1 | 9.9 ms | 141 ms |
| Plain httpx, parsing the stream by hand | 1 | 9.3 ms | 37 ms |

| Other measurements | LangChain | Without it |
|---|---|---|
| Building the writer prompt | 0.33 ms | 0.003 ms (a string replace) |
| Importing what the app needs (median of 7 fresh processes, two runs) | 1.7 to 1.9 s | about 0.9 s (Groq SDK alone), about 0.4 s (httpx) |
| Client work per streamed chunk | 308 µs | 61 µs (SDK: 235 µs) |

**Where the 5× comes from, and why a user never feels it.** The whole-stream row is 185 ms against 37 ms, about 5×. That is how much computer work each client needs to read 600 chunks that all arrive at once. In a real request the chunks arrive over one to two seconds, because Groq is writing the script during that time, and the client's work happens inside that waiting. Measured on two real replies: one token per chunk, 341 and 630 chunks, about 495 chunks a second, arriving in bursts (median gap 0.12 to 0.18 ms, mean 2.0 ms, 90th percentile 4 to 6 ms). LangChain's 0.25 ms a chunk is busy about 12% of the time on average and catches up between bursts, so the cost to one request is a few milliseconds on the last tokens at most. The 5× is a statement about CPU per stream (about 12% of a core against about 3%), not about the user's wait.

**Why the framework does more work per chunk.** A profile (cProfile against the fake server) shows plain httpx turning each chunk into a dictionary with one `json.loads`. Groq's SDK builds typed objects for every chunk (the chunk, its choice, its delta, the usage extras) by walking the type definitions at run time: about 11 `construct_type` calls and about 170 `isinstance` checks per chunk. LangChain's `ChatGroq` calls that SDK underneath (about 70% of its cost) and then converts every chunk again into its own message objects with Pydantic validation (about 30%). What that buys is typed, validated objects, retries and proper error classes; a hand-written client has to handle every one of those cases itself.

**Where the wait actually goes.** About 0.48 s to the first word: roughly 235 ms is the network round trip and connection overhead, about 180 ms is Groq reading the 2,300-token prompt, about 50 ms is Groq's queue (with rare spikes: one reading was 778 ms). After the first word, Groq writes about 500 tokens a second, so a script takes one to two seconds; that is the model's speed. Neither part is something client code can change.

**Follow-ups to be ready for**
- *"So is the industry wrong to use custom code?"* No. Many teams call the provider's SDK directly or write a thin wrapper, but the usual reasons are fewer dependencies, easier debugging, less version churn and control. Speed is rarely the reason, and here it is not measurable.
- *"Does the framework make extra network requests?"* I counted on the server side: one HTTP request per call, for all three clients.
- *"When would you replace it?"* If cold-start time on a free host mattered (about a second saved at boot), or if one server had to stream to hundreds of users at once (CPU per stream), or to drop a heavy dependency. In this app LangChain is used in about 8 places: prompt templates and `ChatGroq` streaming for the writer, the judge and the coach.
- *"What would the replacement look like?"* Groq's SDK or plain httpx plus string templates, behind the same `astream` interface the tests already fake. About half a day with tests; I did not do it because it cannot change the user's wait.

**Not proven.** The start-up numbers are from my machine, not a slow free host. Memory was not measured. The 12% CPU figure is the measured per-chunk cost times the measured chunk rate, not a load test.

---

## 2. "Why stream the reply? Isn't that just hiding latency from the user? Wouldn't one response be faster?"

**Short answer.** Streaming is not a trick and sending one response cannot finish sooner. Groq writes the script at the same speed either way, so the last word arrives at the same moment; streaming just lets the first words, and the first sound, arrive about a second earlier. For the voice it matters most: speech can start as soon as the first sentence is written, instead of after the whole script.

| | Streaming (the app) | One response |
|---|---|---|
| Groq finishes writing at | time T | time T |
| First word on screen | about 0.5 s | when the whole script is back, about 1.2 to 2 s |
| Last word on screen | about T | about T |
| First sound (measured with streaming: 0.90 s in parallel mode) | about 0.9 s | about 1.5 to 2.3 s (inferred: whole script, then Azure's first chunk) |

What a non-streaming call would save is the client's per-chunk processing (a few milliseconds, the 5× above) and nothing else; the network cost is paid once either way. The one place the app does not stream a result is the safety judge: it is a single short call with a one-word answer.

**Not proven.** The "same finish time" claim follows from how generation works; I have not timed a non-streaming call in this app, and the first-sound figure for a non-streaming version is an estimate (the streaming figure is measured).

---

## 3. Related measurements that come up in the same conversations

- *"Where is the latency in a voice app?"* The first word is about 0.6 s and the first sound about 0.9 s in the parallel pipeline: the writer's first text is about 60% of it (544 ms), Azure rendering the first speech chunk about a third (295 ms). The safety judge (about 376 ms) runs alongside the writer and is hidden behind it, by 106 to 233 ms in every one of the five measured requests. Each speech chunk costs roughly 230 ms fixed plus about 1.4 ms per character, so cutting the first chunk shorter would save only about 55 ms and risk choppy intonation: not worth it.
- *"How do you know it is not just noise?"* Single runs swing by up to a second (one request took 2.93 s to first sound because Azure needed 1.1 s for a 20-character piece), so comparisons use medians, several requests per set-up in rotating order, and the sample size is stated next to every number.
- *"How did you make sure a quality comparison was fair?"* Comparing fresh scripts against old, hand-picked winners is biased (regression to the mean): an A/A check, today's prompt against its own earlier scripts, "lost" 4 to 11. The fair test generates both sides fresh in the same run, judged blind in both orders by a separate model.

**Re-run any of it.** No tokens needed: `python -m evals.bench_framework_overhead` (the 5× test), `python -m evals.profile_clients` (where the per-chunk time goes) and `python -m evals.bench_import_cost` (start-up time). With a Groq key set for the command (use the second account's): `python -m evals.probe_groq_timing` (network against Groq's own time) and `python -m evals.run_latency_check --speak --cases tiny-win,vague-professional,boxing-pep,technical-explainer,wedding-toast` (first word and first sound).
