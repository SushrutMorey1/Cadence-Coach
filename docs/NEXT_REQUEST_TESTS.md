# Does a bad request hurt the next one? (step 1 of the safety plan)

Test file: `tests/test_next_request.py` (6 tests, offline, no Groq tokens).

## Why

James Zammit (Roark) asked two things:

1. Is a refusal a terminal stream event?
2. Is there a regression test showing the next request stays healthy afterwards?

Background: in October a refused war prompt was followed by a request whose first token took 8–15 s. The cause was ChatGroq's two silent retries. Those are now off (`max_retries=0`), rate limits show up as a visible `rate_limited` event, and a refusal ends in an `error` event after one visible re-ask.

## What the tests do

Each test sends one "bad" request and then one normal request to the same fake model, and checks the normal one.

| Bad request | What is checked about it | What is checked about the next request |
|---|---|---|
| Refusal | One `retry`, then one `error`; nothing else arrives | Gets the plain prompt (not the re-ask note) |
| Reader leaves halfway (rewrite-only and rewrite-and-speak) | The model stops being read; no task keeps running | Healthy |
| Groq per-minute rate limit | One `rate_limited` event and one matching wait | Healthy |
| Reader leaves during the rate-limit countdown | No task keeps running | Healthy |

"Healthy" means:
- the first token arrives in under 1 s (the fake model answers instantly, so anything slower is an inherited wait);
- no sleep of 0.5 s or more that was not announced by a `rate_limited` event;
- no task from the earlier request is still running;
- the events match the same request run on a fresh server (apart from timings).

## Checked that the tests can fail

- Adding a silent 3 s sleep after the refusal re-ask makes the rate-limit test fail.
- Removing `task.cancel()` in `stream_rewrite` makes both abandoned-request tests fail. (The first version of the tests missed this; the "model stops being read" check was added for it.)

## What this proves and what it does not

Proven (measured): the app code carries no state, task or wait from one request into the next.

Not proven:
- The old 8–15 s delay came from the SDK's silent retries. `ChatGroq` is faked here, so nothing in these tests checks that `max_retries=0` is set. A small separate test could.
- A refusal still costs a full prompt's tokens, and Groq's per-minute budget carries that over. That is Groq's side, not the app's. Step 3 (refuse harmful input in code, zero tokens) addresses it.
- These tests are not part of the in-app Latency Lab; they are ordinary pytest tests.

## Everything related to James (updated 16 Nov 2026)

**Who and what.** James Zammit, co-founder of Roark (voice AI simulations and evals), asked on LinkedIn: (1) is a refusal (a guardrail rejection) a terminal stream event, and (2) is there a Latency Lab regression test showing that the next request stays healthy afterwards? The owner replied "not yet" and promised to ping him back once it was in.

**The incident behind it.** Around 16 to 18 Oct 2026 a war prompt was refused, and the next request's first token took 8 to 15 s (read from `cycle_metrics`). ChatGroq was on the SDK default of 2 silent retries. Silent retries are now off (`max_retries=0`), a Groq rate limit is a visible `rate_limited` event with a countdown, and a refusal ends in an `error` event.

**Answers, as they stand today (all measured or tested unless marked)**

1. *Is a refusal a terminal stream event? Yes.* With today's prompt the stream sends one visible `retry` event (a second look at the request), then, if the model still refuses, a single `error` event, and nothing else arrives (`tests/test_next_request.py`). With the new safety judge (built, off by default) the judge decides first, so a refusal is one `error` event straight away, with a message that says why (harm, tone, or not enough to write from); the writer is cancelled and nothing is written or spoken (`tests/test_judge_pipeline.py`, `tests/test_parallel_judge.py`).
2. *Is there a regression test that the next request stays healthy? Yes, in pytest (commit `3400b25`), not in the in-app Latency Lab.* A refusal, a request abandoned halfway and a rate-limited request are each followed by a normal request, and the second request must reach its first token quickly, wait silently for nothing, leave no task running from the first, and match the same request on a fresh server. Two deliberate bugs (a silent wait, a task left running) were each caught. It runs against a fake model, so it covers our code, not Groq's token budget.

**What came out of it** (the work behind the second question)
- Measured on borderline safe requests, today's single prompt refused 5 of 6 on its first call and only recovered through a visible re-ask at about double the tokens; rewording its refusal rules did not help; the same model wrote all 6 when refusing was not offered.
- A short separate judge call decides much better: on our labelled sets (author-labelled, no real traffic yet) 1 wrong refusal in 110 safe cases and 0 missed harm in 52 harmful ones, at about 640 to 740 tokens and a median 0.35 s a call.
- It is built into the app with the judge and the writer running in parallel (the writer starts at once and its output is held back until the verdict; a refused request has its writer cancelled), with offline tests that check the overlap and the hold-back on timestamps. It has been ON by default since 18 Nov 2026 (`SAFETY_JUDGE=off` rolls back).
- Blind quality check by an independent evaluator (Claude Fable) on the on-screen text, both prompts generated fresh: the new pipeline was preferred 10 to 6 on 16 random cases, within chance, so at least as good. An A/A check (today's prompt against its own reused scripts) showed that comparing against reused, hand-picked scripts biases such tests.
- One live latency round (4 written requests and 1 refusal, three set-ups taking turns): median first word today 0.58 s, judge then writer 0.82 s, judge and writer in parallel 0.68 s; a refusal arrived in 0.41 s (parallel) against 1.00 s for today's prompt and its re-ask. Too few requests to rank series against parallel.

**What is accurate to say, and what is not yet**
- Accurate: the regression test is in; a refusal is a terminal `error` event; the judge is built, tested offline and measured on labelled sets and in one live latency round.
- Not yet true: that the judge is live (it was switched on by default on 18 Nov 2026, but nothing is deployed or measured on real traffic yet), anything about real traffic, a latency ranking of series against parallel, and that the in-app Latency Lab contains the test (it is a pytest suite).

**Draft reply for the owner to adapt** (short; the owner sends it)

> Thanks for pushing on this, James. Short answers: yes, a refusal is a terminal event: the stream ends in a single `error` event and nothing arrives after it. And there's now a regression test for the part you asked about: a refusal, a request abandoned halfway and a rate-limited request are each followed by a normal one, and I check the second one's first-token time, that nothing waits silently, and that no task from the first is still running. It's a pytest suite rather than part of the Latency Lab, and it runs against a fake model, so it covers my code rather than Groq's budget.
>
> Chasing it showed something else: my single prompt was refusing about 5 in 6 borderline-but-safe requests on its first call and only recovering through a retry at roughly double the tokens. I've built a small separate judge call in front of the writer instead (they run in parallel, and a refusal cancels the writer). On my own labelled sets it makes 1 wrong refusal in 110 safe cases and misses 0 of 52 harmful ones, and in a blind comparison the writing came out at least as good. It's built and tested but not live yet; real-traffic numbers are the next step.

**Where the rest lives:** the full safety-eval record is `docs/SAFETY_EVAL.md` (the status section first), the tests are `tests/test_next_request.py`, `tests/test_judge_pipeline.py` and `tests/test_parallel_judge.py`, and the commits are `3400b25` (regression tests) and `3fdb04d` onwards (the judge pipeline).
