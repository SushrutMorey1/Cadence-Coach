# 🎙️ Cadence Coach

### Say it better. Hear it performed. Know exactly where you lose people.

[![CI](https://img.shields.io/badge/CI-GitHub_Actions-2088FF?logo=githubactions&logoColor=white)](.github/workflows/ci.yml)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Groq](https://img.shields.io/badge/Groq-Qwen3_%2B_Whisper-F55036)](https://groq.com)
[![Azure Speech](https://img.shields.io/badge/Azure-Neural_TTS-0078D4?logo=microsoftazure&logoColor=white)](https://azure.microsoft.com/products/ai-services/text-to-speech)

Three tools for people who have to speak well, on one small stack: **Groq** (an LLM for writing and coaching, Whisper for hearing you) and **Azure Neural TTS** (for performing), with **MySQL** for storage and **Google sign-in** for accounts.

## What it does

| | Tool | What happens |
|---|---|---|
| 🎤 | **Speech Director (VOXIS)** | Paste rough text — or a one-line brief — and get a script written for the ear, streamed as it is written. Then hear it *performed*: VOXIS writes voice direction into the script (pauses, pitch drops, emphasis, bracketed notes) and the engine follows it. **The voice starts while the script is still being written**, typically one to two seconds after you press the button. *Compare: flat read* plays the same words with no direction so the difference is audible, not claimed. Style presets, copy/download, edit-and-re-speak, another take. |
| 🧠 | **Communication Coach** | Paste text, or record/upload audio. Six-dimension analysis (vocabulary, tone, modulation, pacing, emphasis, clarity) that **cites your measured delivery**: words per minute, filler words highlighted in the transcript, long pauses, your fastest stretch. Each dimension lights up as it is scored. Every run is stored so you see your trend. |
| 🔬 | **Latency Lab** | A true timeline of every request — the model's first token, each TTS piece, every parse — plus p50/p95 per endpoint, a time-range filter and CSV export. Your calls only. It exists so the other two tools' speed is measured, not asserted. |

## Why it is fast

Latency was the brief, so it is designed in rather than tuned on:

1. **One script, written once.** The model used to write the script twice (a copy to read and an annotated copy to speak) inside a JSON wrapper. It now writes only the annotated script as plain text; the readable version is that same text with the annotations stripped. Half the output tokens, no JSON to escape or parse, and the words shown can never differ from the words spoken.
2. **The voice does not wait for the script.** Model tokens are cut into sentences as they arrive (`PieceCutter`) and each piece goes to Azure immediately; pieces render in parallel and play back to back in the browser. The first piece is one sentence, so audio starts early however long the script is. Before, nothing played until the entire script was written *and* then synthesised in one call.
3. **Small prompts.** The Director prompt went from ~5,000 to ~2,500 tokens and the Coach from ~2,800 to ~1,600. Prompt tokens are paid on every call and count against Groq's free-tier limits. Size was not allowed to cost quality: see *Quality*.
4. **No cold starts.** Azure connections are opened at boot and when you start typing; the HTTP and TLS handshakes are paid before you press the button.
5. **A light page.** Static files are gzipped (~4× smaller) and revalidated with an ETag instead of re-downloaded.

### Measured

Server-side timings with real keys, against Groq (`qwen/qwen3.8-27b`) and Azure Central India, `TTS_POOL_SIZE=3`. The browser adds a little on top.

Original version vs the streaming rewrite, 1 Nov 2026 (short note: average of 3 runs; long speech and Coach: 1 run each):

| | Original | Streaming rewrite |
|---|---|---|
| Short note: first audio | 2.85 s | **1.54 s** |
| Short note: all audio | 2.85 s | 2.16 s |
| ~90 s speech: first audio | 4.81 s | **1.89 s** |
| ~90 s speech: all audio | 4.81 s | 3.22 s |
| Coach analysis complete | 8.9 s (may include silent SDK retries) | 2.7 s |
| Server boot, MySQL running | 7.96 s | 7.68 s |
| Page weight on the wire | 125 KB | 37 KB |

The current prompt against the compact prompt it replaced, 4 Nov 2026, alternating on a short note:

| | Compact prompt | Current prompt |
|---|---|---|
| First audio, no rate-limit waits | 1.7–2.8 s (2 runs) | 0.9 s (3 runs) |
| Prompt size, as reported by Groq | 1,834 tokens | 2,492 tokens |

That is too few runs to call the current prompt faster, since a longer prompt should not speed the model up. It shows no sign of a slowdown. Runs that hit a rate limit took longer, up to 5.4 s to first audio.

Every performance shows its own numbers under the player, and the Lab keeps the full span timeline.

## Quality

The Director prompt is checked with a blind test (`evals/`): 20 cases, two prompts side by side in random order with audio, judged by ear without knowing which is which. The result below is one listener, so it is evidence and not proof.

| Blind verdicts against the original ~5,000-token prompt | Prompt wins | Original wins | Same |
|---|---|---|---|
| First compact prompt (2 Nov 2026) | 5 | 7 | 5 |
| Current prompt, after the fixes below (3–4 Nov 2026) | **11** | 2 | 3 |

What the first round found, and what changed:

| Measured on the current prompt's scripts | First compact prompt | Current prompt | Original |
|---|---|---|---|
| Words under speed or pitch tags | 56% | ~12% | 34% |
| Added pauses per 100 words | 5.0 | ~1 | 5.5 |
| Sentences of 4 words or fewer | 24% | ~15% | 8% |

In the final round the versions the listener rejected had more than twice the speed and pitch swing of the ones they chose, and 4 of 13 ran over 180 words a minute against 1 of 13. All three should-reject inputs (gibberish, symbols as a style, a hate request) were rejected. The original prompt still won `comic-food-duel`, the comic speech case, in both rounds.

The voice was retuned at the same time, by measurement:

| | Before | Now |
|---|---|---|
| Typical pause | ~1.2 s | ~0.75 s |
| Deepest volume dip in a 2 s window | -9.3 dB | -4.3 dB |

## Quickstart

```bash
git clone -b mod-fixes https://github.com/SushrutMorey1/Cadence-Coach.git && cd Cadence-Coach
python -m venv .venv && .venv/Scripts/activate      # or: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                                  # add the Groq, Azure and Google keys and your MySQL details
python run.py                                         # http://localhost:8000
```

The app needs a running MySQL server and Google OAuth credentials, and it refuses to start without them rather than silently degrading. For local development without either, set `DB_BACKEND=sqlite` and `AUTH_REQUIRED=false`; never do that in production. `GET /api/health` shows what is configured and whether Groq still offers the model you chose (they retire models without notice).

### Configuration (`.env`)

| Variable | Default | Notes |
|---|---|---|
| `GROQ_API_KEY` | — | Required. LLM + Whisper. |
| `AZURE_SPEECH_KEY` / `AZURE_SPEECH_REGION` | — / `centralindia` | Required for speech. |
| `LLM_MODEL` | `qwen/qwen3.8-27b` | Any Groq chat model. Check `/api/health → llm.available`. |
| `WHISPER_MODEL` | `whisper-large-v3` | `-turbo` is faster but hears fewer fillers. |
| `TTS_POOL_SIZE` | `3` | Pre-warmed synthesizers = how many pieces render in parallel. |
| `TTS_SENTENCE_GAP_MS` | `700` | Pause between sentences. `off` restores Azure's default of about 0.9 s and keeps every break the model wrote. |
| `AUDIO_RETENTION_HOURS` | `24` | Generated MP3s older than this are deleted at boot. |
| `DB_BACKEND` | `mysql` | No silent fallback: boot fails if MySQL is unreachable. `sqlite` is for tests and local development. |
| `MYSQL_HOST` / `_PORT` / `_USER` / `_PASSWORD` / `_DATABASE` | `localhost` / `3306` / `root` / — / `cadence_coach` | Your MySQL server. |
| `GOOGLE_CLIENT_ID` / `_SECRET` | — | Required. The signed-in email is the rate-limit and history key. |
| `AUTH_REQUIRED` | `true` | `false` opens guest mode, for local development only. |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Read by uvicorn: the proxies whose `X-Forwarded-For` is trusted. Never `*`. |

Keep comments on their own line in `.env`: a line like `KEY=   # note` makes python-dotenv read the comment as the value. The app detects and ignores those, with a warning naming the key.

### Docker

```bash
docker compose up                         # MySQL + the app; the app waits until MySQL is healthy
```

On Render, `render.yaml` expects a MySQL server hosted elsewhere, because Render's managed databases are PostgreSQL. Put its details in the `MYSQL_*` variables.

## API

Interactive docs at `/docs`. Everything except `/api/health` and the `/api/auth/*` sign-in routes needs a signed-in Google account, otherwise it returns 401.

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/api/rewrite/stream` | Director, script only (SSE) |
| `POST` | `/api/rewrite-and-speak/stream` | Director, script + the voice performing it as it is written (SSE) |
| `POST` | `/api/synthesize` | Speak any text through the same pipeline; `"plain": true` for a flat read |
| `POST` | `/api/warm` | Open the Azure connections ahead of use; runs at most once every 30 s |
| `GET` | `/api/voices` | Azure voices |
| `POST` | `/api/transcribe` | Whisper + delivery metrics (fillers with positions, pauses, fastest stretch) |
| `POST` | `/api/coach/stream` | Coach (SSE); `speech_metrics` grounds the advice in a recording |
| `GET` | `/api/coach/history` | Your past analyses with deltas |
| `GET` | `/api/metrics?hours=` · `/api/metrics/export?format=csv` | Latency Lab |
| `GET` | `/api/health` | Version, DB backend, integrations, model availability |

**SSE events** (`data: {json}` lines):

```
rewrite   token · rate_limited · retry · audio_segment {index,url,duration,directed} ·
          script_done {display_script,tts_script} · audio_done · done · error
coach     token · rate_limited · retry · result · done · error
```

`audio_segment` events can arrive *before* `script_done` — that is the point.

## Testing

```bash
pytest -v          # no network, no keys, isolated SQLite (tests/conftest.py)
```

`test_director.py` covers the voice-direction sanitiser, the stream parser (any chunking, fences, `ERROR:` lines), the incremental piece cutter (including tags split across chunks), the parallel pipeline against a fake Azure (ordering, failures, plain-text retry), and the key property: **speech starts before the script is finished**. `test_coach.py` covers filler/pause measurement, the streamed-and-retried analysis and history. `test_lab.py` covers the profiler timeline, per-user metrics, export and static serving.

The prompt's quality is tested separately against the real model, because no unit test can judge writing:

```bash
python -m evals.run_director_eval --audio                         # blind A/B report, ~145k Groq tokens
python -m evals.run_director_eval --audio --reuse-old evals/out/<run>   # reuse the old side, ~60k tokens
python -m evals.run_director_eval --resume evals/out/<run>        # continue after hitting Groq's limit
python -m evals.run_director_eval --revoice evals/out/<run>       # re-render audio only, no Groq tokens
```

The report goes to `evals/out/<run>/report.html`: pick A, B or same for each case, then press *Copy verdicts*.

## Project structure

```
app/
├── main.py                 FastAPI app, middleware, sign-in, routers, health
├── config.py               Every env var in one place
├── prompts/voxis.py        The Speech Director prompt (annotated script out, plain text)
├── prompts/coach.py        The Coach prompt + describe_speech_metrics()
├── routes/                 stream (SSE) · synthesize · coach (history) · transcribe · voices · metrics
├── services/
│   ├── director.py         Model → script → voice, pipelined (ScriptStream + stream_rewrite)
│   ├── coach.py            The Coach stream
│   ├── llm.py              Groq client, rate limits, model-availability check, SSE helpers
│   ├── tts.py              Voice direction, synthesizer pool, PieceCutter, parallel pipeline
│   ├── stt.py              Whisper + fillers (two-tier, with positions), pauses, pace stretches
│   ├── coach_runs.py       Coach history
│   ├── metrics.py          Per-user metrics, p50/p95, export
│   ├── profiler.py         Nested spans with start offsets; add_span() for concurrent work
│   ├── database.py         MySQL pool (SQLite for tests and local development) behind one async API
│   └── rate_limiter.py, user_service.py
└── utils/                  parsing.py (LLM JSON), retry.py
frontend/
├── index.html · style.css  The three tabs
├── app.js                  Director · Coach · Lab (ES module)
└── js/main.js · ui.js · api.js   Shell: auth, badge, tabs, shared helpers
tests/                      conftest + helpers, test_director, test_coach, test_lab, test_config, test_unit, test_basics
evals/                      The blind quality test: director_cases.json, run_director_eval.py
```

## Decisions worth knowing

- **Plain-text output, not JSON, from the Director.** JSON made the model write the script twice and made streaming fragile (escape sequences, partial strings). Plain text streams trivially, the display text is derived by stripping annotations, and the failure mode "model returned broken JSON" disappears. A refusal is a single `ERROR: …` line.
- **The compact Director prompt lost to the original at first, and was fixed on evidence.** A blind listening test found the first version wrote choppier sentences and tagged too much of each script. "Less is more" guidance, sentence rhythm and creative-writing guidance went back in, about 650 tokens in all. See *Quality*.
- **The model is not trusted with facts it was not given.** When the prompt asked it to name real dates, it wrote October 22 and then October 26 for Gandhi Jayanti (it is October 2). The prompt now forbids dates it is not certain of, so such speeches are safe but more general.
- **Volume words are relative.** Azure's `soft` measured -7.7 dB, a jarring dip mid-sentence, and its `loud` measured -1.8 dB, quieter than no tag. They now map to -15% and +15%.
- **Input with no letters is rejected in code,** before any tokens are spent.
- **Scripts are in English** whatever the draft's language, unless the user asks for another language.
- **Parallel work is timed outside the profiler.** Worker threads and concurrent tasks cannot share the profiler's span stack, so each piece reports its timings and they are recorded with explicit parents (`profiler.add_span`).
- **Groq retires models.** `llama-3.3-70b-versatile` vanished in Oct 2026 and every feature returned 404. The default is now `qwen/qwen3.8-27b`; `/api/health` checks availability at boot and the page shows a banner if it is gone.
- **Rate limits are visible.** The groq SDK retries a 429 silently (~25 s of dead air). The client is created with `max_retries=0`; streams emit a `rate_limited` event with the wait and resume by themselves.
- **MySQL and Google sign-in are required.** Users, history, metrics and the rate-limit ledger must live in one persistent database, and the signed-in email is the rate-limit key, so no request falls back to an IP address a client controls.

## Known limitations

- **Hindi has no sound yet.** Asking for a script in Hindi produces correct Hindi text, but the US English voices return silence and the app still reports success. Azure's `hi-IN` voices read it correctly in testing, but they are not wired in.
- **Groq's free tier is shared by everyone using the key.** It allows 200,000 tokens per model, refilling at about 8,300 an hour. That is about 70 Director runs a day, and the Coach draws on the same budget.
- **A Groq output limit is not investigated.** One run returned `output tokens per minute: Limit 1000, Requested 2500`, then succeeded on retry. It may matter with several users at once.
- **Comic writing:** the original prompt still won the comic case in the blind test.
- **Tone clashes:** a grave draft in a hype style is usually refused on the first try and rescued by the automatic retry, and the script can mention the clash ("We do not hype a funeral").
- **"Not just X, it is Y":** the prompt forbids it, but the model still uses it, at about 0.6 times per 100 words.
- **Pace:** the voice averages about 164 words a minute, a little above the 130–160 the Coach recommends, and some scripts run over 180.

## License

Educational and portfolio use.
