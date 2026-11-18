"""
judge.py — The safety judge
=============================
Before the writer runs, one short call decides what to do with the request
(prompts/judge.py): WRITE, WRITE_NOTE (write it and show the fixed note),
REFUSE, or UNSURE.

    attempt 1 ──► WRITE / WRITE_NOTE / a refusal label ──► done
        │
        └─ UNSURE, no label, too slow or a failed call
               ▼
    attempt 2, the same question plus "UNSURE is not allowed this time"
        ├─► WRITE / WRITE_NOTE / a refusal label ──► done
        └─ anything else ──► refused, with a message that says why
                             (could not decide, or took too long)

There is no fallback to the old all-in-one prompt: every request ends in a
decision or in a clear, terminal message. A per-minute 429 is waited out
visibly (the `rate_limited` event) and does not count as an attempt; the
daily limit ends the request at once.
"""

import asyncio
import logging
import re
import time
from typing import Awaitable, Callable, Optional

from langchain_core.messages import HumanMessage

from app.config import JUDGE_TIMEOUT_S
from app.prompts.judge import FORCED_CHOICE_NOTE, judge_prompt
from app.services import llm
from app.services.llm import daily_limit_message, rate_limit_wait_seconds, rate_limited_event, usage_from

logger = logging.getLogger(__name__)

ATTEMPTS = 2
_LABEL_RE = re.compile(r"\b(WRITE_NOTE|WRITE|REFUSE_TONE|REFUSE|UNUSABLE|UNSURE)\b")

# What the reader sees. Drafts for the owner to word as they like.
CHECKING_TEXT = "Checking your request…"      # shown at once while the judge decides, in place of the blinking cursor
NOTE_TEXT = "This piece touches a sensitive subject. It was written for practice and does not reflect the views of Cadence Coach."
REFUSED_MESSAGE = ("This request can't be written: it asks for a speech that attacks or harms people "
                   "(hatred, violence, illegal acts, or content that puts children at risk). Please change the request.")
TONE_MESSAGE = ("This style would make light of a death or a tragedy. Please choose a style that respects the occasion, "
                "and the speech will be written.")
UNUSABLE_MESSAGE = ("There isn't enough here to write a speech from. Please add a sentence or two about what you want "
                    "to say, or describe the occasion.")
UNSURE_MESSAGE = "We couldn't tell how to handle this request. Please rephrase it or add some context."
SLOW_MESSAGE = "This is taking longer than usual. Please try again in a moment."


def parse_label(reply: str) -> Optional[str]:
    """The first label in the reply, ignoring a <think> block; None if there is none."""
    reply = re.sub(r"(?s)<think>.*?</think>", "", reply or "")
    found = _LABEL_RE.search(reply.upper())
    return found.group(1) if found else None


async def _ask(messages) -> tuple:
    """One judge call, read to the end (a label is a few tokens). Returns (reply, usage or None)."""
    text, usage = "", None
    async for chunk in llm.get_judge_llm().astream(messages):
        usage = usage_from(chunk) or usage
        if isinstance(chunk.content, str):
            text += chunk.content
    return text, usage


async def judge_request(user_text: str, target_style: str,
                        notify: Optional[Callable[[str], Awaitable[None]]] = None) -> dict:
    """
    Decide what to do with one request. Returns
      label       WRITE | WRITE_NOTE | REFUSE | REFUSE_TONE | UNUSABLE | UNSURE | SLOW | DAILY_LIMIT
      write       True when the writer should run
      message     what to show the reader when it should not
      attempts, input_tokens, output_tokens, ms, replies
    `notify` receives SSE strings (the `rate_limited` event) while a per-minute limit is waited out.
    """
    t0 = time.perf_counter()
    base = (await judge_prompt.ainvoke({"user_text": user_text, "target_style": target_style})).to_messages()
    out = {"label": None, "write": False, "message": "", "attempts": 0, "input_tokens": 0, "output_tokens": 0, "replies": []}
    last_problem, waits = "unsure", 0

    while out["attempts"] < ATTEMPTS:
        out["attempts"] += 1
        messages = base if out["attempts"] == 1 else base + [HumanMessage(FORCED_CHOICE_NOTE)]
        try:
            reply, usage = await asyncio.wait_for(_ask(messages), timeout=JUDGE_TIMEOUT_S)
        except asyncio.TimeoutError:
            logger.warning("Safety judge took longer than %.1fs (attempt %d).", JUDGE_TIMEOUT_S, out["attempts"])
            last_problem = "slow"
            out["replies"].append("(timeout)")
            continue
        except Exception as err:
            daily = daily_limit_message(err)
            if daily:
                out.update(label="DAILY_LIMIT", message=daily)
                break
            wait = rate_limit_wait_seconds(err)
            if wait is not None and waits < 2:
                waits += 1
                out["attempts"] -= 1        # waiting out a per-minute limit is not an attempt
                if notify:
                    await notify(rate_limited_event(wait))
                await asyncio.sleep(wait + 0.5)
                continue
            logger.warning("Safety judge call failed (attempt %d): %s", out["attempts"], err)
            last_problem = "slow"
            out["replies"].append(f"(error: {type(err).__name__})")
            continue

        if usage:
            out["input_tokens"] += usage.get("input_tokens", 0)
            out["output_tokens"] += usage.get("output_tokens", 0)
        out["replies"].append(reply.strip()[:40])
        label = parse_label(reply)
        if label in ("WRITE", "WRITE_NOTE"):
            out.update(label=label, write=True)
            break
        if label in ("REFUSE", "REFUSE_TONE", "UNUSABLE"):
            out.update(label=label, message={"REFUSE": REFUSED_MESSAGE, "REFUSE_TONE": TONE_MESSAGE, "UNUSABLE": UNUSABLE_MESSAGE}[label])
            break
        last_problem = "unsure"
    else:
        out.update(label="UNSURE" if last_problem == "unsure" else "SLOW",
                   message=UNSURE_MESSAGE if last_problem == "unsure" else SLOW_MESSAGE)

    out["ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return out
