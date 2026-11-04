"""
run_director_eval.py — Blind quality comparison for the Speech Director prompt
=================================================================================
Runs every case in director_cases.json through two versions of the VOXIS prompt
and writes a side-by-side HTML report with the sides shuffled, so you judge the
writing without knowing which prompt wrote it. The answer key is at the bottom.

  new  the prompt in app/prompts/voxis.py, through the app's real pipeline
       (services/director.stream_rewrite: refusal retry, rate-limit waits)
  old  the prompt from another git ref (default: working-demo), which answers
       in JSON; its display_script and tts_script are compared the same way

Each case also gets automatic checks (error vs script as expected, numbers and
names kept, length, unclosed tags, a script that stops mid-sentence). These only
flag obvious breakage; the quality judgement is yours. Tick A, B or "same" in the
report and press "Copy verdicts" to hand your judgement back as text.

Usage (from the project root, with real keys in .env):
  python -m evals.run_director_eval                 # all cases, old vs new
  python -m evals.run_director_eval --audio         # also voice both scripts
  python -m evals.run_director_eval --limit 10      # run 10 cases now, the rest later
  python -m evals.run_director_eval --resume evals/out/<run>   # continue a run
  python -m evals.run_director_eval --only comic-food-duel,tiny-draft
  python -m evals.run_director_eval --no-old        # new prompt only
  python -m evals.run_director_eval --audio --reuse-old evals/out/<run>
                                                    # reuse the old side of an earlier run
  python -m evals.run_director_eval --revoice evals/out/<run>
                                                    # re-render a run's audio, no Groq tokens

A resumed run keeps the settings it was started with. Reused audio is only a fair
comparison while the voice code is unchanged since that run; if it has changed,
re-render with --revoice so both sides are spoken the same way.

Cases marked new_only are rule checks on the current prompt, shown after the blind
comparison. summary.txt holds each prompt's writing shape and the refusal count.

Cost: Groq's free tier allows ~8,000 tokens per minute and 200,000 per rolling 24
hours per model: tokens come back 24 hours after they were spent, whatever wait
its error message suggests. A full run takes ~145k tokens because the old prompt alone is ~5k a call,
most of a day's allowance; with --reuse-old it takes ~60k. If the daily allowance
runs out, the run stops cleanly and prints the --resume command. The report is
rewritten after every case, so a partial run can already be judged.
"""

import argparse
import asyncio
import html
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASES = Path(__file__).with_name("director_cases.json")

# The eval writes metrics as the app does; keep them out of the real database.
os.environ.setdefault("DB_BACKEND", "sqlite")
os.environ.setdefault("SQLITE_PATH", str(Path(tempfile.gettempdir()) / "cadence-eval.db"))
os.environ.setdefault("AUTH_REQUIRED", "false")
sys.path.insert(0, str(ROOT))

from langchain_core.prompts import ChatPromptTemplate  # noqa: E402

from app.services import llm  # noqa: E402
from app.services.database import init_db  # noqa: E402
from app.services.director import stream_rewrite  # noqa: E402
from app.services.tts import speak_script, strip_all_ssml  # noqa: E402

GAP_SECONDS = 30          # keeps an old + new pair under the free tier's per-minute budget
MINUTE_RETRIES = 3        # a call that still hits the per-minute limit is retried after a wait

_TIC_RE = re.compile(r"\b(not (merely|just|simply|only)|(is|are|did|do)n['’]t (just|merely|only))\b", re.I)
_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")


class DailyLimitReached(Exception):
    """Groq's per-day allowance is used up, or the network is down: stop now, resume later."""


def _is_daily_limit(message: str) -> bool:
    text = message.lower()
    return "daily token allowance" in text or "tokens per day" in text


def _is_network_error(message: str) -> bool:
    text = message.lower()
    return "connection error" in text or "getaddrinfo" in text or "timed out" in text


def _is_minute_limit(message: str) -> bool:
    text = message.lower()
    return ("429" in text or "rate limit reached" in text) and not _is_daily_limit(message)


# ── Running the two prompts ──────────────────────────────────────

async def _stream_new(case: dict) -> dict:
    out = {"error": None, "tts_script": "", "display_script": "", "retries": 0, "waits": 0}
    t0 = time.perf_counter()
    async for chunk in stream_rewrite(case["user_text"], case["target_style"], speak=False, user_key="eval"):
        for line in chunk.splitlines():
            if not line.startswith("data:"):
                continue
            try:
                ev = json.loads(line[5:].strip())
            except ValueError:
                continue
            kind = ev.get("type")
            if kind == "script_done":
                out["display_script"] = ev.get("display_script", "")
                out["tts_script"] = ev.get("tts_script", "")
            elif kind == "error":
                out["error"] = ev.get("message") or "error"
            elif kind == "retry":
                out["retries"] += 1
            elif kind == "rate_limited":
                out["waits"] += 1
            elif kind == "token":
                out.setdefault("first_token_s", round(time.perf_counter() - t0, 2))
            elif kind == "done":
                out["prompt_tokens"] = ev.get("prompt_tokens") or 0
                out["tokens"] = out["prompt_tokens"] + (ev.get("completion_tokens") or 0)
    out["seconds"] = round(time.perf_counter() - t0, 2)
    return out


async def run_new(case: dict) -> dict:
    """The current prompt through the app's own streaming pipeline (no voice)."""
    attempt = 0
    while True:
        out = await _stream_new(case)
        error = out.get("error") or ""
        if _is_daily_limit(error):
            raise DailyLimitReached(error)
        if _is_network_error(error) and attempt >= MINUTE_RETRIES:
            raise DailyLimitReached(f"The network is down ({error})")      # says nothing about the prompt: never save it
        if not (_is_minute_limit(error) or _is_network_error(error)) or attempt >= MINUTE_RETRIES:
            return out
        attempt += 1
        await asyncio.sleep(60)


def load_old_prompt(ref: str) -> ChatPromptTemplate:
    """Import app/prompts/voxis.py as it is at `ref` without checking it out."""
    source = subprocess.run(["git", "show", f"{ref}:app/prompts/voxis.py"], cwd=ROOT,
                            capture_output=True, text=True, encoding="utf-8", check=True).stdout
    namespace: dict = {}
    exec(compile(source, f"{ref}:voxis.py", "exec"), namespace)
    return namespace["voxis_prompt"]


def parse_old_reply(text: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", re.sub(r"(?s)<think>.*?</think>", "", text).strip())
    start, end = cleaned.find("{"), cleaned.rfind("}")
    try:
        data = json.loads(cleaned[start:end + 1])
    except ValueError:
        return {"error": f"unparseable reply: {cleaned[:200]}"}
    if data.get("error"):
        return {"error": data.get("message") or "error"}
    return {"error": None, "display_script": data.get("display_script", ""), "tts_script": data.get("tts_script", "")}


async def run_old(case: dict, prompt: ChatPromptTemplate) -> dict:
    """The old prompt with the same model and settings, waiting out per-minute limits."""
    messages = await prompt.ainvoke({"user_text": case["user_text"], "target_style": case["target_style"]})
    t0, waits = time.perf_counter(), 0
    while True:
        try:
            reply = await llm.get_llm().ainvoke(messages)
            break
        except Exception as err:
            daily = llm.daily_limit_message(err)
            if daily:
                raise DailyLimitReached(daily) from err
            wait = llm.rate_limit_wait_seconds(err)
            if wait is None or waits >= MINUTE_RETRIES * 3:
                return {"error": f"{type(err).__name__}: {err}", "seconds": round(time.perf_counter() - t0, 2)}
            waits += 1
            await asyncio.sleep(wait + 1)
    out = parse_old_reply(reply.content if isinstance(reply.content, str) else str(reply.content))
    out["finish_reason"] = (getattr(reply, "response_metadata", None) or {}).get("finish_reason")
    if (out.get("error") or "").startswith("unparseable") and out["finish_reason"] == "length":
        out["error"] = "reply cut off at the max_tokens cap before the JSON closed"
    if not out.get("error") and not out.get("tts_script"):
        # The old JSON sometimes leaves tts_script empty. The old app spoke display_script anyway, so
        # voice that: an old side without audio would also give away which prompt wrote it.
        out.update(tts_script=out.get("display_script", ""), tts_missing=True)
    usage = llm.usage_from(reply) or {}
    out.update(seconds=round(time.perf_counter() - t0, 2), waits=waits,
               tokens=(usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0))
    return out


async def voice(result: dict, case: dict, out_dir: Path) -> None:
    """Speak a script through the app's current voice pipeline, so only the writing differs."""
    if result.get("error") or not result.get("tts_script") or result.get("audio"):
        return
    spoken = await speak_script(result["tts_script"], target_style=case["target_style"], content=case["user_text"])
    if not spoken.get("success"):
        result["audio_error"] = spoken.get("message", "failed")
        return
    # A neutral name inside the run folder: the report stays blind and the folder is self-contained.
    target = out_dir / "audio" / f"{uuid.uuid4().hex[:10]}.mp3"
    target.parent.mkdir(exist_ok=True)
    shutil.move(spoken["file_path"], target)
    result["audio"] = f"audio/{target.name}"


# ── Automatic checks ─────────────────────────────────────────────

def _loose(text: str) -> str:
    """Lower-case with hyphens and runs of whitespace folded, so 'twenty-three' matches 'twenty three'."""
    return re.sub(r"[\s\-]+", " ", text.lower())


def _sentences(text: str) -> list:
    return [s for s in re.split(r"(?<=[.!?…])\s+", text) if s.strip()]


def _directed_share(tts: str) -> float:
    """The share of a script's words that sit inside a prosody tag."""
    spoken = len(strip_all_ssml(tts).split())
    tagged = sum(len(strip_all_ssml(span).split()) for span in re.findall(r"<prosody[^>]*>(.*?)</prosody>", tts, re.S))
    return tagged / spoken if spoken else 0.0


def checks(case: dict, result: dict) -> list:
    """Return (ok, label) pairs for the obvious, mechanical things; ok=None is shown as information."""
    found = []
    got_error = bool(result.get("error"))
    want_error = case.get("expect") == "error"
    found.append((got_error == want_error, f"expected {'an error' if want_error else 'a script'}"
                  + ("" if got_error == want_error else f", got {'an error' if got_error else 'a script'}")))
    if got_error:
        return found
    text = result.get("display_script") or strip_all_ssml(result.get("tts_script", ""))
    words = len(text.split())
    spoken = _loose(text)
    for item in case.get("must_keep", []):
        # A script for the ear often writes numbers out ("twenty-three percent"), so a case can list spellings.
        spellings = item if isinstance(item, list) else [item]
        found.append((any(_loose(s) in spoken for s in spellings), f"keeps “{spellings[0]}”"))
    for item in case.get("must_not", []):
        found.append((_loose(item) not in spoken, f"does not say “{item}”"))
    if case.get("any_of"):
        found.append((any(_loose(t) in spoken for t in case["any_of"]["terms"]), case["any_of"]["label"]))
    if case.get("language"):
        hindi = bool(_DEVANAGARI_RE.search(text))
        found.append((hindi if case["language"] == "hindi" else not hindi, f"written in {case['language']}"))
    low, high = case.get("min_words"), case.get("max_words")
    if low or high:
        found.append(((not low or words >= low) and (not high or words <= high), f"{words} words (wants {low}–{high})"))
    else:
        found.append((None if words >= 30 else False, f"{words} words"))      # length is information, not a verdict
    tics = len(_TIC_RE.findall(text))
    if tics >= 2:
        found.append((False, f"“not just X” {tics} times"))
    tts = result.get("tts_script", "")
    share = _directed_share(tts)
    found.append((None if share <= 0.8 else False, f"{share:.0%} of words under speed or pitch tags"))
    for tag in ("prosody", "emphasis"):
        opened, closed = len(re.findall(rf"<{tag}\b", tts)), len(re.findall(rf"</{tag}>", tts))
        if opened != closed:
            found.append((False, f"{opened} <{tag}> opened, {closed} closed"))
    found.append((bool(re.search(r"[.!?…।\"”')]\s*$", text)), "ends on a finished sentence"))
    return found


# ── Report ───────────────────────────────────────────────────────

def side_html(result: dict, case: dict) -> str:
    rows = "".join(f'<li class="{"info" if ok is None else "ok" if ok else "bad"}">'
                   f'{"·" if ok is None else "✓" if ok else "✗"} {html.escape(label)}</li>'
                   for ok, label in checks(case, result))
    if result.get("error"):
        body = f'<p class="err">ERROR: {html.escape(str(result["error"]))}</p>'
    else:
        text = result.get("display_script") or strip_all_ssml(result.get("tts_script", ""))
        body = "".join(f"<p>{html.escape(p)}</p>" for p in text.split("\n\n") if p.strip())
        body += f'<details><summary>Voice script</summary><pre>{html.escape(result.get("tts_script", ""))}</pre></details>'
    if result.get("audio"):
        body += f'<audio controls preload="none" src="{html.escape(result["audio"])}"></audio>'
    elif result.get("audio_error"):
        body += f'<p class="meta">Audio failed: {html.escape(result["audio_error"])}</p>'
    return f'<ul class="checks">{rows}</ul>{body}'


_REPORT_SCRIPT = """
const run = document.body.dataset.run;
const load = (k) => { try { return localStorage.getItem(run + ':' + k); } catch (e) { return null; } };
const store = (k, v) => { try { localStorage.setItem(run + ':' + k, v); } catch (e) { /* private window */ } };
document.querySelectorAll('article[data-case]').forEach((art) => {
  const id = art.dataset.case;
  art.querySelectorAll('input[type=radio]').forEach((r) => {
    if (load(id) === r.value) r.checked = true;
    r.addEventListener('change', () => store(id, r.value));
  });
  const note = art.querySelector('textarea');
  note.value = load(id + ':note') || '';
  note.addEventListener('input', () => store(id + ':note', note.value));
});
document.getElementById('copy').addEventListener('click', async () => {
  const lines = [...document.querySelectorAll('article[data-case]')].map((art) => {
    const picked = art.querySelector('input[type=radio]:checked');
    const choice = picked ? picked.value : 'not judged';
    const who = picked && picked.value !== 'same' ? ' (= ' + art.dataset[picked.value.toLowerCase()] + ')' : '';
    const note = art.querySelector('textarea').value.trim();
    return art.dataset.case + ': ' + choice + who + (note ? ' | ' + note : '');
  });
  const out = document.getElementById('verdicts');
  out.value = lines.join('\\n');
  out.hidden = false;
  out.select();
  try { await navigator.clipboard.writeText(out.value); } catch (e) { /* the text is selected for manual copy */ }
});
"""


def _sides_for(case: dict, sides: list) -> list:
    return ["new"] if case.get("new_only") else sides


def write_report(cases: list, results: dict, out_dir: Path, seed: int, run_sides: list) -> Path:
    key, blocks, rule_blocks = [], [], []
    for case in cases:
        pair = results.get(case["id"]) or {}
        if any(s not in pair for s in _sides_for(case, run_sides)):
            continue        # shown once every side exists: a lone side would give away which prompt wrote it
        if case.get("new_only"):
            rule_blocks.append(f"""<article>
  <h2>{html.escape(case["id"])}</h2>
  <p class="meta"><b>Style:</b> {html.escape(case["target_style"])} · <i>{html.escape(case["why"])}. Current prompt only, not part of the blind comparison.</i></p>
  <blockquote>{html.escape(case["user_text"])}</blockquote>
  {side_html(pair["new"], case)}
</article>""")
            continue
        sides = [s for s in ("new", "old") if s in pair]
        random.Random(f"{seed}:{case['id']}").shuffle(sides)    # per case, so it never changes as the run grows
        key.append(f'<li><b>{html.escape(case["id"])}</b>: ' + ", ".join(
            f'{"AB"[i]} = {s}' for i, s in enumerate(sides)) + "</li>")
        cols = "".join(f'<section><h3>{"AB"[i]}</h3>{side_html(pair[s], case)}</section>'
                       for i, s in enumerate(sides))
        data_sides = " ".join(f'data-{"ab"[i]}="{s}"' for i, s in enumerate(sides))
        cid = html.escape(case["id"])
        picks = "".join(f'<label><input type="radio" name="{cid}" value="{v}"> {v}</label>' for v in ("A", "B", "same"))
        blocks.append(f"""<article data-case="{cid}" {data_sides}>
  <h2>{cid}</h2>
  <p class="meta"><b>Style:</b> {html.escape(case["target_style"])} · <i>{html.escape(case["why"])}</i></p>
  <blockquote>{html.escape(case["user_text"])}</blockquote>
  <div class="cols">{cols}</div>
  <div class="verdict">Better: {picks}<textarea rows="1" placeholder="notes"></textarea></div>
</article>""")
    page = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Director Quality Eval</title><style>
:root{{--bg:#fafafa;--card:#fff;--ink:#1c1c1c;--muted:#555;--line:#ddd;--quote:#f1f1f1;--ok:#1d6b35;--bad:#b3261e}}
@media (prefers-color-scheme: dark){{:root{{--bg:#151515;--card:#1f1f1f;--ink:#e8e8e8;--muted:#a8a8a8;--line:#333;--quote:#2a2a2a;--ok:#6fcf8f;--bad:#ff8a80}}}}
body{{font:15px/1.55 system-ui,sans-serif;max-width:1200px;margin:0 auto;padding:16px;background:var(--bg);color:var(--ink)}}
article{{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:16px;margin:18px 0}}
.cols{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(320px,100%),1fr));gap:16px}}
section{{border-top:3px solid var(--muted);padding-top:6px}} blockquote{{margin:8px 0;padding:8px 12px;background:var(--quote);border-radius:6px}}
.checks{{list-style:none;padding:0;margin:0 0 8px;font-size:13px}} .ok{{color:var(--ok)}} .bad{{color:var(--bad);font-weight:600}} .info{{color:var(--muted)}}
.err{{color:var(--bad)}} pre{{white-space:pre-wrap;font-size:12px;background:var(--quote);padding:8px}} .meta{{color:var(--muted)}}
audio{{width:100%;margin-top:8px}} .verdict{{display:flex;flex-wrap:wrap;gap:12px;align-items:center;margin-top:12px;color:var(--muted)}}
.verdict textarea{{flex:1;min-width:200px;font:inherit;background:var(--bg);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:4px 8px}}
#verdicts{{width:100%;min-height:160px;font:13px monospace;background:var(--card);color:var(--ink)}} button{{font:inherit;padding:6px 14px}}
</style></head><body data-run="{html.escape(out_dir.name)}"><h1>Speech Director: blind A/B</h1>
<p>{len(blocks) + len(rule_blocks)} of {len(cases)} cases complete. Each case shows two scripts in random order: judge the writing (and the audio, if present), pick A, B or same, then press <b>Copy verdicts</b> and paste the text back.</p>
{"".join(blocks)}
{"".join(rule_blocks)}
<p><button id="copy" type="button">Copy verdicts</button></p>
<textarea id="verdicts" hidden readonly></textarea>
<details><summary>Answer key (open after judging)</summary><ul>{"".join(key)}</ul></details>
<script>{_REPORT_SCRIPT}</script>
</body></html>"""
    path = out_dir / "report.html"
    path.write_text(page, encoding="utf-8")
    return path


# ── Main ─────────────────────────────────────────────────────────

def save(out_dir: Path, results: dict) -> None:
    (out_dir / "results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")


def reuse_old_side(source: Path, out_dir: Path) -> dict:
    """Copy the old prompt's scripts and audio from an earlier run: re-asking an unchanged prompt only spends tokens."""
    earlier = json.loads((source / "results.json").read_text(encoding="utf-8"))
    results = {}
    for case_id, pair in earlier.items():
        old = pair.get("old")
        if not old:
            continue
        results[case_id] = {"old": old}
        if old.get("audio"):
            target = out_dir / old["audio"]
            target.parent.mkdir(exist_ok=True)
            shutil.copyfile(source / old["audio"], target)
    return results


async def revoice_run(out_dir: Path, cases: list, results: dict) -> None:
    """Re-render every script in a run with the current voice code. Azure only: no Groq tokens."""
    by_id = {c["id"]: c for c in cases}
    for case_id, pair in results.items():
        for result in pair.values():
            if result.get("audio"):
                (out_dir / result["audio"]).unlink(missing_ok=True)
            result.pop("audio", None)
            result.pop("audio_error", None)
            await voice(result, by_id[case_id], out_dir)
        save(out_dir, results)
        print(f"re-voiced {case_id}", flush=True)


def summary_lines(cases: list, results: dict) -> list:
    """Each prompt's writing shape on the cases both wrote with voice direction, then the current prompt's behaviour."""
    both = [results[c["id"]] for c in cases if not c.get("new_only") and all(
        s in results.get(c["id"], {}) and not results[c["id"]][s].get("error") and not results[c["id"]][s].get("tts_missing")
        for s in ("new", "old"))]
    lines = [f"Compared on {len(both)} cases both prompts wrote with voice direction."]
    for side in ("new", "old"):
        scripts = [p[side] for p in both]
        words = sum(len(r["display_script"].split()) for r in scripts) or 1
        sentences = [len(s.split()) for r in scripts for s in _sentences(r["display_script"])] or [0]
        tts = " ".join(r["tts_script"] for r in scripts)
        directed = sum(_directed_share(r["tts_script"]) * len(r["display_script"].split()) for r in scripts)
        tics = sum(len(_TIC_RE.findall(r["display_script"])) for r in scripts)
        lines.append(
            f"{side}: {words} words | sentences avg {sum(sentences) / len(sentences):.1f} words, "
            f"{100 * sum(n <= 4 for n in sentences) / len(sentences):.0f}% are 4 words or fewer | per 100 words: "
            f"prosody {100 * len(re.findall(r'<prosody[ >]', tts)) / words:.1f}, breaks {100 * tts.count('<break') / words:.1f}, "
            f"'not just X' {100 * tics / words:.2f} | {100 * directed / words:.0f}% of words under tags")
    new = [(c["id"], results[c["id"]]["new"]) for c in cases if "new" in results.get(c["id"], {})]
    lines.append("first-try refusals on the current prompt: " + (", ".join(i for i, r in new if r.get("retries")) or "none"))
    clean = [r for _, r in new if r.get("first_token_s") and not r.get("waits") and not r.get("retries")]
    if clean:
        lines.append(f"first token avg {sum(r['first_token_s'] for r in clean) / len(clean):.2f} s over {len(clean)} calls; "
                     f"prompt tokens avg {sum(r.get('prompt_tokens') or 0 for r in clean) / len(clean):.0f}")
    return lines


def _shown(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", help="comma-separated case ids")
    parser.add_argument("--old-ref", default="working-demo", help="git ref holding the old prompt")
    parser.add_argument("--no-old", action="store_true", help="run the new prompt only")
    parser.add_argument("--audio", action="store_true", help="voice every script (uses Azure)")
    parser.add_argument("--limit", type=int, help="run at most this many cases now; finish with --resume")
    parser.add_argument("--resume", type=Path, help="a run folder under evals/out to continue")
    parser.add_argument("--reuse-old", type=Path, help="an earlier run whose old side (scripts and audio) to reuse")
    parser.add_argument("--revoice", type=Path, help="a run folder whose audio to re-render with the current voice code")
    parser.add_argument("--seed", type=int, default=int(time.time()), help="side shuffle seed")
    args = parser.parse_args()

    if args.revoice:
        out_dir = args.revoice.resolve()
        meta = json.loads((out_dir / "meta.json").read_text(encoding="utf-8"))
        results = json.loads((out_dir / "results.json").read_text(encoding="utf-8"))
        cases = json.loads(CASES.read_text(encoding="utf-8"))
        init_db()
        await revoice_run(out_dir, cases, results)
        run_sides = ["new"] + (["old"] if meta.get("old_ref") else [])
        print("Report:", write_report(cases, results, out_dir, meta["seed"], run_sides), flush=True)
        return

    if args.resume:
        out_dir = args.resume.resolve()
        meta = json.loads((out_dir / "meta.json").read_text(encoding="utf-8"))
        saved = out_dir / "results.json"
        results = json.loads(saved.read_text(encoding="utf-8")) if saved.exists() else {}
    else:
        out_dir = ROOT / "evals" / "out" / time.strftime("%Y%m%d-%H%M%S")
        out_dir.mkdir(parents=True)
        meta = {"seed": args.seed, "old_ref": None if args.no_old else args.old_ref,
                "audio": args.audio, "only": args.only}
        (out_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        results = reuse_old_side(args.reuse_old.resolve(), out_dir) if args.reuse_old else {}
        save(out_dir, results)

    cases = json.loads(CASES.read_text(encoding="utf-8"))
    if meta.get("only"):
        wanted = set(meta["only"].split(","))
        cases = [c for c in cases if c["id"] in wanted]
    sides = ["new"] + (["old"] if meta.get("old_ref") else [])
    todo = [c for c in cases if any(s not in results.get(c["id"], {}) for s in _sides_for(c, sides))]
    if args.limit:
        todo = todo[:args.limit]
    init_db()
    needs_old = any(s == "old" and s not in results.get(c["id"], {}) for c in todo for s in _sides_for(c, sides))
    old_prompt = load_old_prompt(meta["old_ref"]) if needs_old else None
    print(f"{len(todo)} case(s) to run, results in {_shown(out_dir)}", flush=True)

    tokens, first_call = 0, True
    try:
        for n, case in enumerate(todo, 1):
            pair = results.setdefault(case["id"], {})
            for side in _sides_for(case, sides):
                if side in pair:
                    continue
                if not first_call:
                    await asyncio.sleep(GAP_SECONDS)
                first_call = False
                pair[side] = await (run_new(case) if side == "new" else run_old(case, old_prompt))
                tokens += pair[side].get("tokens") or 0
                save(out_dir, results)
            if meta.get("audio"):
                for result in pair.values():
                    await voice(result, case, out_dir)
                save(out_dir, results)
            write_report(cases, results, out_dir, meta["seed"], sides)
            summary = ", ".join(f"{s}: {'ERROR' if r.get('error') else 'ok'} {r.get('seconds')}s" for s, r in pair.items())
            print(f"[{n}/{len(todo)}] {case['id']} - {summary} - {tokens:,} Groq tokens so far", flush=True)
    except DailyLimitReached as stop:
        write_report(cases, results, out_dir, meta["seed"], sides)
        print(f"Stopped: {stop}", flush=True)
        print(f"Finish later with: python -m evals.run_director_eval --resume {_shown(out_dir)}", flush=True)
        print("Report so far:", out_dir / "report.html", flush=True)
        sys.exit(2)

    report = write_report(cases, results, out_dir, meta["seed"], sides)
    left = sum(any(s not in results.get(c["id"], {}) for s in _sides_for(c, sides)) for c in cases)
    if left:
        print(f"{left} case(s) still to run: python -m evals.run_director_eval --resume {_shown(out_dir)}", flush=True)
    summary = summary_lines(cases, results)
    (out_dir / "summary.txt").write_text("\n".join(summary) + "\n", encoding="utf-8")
    print("\n".join(summary), flush=True)
    print("Report:", report, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
