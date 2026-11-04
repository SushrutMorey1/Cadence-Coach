# Demo playbook

Show the three tools in four minutes, survive a bad network, answer what follows.

## Before you start

- `python run.py` running; open `http://localhost:8000` in Chrome; headphones or a decent speaker.
- MySQL running, and in `.env`: `GROQ_API_KEY`, `AZURE_SPEECH_KEY`, the Google OAuth keys and the `MYSQL_*` details. The app will not start without MySQL and Google.
- **Sign in with Google before you present.** The header badge says **Online** and no setup banner is showing (the banner appears if a key is missing or Groq no longer offers the model).
- Let the server finish booting before the first click: it warms the Groq client and the Azure connections in the first couple of seconds.
- **Groq free tier = 8,000 tokens/min.** A Director run now uses ~2,800, so leave about 30 s between runs. If you do hit the limit the app counts the wait down and resumes by itself — but don't make a judge watch it.
- **Groq free tier also has a daily cap: 200,000 tokens per model, about 70 Director runs.** It refills at about 8,300 tokens an hour, and everyone using the key shares it. Every rehearsal spends from it, so stop rehearsing at least 2 hours before presenting, and don't let judges use the same key unless you have checked the budget. For judging day, consider a paid Groq key.
- **Stay in English for the demo.** A script requested in Hindi has silent audio.
- Have one 30–60 s recording of yourself ready (any phone voice memo works) for the Coach.

## The script

**1. Speech Director (90 s)**
Click the **Calm CEO** preset. Paste: *"we are launching a new product next week. it will help people save time and money. our team has worked really hard on this."* Click **Rewrite & speak**.
- The voice starts **a second or two after you click**, while the script is still appearing. Say so: "it is already talking — the first sentence went to the voice the moment the model wrote it."
- Point at the strip under the player: *first word · script done · all audio*, and read out the real number on screen. "Nothing waits for the whole script, and nothing waits for the whole audio."
- Point at the **Voice-directed · N cues** pill: "those are pauses and pitch changes the model wrote into the script; the engine performs them." Click **Compare: flat read** and play 5 seconds of each.
- Open **Voice direction** under the script to show the annotated text.

**2. Communication Coach (90 s)**
Switch to **Record / Upload Audio**, upload the voice memo, **Transcribe & Analyze**.
- Speaking metrics appear first: pace, fillers highlighted in the transcript, pauses, your fastest stretch. "Measured from the audio, not guessed."
- The six dimensions light up one by one as the coach scores them. Read the **delivery notes** aloud — they quote your numbers.
- Scroll to **Your trend**: every past analysis with the delta. "It only matters if you come back."

**3. Latency Lab (45 s)**
- Top cards: p50 and p95 for the LLM. "Every call, your calls only."
- **Deep latency inspector**: open the last *Rewrite & Speak* cycle. The model's span and the speech pipeline overlap on the timeline, with each TTS piece underneath. "That overlap is why the first word is fast."

## Numbers you can quote

All measured. Say "in our tests", because these come from a small number of runs on one machine.

| Claim | Data |
|---|---|
| First audio, short note | 2.85 s in the original → 1.54 s (3 runs each); 0.9 s in the latest clean runs (3 runs, rate-limit-free) |
| First audio, ~90 s speech | 4.81 s → 1.89 s (1 run each) |
| Prompt size | ~5,000 → 2,492 tokens |
| Page weight | 125 KB → 37 KB |
| Blind listening test against the original prompt | 11 wins, 2 losses, 3 ties (16 cases, one listener) |
| Overdirected voice | 34% of words under speed or pitch tags → ~12% |
| Volume dips | -9.3 dB → -4.3 dB at worst |
| Typical pause | ~1.2 s → ~0.75 s |
| Automated tests | 190 passing, offline, with no keys needed |
| Free-tier capacity | About 70 Director runs a day, against under 40 with the original prompt |

Don't claim "under a second", "every rule kept" or "always faster". Rate limits can push first audio to about 5 s.

## Fallbacks

| Problem | Do this |
|---|---|
| Countdown appears ("per-minute token budget") | Wait it out (or switch to the Coach while it counts). Mention the free-tier limit honestly. |
| Setup banner says the model is unavailable | `/api/health → llm.alternatives`, set `LLM_MODEL` in `.env`, restart. Groq retires models. |
| Audio does not start | Click the player under the strip — the full file is there. Check the browser allowed audio. |
| No microphone / recording fails | Upload the prepared voice memo instead. |
| Whisper returns no text | Use **Paste Text** mode with a pasted paragraph; the Coach still works without audio (it says so). |
| Network dies | Lab still shows stored cycles; Coach history still loads. |
| "Daily token allowance is used up" | Set `LLM_MODEL` to another Groq model in `.env` and restart. The allowance is per model. |
| App will not start | MySQL is down, or the Google keys are missing. The error names which. |

## Questions you will get

**Why not one of the many voice-agent products?** This is not a voice agent. It makes what you are about to say better and tells you, with numbers, how you actually said it.

**What is yours vs the model's?** The prompts (how a script is written for the ear, how delivery is coached), the voice-direction engine (so the performance is directed, not read), the pipeline that speaks while the model is still writing, the measurement (fillers, pauses, pace) and the profiling. The LLM and the voices are rented.

**Why Groq and Azure?** Fastest tokens per second for the writing; the most controllable neural voices (SSML) for the performance.

**Did the smaller prompt hurt quality?** At first, yes: the first version lost a blind listening test 5–7. We measured why, fixed it, and the current one won 11–2 with 3 ties. It is one listener and 16 cases, but it is tested, not assumed.

**How fast?** Typically one to two seconds to the first word, for a short note or a long speech, because speech starts when the first sentence exists. Measured in the Lab and shown under every performance.

**What breaks?** A free Groq key has per-minute and daily token budgets (the app shows a countdown for the per-minute one). Groq retires models without notice (the app checks at boot). Azure needs a key per region. Hindi scripts have no sound yet.
