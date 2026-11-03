# Architecture

Three tools, one request path each, one small stack: FastAPI, Groq (LLM + Whisper), Azure Neural TTS. This document explains the parts that are not obvious from reading a single file.

## 1. Speech Director — `/api/rewrite-and-speak/stream`

```
POST text + style
  │
  Groq stream ──► ScriptStream ──┬─► `token` events        what the reader sees (annotations stripped)
  (annotated script,             │
   plain text)                   └─► PieceCutter ──► piece ──► Azure TTS ─┐   up to TTS_POOL_SIZE in parallel
                                       │ (a sentence at a time,             ├─► `audio_segment` events, IN ORDER
                                       │  never inside a tag)         piece ─┘
                                       ▼
                       script finished ─► `script_done`  ·  all pieces finished ─► `audio_done` (+ concatenated MP3)  ·  `done`
```

The two halves run as separate tasks and talk to the single SSE response through a queue (`services/director.py`). Because a sentence is handed to the voice as soon as the model has written it, **the first words are heard early, typically one to two seconds after the request, regardless of script length** — `audio_segment` events routinely arrive before `script_done`.

**What the model writes.** One annotated script as plain text (`prompts/voxis.py`): paragraphs separated by blank lines, `<prosody>` / `<emphasis>` / `<break>` tags and `[bracketed director's notes]`. A refusal is exactly one line, `ERROR: <reason>`. There is no JSON: the old two-key format made the model write the script twice, and streaming a string out of partial JSON is fragile. The readable text is the script with annotations stripped, so shown words and spoken words cannot drift.

**`ScriptStream`** reads the model's output incrementally. It holds back the first ~8 characters to tell `ERROR:` from a script, drops code fences and `<think>` blocks, and produces two aligned deltas: the script (with annotations, for the voice) and the display text (stripped, with spaces collapsed lazily so the stream is append-only).

**`PieceCutter`** (`services/tts.py`) turns script text into pieces *incrementally*. A sentence ends at `. ! ? …` followed by whitespace — or at a closing tag that ends one — and only at tag depth 0, so a `<prosody>` span never straddles two pieces. An unfinished trailing tag (`<pros`, arriving in fragments) is held back until it is complete; a lone `<` in speech is kept as text. Piece sizes grow (≥40 spoken characters, then 220, then 380): the first is about one sentence so audio starts fast, later ones are bigger to keep prosody natural and Azure round-trips few. A paragraph break always ends a piece and opens the next with a 400 ms pause. Feeding the cutter one character at a time yields exactly the pieces of a single call (tested).

**`synthesize_pieces`** consumes any async iterator of pieces. It renders them in worker threads (bounded by a semaphore the size of the synthesizer pool), keeps them in script order however they finish, writes each MP3 to `audio_output/`, and finally concatenates them into one file for replay and download. Each piece gets one retry as plain text if Azure rejects the voice direction; a piece that still fails is skipped and the rest play.

**Voice direction** (`direct_tts_script`). Keeps `prosody` (pitch ±20 %, rate ±30 %, keyword values mapped to percentages), `break` (50–500 ms), `emphasis`, `p`, `s`; converts `[voice drops]`-style notes into prosody on the sentence they introduce (`[let this land]` → a 450 ms break after it); drops every other tag but keeps its text; repairs unbalanced tags; proves the fragment parses as XML; otherwise falls back to plain text. `POST /api/synthesize {"plain": true}` renders with no direction and no speaking style for the flat-read comparison.

**The browser** (`frontend/app.js`, `segPlayer`). Each `audio_segment` URL is fetched, decoded with `decodeAudioData` and scheduled back to back on one `AudioContext`, created inside the click so autoplay rules allow it; decoding is chained so pieces cannot play out of order. When `audio_done` arrives the `<audio>` element gets the concatenated file for scrubbing and download. The latency strip under the player shows what the user felt, measured in the browser from the click.

**Profiling concurrent work.** `profiler.trace()` keeps a per-request span stack in a contextvar, which is only valid for one flow of control. The model-reading task uses it; the voice pipeline runs concurrently and so reports `perf_counter` timestamps instead, recorded with `profiler.add_span(..., parent=…, depth=…)` under a "Speech Synthesis Pipeline" span. The Lab draws them on the shared timeline, overlapping the model's own span.

## 2. Communication Coach — `/api/transcribe` → `/api/coach/stream`

**Transcription** (`services/stt.py`): Groq Whisper (`whisper-large-v3`, primed with a disfluent prompt so it keeps the ums), then:

- two-tier fillers — `um`, `uh`, `you know`, … count anywhere; `so`, `like`, `right`, `okay`, `well` only when sentence-initial or set off by punctuation (Whisper punctuates, which is what makes this reliable). Character spans come back so the UI can highlight them in place;
- stutters (`the the`), long pauses (> 0.7 s between segments), the fastest and slowest stretch from segment timings.

**Coaching** (`services/coach.py`): the prompt gets a third input, `speech_metrics`, rendered as plain facts by `prompts/coach.py::describe_speech_metrics()`; the model must quote them, add a filler- or pace-specific action when they are out of band, and fill `delivery_notes`. The output is one JSON object, so raw tokens are not readable; the client uses them to light up each dimension as its score appears, while the server validates the final object with `CoachResponse` and retries a bad parse silently (`retry` event) — nothing user-visible is committed until `result`. `KeyMoment.technique` and `ModulationSuggestion.instruction` accept each other's name, because models sometimes swap the two look-alike fields and a retry costs a whole second call. Every run is stored (`coach_runs`) and `/api/coach/history` returns deltas.

**Output size is latency.** Coach time is dominated by its ~800 output tokens; the prompt's ceiling (850) is the knob.

## 3. Latency Lab — `/api/metrics`

Every request initialises a trace (`init_traces`) and ends by logging a *cycle* (the whole span tree) server-side. Spans carry `start_ms` offsets; the Lab sorts by start and draws a true timeline. `llm_metrics`, `tts_metrics` and `cycle_metrics` carry `user_key`; every reader is scoped to the caller and accepts `?hours=`. Percentiles are computed in Python so the SQL stays dialect-neutral. Bookkeeping writes happen concurrently and after the reader already has the result.

## 4. Prompt budget and Groq rate limits

Groq's free tier allows ~8,000 tokens per minute (prompt + completion; `max_tokens` is *not* charged up front — three 7,000-token requests in a row all succeeded). The old prompts were ~5,000 (Director) and ~3,200 (Coach) tokens, so one Director run left almost no budget for anything else. The current prompts are ~2,500 and ~1,600. There is also a limit of 200,000 tokens per model per day, refilling at about 8,300 an hour, or about 70 Director runs. The wait in Groq's 429 message comes from that refill rate.

If a call is rate-limited anyway, the groq SDK's default is to retry silently with backoff — up to ~25 s of dead air. `ChatGroq` is created with `max_retries=0`; `rate_limit_wait_seconds()` reads `retry-after` / `x-ratelimit-reset-tokens` from the 429, streams emit a `rate_limited {wait_s}` event (the UI counts down) and resume. The app's own per-user token budget (`rate_limiter.py`, epoch-based, fails open) is separate and larger.

## 5. Persistence

`services/database.py` runs the same async API on MySQL (pool, the default; boot fails if it is unreachable) or SQLite (only when `DB_BACKEND=sqlite` is set, for tests and local development; WAL mode, write lock). Rules: `%s` placeholders (rewritten to `?`), ISO-string timestamps, dialect branches only for upserts. Columns added after a table first shipped are applied by an idempotent `ALTER TABLE` migration at boot.

## 6. Startup

`init_db` → delete generated MP3s older than `AUDIO_RETENTION_HOURS` → open `TTS_POOL_SIZE` Azure connections (a brand-new connection costs ~0.4 s on its first sentence; an open one does not) → ask Groq whether `LLM_MODEL` still exists (`/api/health → llm`; the setup banner shows alternatives if not). The page repeats the Azure warm-up when you start typing.

## 7. Serving

The frontend mount and the API's JSON answers are gzipped — text compresses ~4×. MP3s are not, since they are already compressed and need Range support for seeking. App files are served with `Cache-Control: no-cache`: the browser keeps its copy but revalidates, and `StaticFiles`' ETag turns that into a 304 when nothing changed, so an edited file is never stale and an unchanged one is never re-downloaded. SSE responses live under `/api` and are never compressed (that would buffer events).

## 8. Known limits

- A free Groq key still allows only a few runs a minute; the rate-limit handling makes the wait visible rather than hiding it.
- Pieces are concatenated as raw MP3 frames. Browsers and players handle it; a strict decoder could complain about the join. Per-piece files remain on disk for the retention window.
- The audible quality of the voice direction has been judged by ear on a handful of scripts — tune the note→prosody table in `tts.py::_NOTE_RULES` after listening.
- Filler detection is lexical with positional rules; a classifier would catch "like" used as a filler without surrounding punctuation.
- The compact prompts were checked on a dozen inputs against the originals, not on a large evaluation set. If a style of input behaves oddly, the originals are in git history (`73d14fd`) for comparison.
