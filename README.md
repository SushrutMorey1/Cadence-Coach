# 🎙️ Cadence Coach

### Say it better. Hear it performed. Know exactly where you lose people.

[![CI](https://img.shields.io/badge/CI-GitHub_Actions-2088FF?logo=githubactions&logoColor=white)](.github/workflows/ci.yml)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Groq](https://img.shields.io/badge/Groq-Qwen3_%2B_Whisper-F55036)](https://groq.com)
[![Azure Speech](https://img.shields.io/badge/Azure-Neural_TTS-0078D4?logo=microsoftazure&logoColor=white)](https://azure.microsoft.com/products/ai-services/text-to-speech)

Three tools for people who have to speak well, on one small stack: **Groq** (an LLM for writing and coaching, Whisper for hearing you) and **Azure Neural TTS** (for performing). Two API keys. Nothing else to run.

## What it does

| | Tool | What happens |
|---|---|---|
| 🎤 | **Speech Director (VOXIS)** | Paste rough text — or a one-line brief — and get a script written for the ear, streamed as it is written. Then hear it *performed*: VOXIS writes voice direction into the script (pauses, pitch drops, emphasis, bracketed notes) and the engine follows it. **The voice starts while the script is still being written** — about a second after you press the button. *Compare: flat read* plays the same words with no direction so the difference is audible, not claimed. Style presets, copy/download, edit-and-re-speak, another take. |
| 🧠 | **Communication Coach** | Paste text, or record/upload audio. Six-dimension analysis (vocabulary, tone, modulation, pacing, emphasis, clarity) that **cites your measured delivery**: words per minute, filler words highlighted in the transcript, long pauses, your fastest stretch. Each dimension lights up as it is scored. Every run is stored so you see your trend. |
| 🔬 | **Latency Lab** | A true timeline of every request — the model's first token, each TTS piece, every parse — plus p50/p95 per endpoint, a time-range filter and CSV export. Your calls only. It exists so the other two tools' speed is measured, not asserted. |

## Why it is fast

Latency was the brief, so it is designed in rather than tuned on:

1. **One script, written once.** The model used to write the script twice (a copy to read and an annotated copy to speak) inside a JSON wrapper. It now writes only the annotated script as plain text; the readable version is that same text with the annotations stripped. Half the output tokens, no JSON to escape or parse, and the words shown can never differ from the words spoken.
2. **The voice does not wait for the script.** Model tokens are cut into sentences as they arrive (`PieceCutter`) and each piece goes to Azure immediately; pieces render in parallel and play back to back in the browser. The first piece is one sentence, so audio starts about a second in — however long the script is. Before, nothing played until the entire script was written *and* then synthesised in one call.
3. **Small prompts.** The Director prompt went from ~5,000 to ~1,700 tokens and the Coach from ~3,200 to ~1,600, with every rule kept (checked against the originals on the real model — see *Decisions*). Prompt tokens are paid on every call: they cost time to first token, and on Groq's free tier (8,000 tokens/minute) the old prompts allowed about one Director run a minute.
4. **No cold starts.** Azure connections are opened at boot and when you start typing; the HTTP and TLS handshakes are paid before you press the button.
5. **A light page.** Static files are gzipped (~4× smaller) and revalidated with an ETag instead of re-downloaded.

### Measured

31 Oct 2026, Bengaluru → Groq (`qwen/qwen3.8-27b`) and Azure Central India, real keys, `TTS_POOL_SIZE=3`. Time from pressing *Rewrite & speak*:

| | Before | Now |
|---|---|---|
| First word of a short note | 2.95 s | **~1.0 s** |
| All audio of a short note | 3.1 s | ~1.5 s |
| Two-minute speech: first word | not played until fully written | ~1.0 s (all 78 s of speech ready at 2.2 s) |
| First click after server boot | 3.1 s | 0.9 s |
| Server boot | ~10 s | ~4.3 s |
| Database init | 4.2 s | 0.3 s |
| Page weight | 156 KB | 37 KB (304 revalidation in ~8 ms) |
| Director / Coach prompt | ~5,000 / ~3,200 tokens | ~1,700 / ~1,600 tokens |

Azure connections are opened at boot and again when you start typing; a warmed first sentence took ~0.2 s against ~0.7 s cold.

Every performance shows its own numbers under the player, and the Lab keeps the full span timeline.

## Quickstart

```bash
git clone https://github.com/codepd111-stack/Cadence-Coach.git && cd Cadence-Coach
python -m venv .venv && .venv/Scripts/activate      # or: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                                  # add GROQ_API_KEY and AZURE_SPEECH_KEY
python run.py                                         # http://localhost:8000
```

That is everything. The database falls back to SQLite when MySQL is not running, and the app runs in guest mode when Google OAuth is not configured. `GET /api/health` shows what is configured and whether Groq still offers the model you chose (they retire models without notice).

### Configuration (`.env`)

| Variable | Default | Notes |
|---|---|---|
| `GROQ_API_KEY` | — | Required. LLM + Whisper. |
| `AZURE_SPEECH_KEY` / `AZURE_SPEECH_REGION` | — / `centralindia` | Required for speech. |
| `LLM_MODEL` | `qwen/qwen3.8-27b` | Any Groq chat model. Check `/api/health → llm.available`. |
| `WHISPER_MODEL` | `whisper-large-v3` | `-turbo` is faster but hears fewer fillers. |
| `TTS_POOL_SIZE` | `3` | Pre-warmed synthesizers = how many pieces render in parallel. |
| `AUDIO_RETENTION_HOURS` | `24` | Generated MP3s older than this are deleted at boot. |
| `DB_BACKEND` | `auto` | `auto` tries MySQL then SQLite; `sqlite` for zero-infra. |
| `GOOGLE_CLIENT_ID` / `_SECRET` | — | Optional. Without them: guest mode. |

Keep comments on their own line in `.env`: a line like `KEY=   # note` makes python-dotenv read the comment as the value. The app detects and ignores those, with a warning naming the key.

### Docker

```bash
docker compose up backend                 # SQLite, zero infra
docker compose --profile mysql up         # MySQL-backed
```

## API

Interactive docs at `/docs`.

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/api/rewrite/stream` | Director, script only (SSE) |
| `POST` | `/api/rewrite-and-speak/stream` | Director, script + the voice performing it as it is written (SSE) |
| `POST` | `/api/synthesize` | Speak any text through the same pipeline; `"plain": true` for a flat read |
| `POST` | `/api/warm` | Open the Azure connections ahead of use |
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

## Project structure

```
app/
├── main.py                 FastAPI app, middleware, OAuth/guest auth, routers, health
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
│   ├── database.py         MySQL pool / SQLite fallback behind one async API
│   └── rate_limiter.py, user_service.py
└── utils/                  parsing.py (LLM JSON), retry.py
frontend/
├── index.html · style.css  The three tabs
├── app.js                  Director · Coach · Lab (ES module)
└── js/main.js · ui.js · api.js   Shell: auth, badge, tabs, shared helpers
tests/                      conftest + helpers, test_director, test_coach, test_lab, test_config, test_unit, test_basics
```

## Decisions worth knowing

- **Plain-text output, not JSON, from the Director.** JSON made the model write the script twice and made streaming fragile (escape sequences, partial strings). Plain text streams trivially, the display text is derived by stripping annotations, and the failure mode "model returned broken JSON" disappears. A refusal is a single `ERROR: …` line.
- **Compact prompts were A/B-tested, not assumed.** Both prompts were run against the previous ones on the real model. That caught three problems that were then fixed: a thin one-line input being rejected as "too vague" (now an explicit tie-breaker plus examples), short drafts coming back flat (the author's "a monotone rewrite is a failed rewrite" line restored), and the model swapping two look-alike Coach field names (explicit two-entry examples, and the reader accepts either name rather than paying for a retry). The original prompts are in git history (`73d14fd`).
- **Parallel work is timed outside the profiler.** Worker threads and concurrent tasks cannot share the profiler's span stack, so each piece reports its timings and they are recorded with explicit parents (`profiler.add_span`).
- **Groq retires models.** `llama-3.3-70b-versatile` vanished in Oct 2026 and every feature returned 404. The default is now `qwen/qwen3.8-27b`; `/api/health` checks availability at boot and the page shows a banner if it is gone.
- **Rate limits are visible.** The groq SDK retries a 429 silently (~25 s of dead air). The client is created with `max_retries=0`; streams emit a `rate_limited` event with the wait and resume by themselves.
- **SQLite + guest mode are fallbacks, not the target.** Set `DB_BACKEND=mysql` and Google credentials for the original production setup.

## License

Educational and portfolio use.
