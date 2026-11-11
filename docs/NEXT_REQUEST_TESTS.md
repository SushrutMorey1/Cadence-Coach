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

## Answer to James (draft, for the owner to send)

A refusal ends the stream with an `error` event after one visible re-ask. Nothing arrives after it. A pytest regression test now runs a refusal, an abandoned request and a rate-limited request, each followed by a normal request, and checks the second request's first-token time, that no unannounced wait happens, and that no task from the first is still running. It runs against a fake model, so it covers our code, not Groq's side of the budget.
