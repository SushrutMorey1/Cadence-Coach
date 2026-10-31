import re
import time
import uuid
import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import AsyncIterator, Optional

import azure.cognitiveservices.speech as speechsdk

from app.config import (
    AZURE_SPEECH_KEY,
    AZURE_SPEECH_REGION,
    AUDIO_OUTPUT_DIR,
    AUDIO_RETENTION_HOURS,
    TTS_POOL_SIZE,
    VOICES,
    DEFAULT_VOICE,
)
from app.services.profiler import add_span, elapsed_ms
from app.utils.retry import sync_retry

logger = logging.getLogger(__name__)


# Which speaking styles each voice supports
VOICE_STYLES = {
    "en-US-AriaNeural": {
        "narration-professional", "empathetic", "chat", "cheerful",
        "customerservice", "excited", "friendly", "hopeful",
        "newscast-casual", "newscast-formal", "sad", "angry",
        "shouting", "terrified", "unfriendly", "whispering",
    },
    "en-US-DavisNeural": {
        "chat", "cheerful", "excited", "friendly", "hopeful",
        "sad", "angry", "shouting", "terrified", "unfriendly", "whispering",
    },
    "en-US-GuyNeural": {
        "newscast", "cheerful", "excited", "friendly", "hopeful",
        "sad", "angry", "shouting", "terrified", "unfriendly", "whispering",
    },
    "en-US-JennyNeural": {
        "assistant", "chat", "cheerful", "customerservice", "excited",
        "friendly", "hopeful", "newscast", "sad", "angry",
        "shouting", "terrified", "unfriendly", "whispering",
    },
    "en-US-SaraNeural": {
        "cheerful", "friendly", "hopeful", "sad", "angry",
        "excited", "shouting", "terrified", "unfriendly", "whispering",
    },
    "en-US-JasonNeural": {
        "cheerful", "friendly", "hopeful", "sad", "angry",
        "excited", "shouting", "terrified", "unfriendly", "whispering",
    },
}

# Keyword -> (style, degree) mapping
_STYLE_KEYWORDS = [
    ("ted talk",       "friendly",                 1.0),
    ("ted ",           "friendly",                 1.0),
    ("keynote",        "narration-professional",   1.0),
    ("commencement",   "hopeful",                  1.0),
    ("narrat",         "narration-professional",   1.0),
    ("corporate",      "narration-professional",   0.8),
    ("executive",      "narration-professional",   0.8),
    ("professional",   "narration-professional",   0.8),
    ("newscast",       "newscast",                 1.0),
    ("news anchor",    "newscast",                 1.0),
    ("broadcast",      "newscast",                 0.9),
    ("journalist",     "newscast",                 0.9),
    ("podcast",        "chat",                     0.9),
    ("casual",         "chat",                     0.8),
    ("conversation",   "chat",                     0.8),
    ("therapist",      "empathetic",               1.0),
    ("counsel",        "empathetic",               0.9),
    ("empathetic",     "empathetic",               1.0),
    ("empathy",        "empathetic",               1.0),
    ("compassion",     "empathetic",               0.9),
    ("warm",           "friendly",                 0.9),
    ("friendly",       "friendly",                 1.0),
    ("kind",           "friendly",                 0.8),
    ("gentle",         "friendly",                 0.7),
    ("motivat",        "excited",                  0.7),
    ("coach",          "excited",                  0.6),
    ("hype",           "excited",                  0.9),
    ("energetic",      "excited",                  0.8),
    ("inspir",         "hopeful",                  1.0),
    ("hope",           "hopeful",                  1.0),
    ("optimist",       "hopeful",                  0.9),
    ("uplift",         "hopeful",                  0.9),
    ("calm",           "friendly",                 0.6),
    ("sooth",          "friendly",                 0.6),
    ("relax",          "chat",                     0.7),
    ("cheer",          "cheerful",                 1.0),
    ("happy",          "cheerful",                 0.9),
    ("joy",            "cheerful",                 0.8),
    ("celebrat",       "cheerful",                 0.9),
    ("sad",            "sad",                      0.8),
    ("sorrow",         "sad",                      0.9),
    ("somber",         "sad",                      0.7),
    ("funeral",        "sad",                      0.8),
    ("serious",        "narration-professional",   0.7),
    ("authorit",       "narration-professional",   0.9),
    ("command",        "narration-professional",   0.9),
    ("military",       "narration-professional",   0.8),
    ("morgan freeman", "friendly",                 0.8),
    ("obama",          "hopeful",                  0.9),
    ("service",        "customerservice",           0.9),
    ("assist",         "assistant",                0.9),
    ("whisper",        "whispering",               1.0),
    ("secret",         "whispering",               0.7),
]


def infer_speaking_style(target_style: str) -> tuple:
    """Map a target_style description to an Azure speaking style and degree."""
    style_lower = target_style.lower() if target_style else ""

    for keyword, style, degree in _STYLE_KEYWORDS:
        if keyword in style_lower:
            return (style, degree)

    return ("friendly", 0.8)


def get_valid_style_for_voice(voice: str, desired_style: str) -> Optional[str]:
    """Check if voice supports the style, otherwise find a fallback."""
    supported = VOICE_STYLES.get(voice, set())

    if desired_style in supported:
        return desired_style

    fallbacks = ["friendly", "chat", "cheerful", "hopeful"]
    for fb in fallbacks:
        if fb in supported:
            return fb

    return None


def strip_all_ssml(text: str) -> str:
    """Remove ALL SSML/XML tags and bracketed notes, returning pure text."""
    text = re.sub(r"\[.*?\]", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    return text.strip()


# ── Voice direction ─────────────────────────────────────────
# VOXIS writes a `tts_script` with gentle prosody tags and bracketed
# director's notes. Azure rejects anything malformed and silently ignores
# anything it does not know, so before synthesis we:
#   1. turn director's notes into prosody on the sentence they govern,
#   2. keep only a whitelist of tags and clamp every value to a safe range,
#   3. repair unbalanced tags and prove the fragment parses as XML.
# If any of that fails the caller falls back to the plain text, so a bad
# annotation can never cost the user their audio.

_ALLOWED_TAGS = {"prosody", "break", "emphasis", "p", "s"}
_TAG_RE = re.compile(r"<[^<>]*>")
_TAG_PARTS_RE = re.compile(r"^<\s*(/?)\s*([A-Za-z][\w:.-]*)\s*([^>]*?)\s*(/?)\s*>$", re.S)
_ATTR_RE = re.compile(r"([\w:-]+)\s*=\s*(?:\"([^\"]*)\"|'([^']*)')")
_PCT_RE = re.compile(r"^([+-]?\d+(?:\.\d+)?)\s*%$")
_NOTE_RE = re.compile(r"\[([^\[\]\n]{1,60})\]")
# A sentence the note can govern: plain text (no tags/notes) up to a terminator.
_GOVERNED_RE = re.compile(r"^\s*([^<\[\]]*?[.!?…]+[\"”’)\]]*)(?=\s|$)")
_CLAUSE_RE = re.compile(r"^\s*([^<\[\]]+?)(?=<|\[|$)", re.S)

_PITCH_LIMIT = 20.0    # percent
_RATE_LIMIT = 30.0     # percent
_VOLUME_LIMIT = 50.0   # percent
_BREAK_MIN_MS, _BREAK_MAX_MS = 50, 500

_PITCH_WORDS = {"x-low": -15.0, "low": -8.0, "medium": None, "default": None, "high": 8.0, "x-high": 15.0}
_RATE_WORDS = {"x-slow": -30.0, "slow": -15.0, "medium": None, "default": None, "fast": 15.0, "x-fast": 30.0}
_VOLUME_WORDS = {"silent", "x-soft", "soft", "medium", "loud", "x-loud", "default"}
_EMPHASIS_LEVELS = {"reduced", "none", "moderate", "strong"}
_BREAK_STRENGTHS = {"none", "x-weak", "weak", "medium", "strong", "x-strong"}

# Director's notes → the prosody applied to the sentence that follows them.
# Order matters: the first rule whose keyword appears in the note wins.
_NOTE_RULES = [
    (("let this land", "let it land", "pause", "beat", "hold", "breathe", "silence"), "break"),
    (("intimate", "softer", "quiet", "hushed", "whisper", "conspirator", "tender", "gentle"),
     {"pitch": "-3%", "rate": "-6%", "volume": "soft"}),
    (("voice drops", "drops", "lower", "deeper", "gravity", "grave", "somber", "serious"),
     {"pitch": "-4%", "rate": "-6%"}),
    (("voice rises", "rises", "building", "momentum", "energy", "faster", "excite", "urgent", "accelerat", "crescendo"),
     {"pitch": "+4%", "rate": "+6%"}),
    (("smile", "warm", "bright", "lift", "hopeful", "optimis", "playful"), {"pitch": "+3%"}),
    (("weight", "slow", "deliberate", "emphasis", "stress", "measured", "firm", "land"), {"rate": "-10%"}),
]


def _clamp_pct(raw: str, limit: float, words: Optional[dict] = None, stats: Optional[dict] = None) -> Optional[str]:
    """Normalise a prosody value to a signed percentage within ±limit, or None to drop it."""
    value = (raw or "").strip().lower()
    if not value:
        return None
    if words is not None and value in words:
        mapped = words[value]
        return None if mapped is None else f"{mapped:+.0f}%"
    m = _PCT_RE.match(value)
    if m:
        num = float(m.group(1))
    else:
        try:
            # Relative multipliers like rate="0.9" → -10%
            num = (float(value) - 1.0) * 100.0
        except ValueError:
            return None
    clamped = max(-limit, min(limit, num))
    if stats is not None and abs(clamped - num) > 0.01:
        stats["clamped"] += 1
    if abs(clamped) < 0.5:
        return None
    return f"{clamped:+.0f}%"


def _break_ms(raw: str, stats: dict) -> Optional[int]:
    value = (raw or "").strip().lower()
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(ms|s)$", value)
    if not m:
        return None
    ms = float(m.group(1)) * (1000 if m.group(2) == "s" else 1)
    clamped = int(max(_BREAK_MIN_MS, min(_BREAK_MAX_MS, ms)))
    if clamped != int(ms):
        stats["clamped"] += 1
    return clamped


def _escape_text(text: str) -> str:
    """Escape plain text for XML without double-escaping existing entities."""
    text = re.sub(r"&(?!amp;|lt;|gt;|apos;|quot;|#\d+;)", "&amp;", text)
    return text.replace("<", "&lt;").replace(">", "&gt;")


def _apply_director_notes(text: str, stats: dict) -> str:
    """Convert `[bracketed notes]` into prosody wrapping the sentence they introduce."""
    out: list = []
    pos = 0
    while True:
        m = _NOTE_RE.search(text, pos)
        if not m:
            out.append(text[pos:])
            break
        out.append(text[pos:m.start()])
        note = m.group(1).strip().lower()
        rest_start = m.end()
        rule = next((effect for keys, effect in _NOTE_RULES if any(k in note for k in keys)), None)
        stats["notes"] += 1

        if rule is None:
            stats["notes_dropped"] += 1
            pos = rest_start
            continue

        if rule == "break":
            # "Let this land": the pause belongs after the sentence it introduces.
            g = _GOVERNED_RE.match(text[rest_start:])
            if g and ("land" in note or "hold" in note):
                out.append(g.group(1) + ' <break time="450ms"/>')
                pos = rest_start + g.end()
            else:
                out.append('<break time="400ms"/>')
                pos = rest_start
            continue

        g = _GOVERNED_RE.match(text[rest_start:]) or _CLAUSE_RE.match(text[rest_start:])
        if g is None or len(g.group(1).split()) < 3:
            stats["notes_dropped"] += 1
            pos = rest_start
            continue
        attrs = " ".join(f'{k}="{v}"' for k, v in rule.items())
        out.append(f"<prosody {attrs}>{g.group(1).strip()}</prosody>")
        pos = rest_start + g.end()
    return "".join(out)


def _sanitize_tags(text: str, stats: dict) -> str:
    """Whitelist tags, clamp attribute values, balance the tree and escape text."""
    out: list = []
    stack: list = []   # [(name, emitted)] — emitted=False means the opener was dropped
    pos = 0
    for m in _TAG_RE.finditer(text):
        out.append(_escape_text(text[pos:m.start()]))
        pos = m.end()
        parts = _TAG_PARTS_RE.match(m.group(0).replace("“", '"').replace("”", '"'))
        if not parts:
            stats["tags_dropped"] += 1
            continue
        closing, name, attr_src, self_closing = parts.group(1) == "/", parts.group(2).lower(), parts.group(3), parts.group(4) == "/"

        if name not in _ALLOWED_TAGS:
            stats["tags_dropped"] += 1
            if not closing and not self_closing:
                stack.append((name, False))
            elif closing:
                for i in range(len(stack) - 1, -1, -1):
                    if stack[i][0] == name:
                        del stack[i]
                        break
            continue

        attrs = {k.lower(): (v1 if v1 is not None else v2) for k, v1, v2 in _ATTR_RE.findall(attr_src)}

        if closing:
            # Close the nearest matching opener, closing anything left open above it.
            idx = next((i for i in range(len(stack) - 1, -1, -1) if stack[i][0] == name), None)
            if idx is None:
                stats["repaired"] += 1
                continue
            for open_name, emitted in reversed(stack[idx + 1:]):
                if emitted:
                    out.append(f"</{open_name}>")
                    stats["repaired"] += 1
            _, emitted = stack[idx]
            del stack[idx:]
            if emitted:
                out.append(f"</{name}>")
            continue

        if name == "break":
            ms = _break_ms(attrs.get("time", ""), stats)
            strength = attrs.get("strength", "").lower()
            if ms is not None:
                out.append(f'<break time="{ms}ms"/>')
            elif strength in _BREAK_STRENGTHS:
                out.append(f'<break strength="{strength}"/>')
            else:
                out.append('<break time="250ms"/>')
            stats["tags_kept"] += 1
            continue

        if name == "prosody":
            kept = {}
            pitch = _clamp_pct(attrs.get("pitch", ""), _PITCH_LIMIT, _PITCH_WORDS, stats)
            rate = _clamp_pct(attrs.get("rate", ""), _RATE_LIMIT, _RATE_WORDS, stats)
            vol_raw = attrs.get("volume", "").strip().lower()
            volume = vol_raw if vol_raw in _VOLUME_WORDS else _clamp_pct(vol_raw, _VOLUME_LIMIT, None, stats)
            if pitch:
                kept["pitch"] = pitch
            if rate:
                kept["rate"] = rate
            if volume and volume not in ("medium", "default"):
                kept["volume"] = volume
            if self_closing:
                stats["tags_dropped"] += 1
                continue
            if not kept:
                stats["tags_dropped"] += 1
                stack.append((name, False))
                continue
            out.append("<prosody " + " ".join(f'{k}="{v}"' for k, v in kept.items()) + ">")
            stack.append((name, True))
            stats["tags_kept"] += 1
            continue

        if name == "emphasis":
            if self_closing:
                stats["tags_dropped"] += 1
                continue
            level = attrs.get("level", "moderate").lower()
            out.append(f'<emphasis level="{level if level in _EMPHASIS_LEVELS else "moderate"}">')
            stack.append((name, True))
            stats["tags_kept"] += 1
            continue

        # <p> / <s>
        if self_closing:
            continue
        out.append(f"<{name}>")
        stack.append((name, True))
        stats["tags_kept"] += 1

    out.append(_escape_text(text[pos:]))
    for open_name, emitted in reversed(stack):
        if emitted:
            out.append(f"</{open_name}>")
            stats["repaired"] += 1
    return "".join(out)


def direct_tts_script(tts_script: str) -> tuple:
    """
    Turn VOXIS's annotated `tts_script` into an SSML fragment Azure will accept.

    Returns (fragment, stats). `fragment` is XML-escaped text with only
    whitelisted, clamped, balanced tags; `stats["fallback"]` is True when
    the script could not be made valid and the plain text was used instead.
    """
    import xml.etree.ElementTree as ET

    stats = {"notes": 0, "notes_dropped": 0, "tags_kept": 0, "tags_dropped": 0,
             "clamped": 0, "repaired": 0, "fallback": False}
    text = tts_script or ""
    try:
        fragment = _sanitize_tags(_apply_director_notes(text, stats), stats)
        fragment = re.sub(r"[ \t]+", " ", fragment)
        fragment = re.sub(r"\n\s*\n+", "\n\n", fragment).strip()
        ET.fromstring(f"<speak>{fragment}</speak>")
    except Exception as err:  # malformed beyond repair → plain read
        logger.warning("Voice direction rejected (%s) — speaking the plain text instead.", err)
        stats["fallback"] = True
        fragment = _escape_text(strip_all_ssml(text))
    if not re.sub(r"<[^>]+>|\s+", "", fragment):
        stats["fallback"] = True
        fragment = _escape_text(strip_all_ssml(text))
    return fragment, stats


def build_ssml(clean_text, voice, speaking_style=None, style_degree=1.0, lang="en-US"):
    """Build the final SSML document with optional express-as wrapping."""
    # Escape ampersands for XML
    text = re.sub(r"&(?!amp;|lt;|gt;|apos;|quot;)", "&amp;", clean_text)

    if speaking_style:
        inner = (
            f'<mstts:express-as style="{speaking_style}" '
            f'styledegree="{style_degree:.1f}">'
            f'{text}'
            f'</mstts:express-as>'
        )
    else:
        inner = text

    ssml = (
        f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis"'
        f' xmlns:mstts="https://www.w3.org/2001/mstts" xml:lang="{lang}">'
        f'<voice name="{voice}">'
        f'{inner}'
        f'</voice></speak>'
    )
    return ssml


def _get_speech_config():
    """Create Azure SpeechConfig (24 kHz mono MP3: small, and what browsers decode fastest)."""
    if not AZURE_SPEECH_KEY:
        raise ValueError("AZURE_SPEECH_KEY not found. Set it in .env.")

    config = speechsdk.SpeechConfig(
        subscription=AZURE_SPEECH_KEY,
        region=AZURE_SPEECH_REGION,
    )
    config.set_speech_synthesis_output_format(speechsdk.SpeechSynthesisOutputFormat.Audio24Khz48KBitRateMonoMp3)
    return config


def resolve_voice(voice_input):
    """Map an alias like 'davis' to a full Azure voice name."""
    if voice_input is None:
        return DEFAULT_VOICE
    return VOICES.get(voice_input, voice_input)


# ── Synthesizer Pool (connection reuse) ─────────────────────

_WARMUP_SSML = (
    '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="en-US">'
    '<voice name="en-US-DavisNeural"><break time="10ms"/>.</voice></speak>'
)


class _SynthesizerPool:
    """
    Pool of Azure SpeechSynthesizer instances with their connections kept open.

    Creating a synthesizer is cheap; the TLS + WebSocket handshake to Azure
    (~0.6 s) happens on its first use unless the connection is opened ahead of
    time (`warm`). Reusing them also skips that handshake on later requests.
    Synthesizers use audio_config=None, so callers get the bytes back from
    result.audio_data and decide where to put them.
    """

    def __init__(self, max_size: int = 3):
        self._max_size = max_size
        self._pool: list = []
        self._lock = threading.Lock()
        self._build_lock = threading.Lock()
        self._speech_config: Optional[speechsdk.SpeechConfig] = None

    def _ensure_config(self):
        """Lazily create and cache the SpeechConfig."""
        if self._speech_config is None:
            self._speech_config = _get_speech_config()

    def acquire(self) -> speechsdk.SpeechSynthesizer:
        """Get a synthesizer from the pool, or create a new one."""
        with self._lock:
            if self._pool:
                return self._pool.pop()

        self._ensure_config()
        # The SDK's native constructor is not safe to run in several threads at once: with a starved
        # CPU (Render's free plan) three at once segfaulted the whole server. Build one at a time.
        with self._build_lock:
            return speechsdk.SpeechSynthesizer(
                speech_config=self._speech_config,
                audio_config=None,
            )

    def release(self, synthesizer: speechsdk.SpeechSynthesizer):
        """Return a healthy synthesizer to the pool for reuse (excess ones are discarded)."""
        with self._lock:
            if len(self._pool) < self._max_size:
                self._pool.append(synthesizer)

    def warm(self, count: int = 1) -> int:
        """
        Make `count` synthesizers ready AND connected; returns how many connected.

        Idempotent and cheap to repeat: the page calls this when the user starts
        typing so the handshake is already paid for when they press the button.
        Each synthesizer is built and connected in its own thread (building one takes
        ~0.8 s, so doing them one after another would triple the wait).
        """
        self._ensure_config()
        count = max(1, min(count, self._max_size))

        def ready_and_connected(_) -> bool:
            synth = self.acquire()
            try:
                # One short sound opens the connection the same way a real request would.
                # (Connection.open() is the obvious call but it segfaults the whole process
                # on Linux when the CPU is starved — Render's free plan — so it is not used.)
                result = synth.speak_ssml_async(_WARMUP_SSML).get()
                connected = result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted
            except Exception as err:
                logger.warning("Could not pre-open the Azure connection: %s", err)
                connected = False
            self.release(synth)
            return connected

        with ThreadPoolExecutor(max_workers=count) as pool:
            opened = sum(pool.map(ready_and_connected, range(count)))
        logger.info("Warmed %d synthesizer(s); %d connection(s) open", count, opened)
        return opened

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._pool)


_synth_pool = _SynthesizerPool(max_size=max(1, TTS_POOL_SIZE))


@sync_retry(
    max_retries=2,
    base_delay=2.0,
    retryable_exceptions=(ConnectionError, TimeoutError, OSError),
)
def _azure_tts_call(synthesizer, ssml):
    """Perform the Azure TTS network call with retry on transient errors."""
    result = synthesizer.speak_ssml_async(ssml).get()
    # Raise on cancellation due to connection errors so retry kicks in
    if result.reason == speechsdk.ResultReason.Canceled:
        cd = result.cancellation_details
        if cd.reason == speechsdk.CancellationReason.Error and cd.error_code in (
            speechsdk.CancellationErrorCode.ConnectionFailure,
            speechsdk.CancellationErrorCode.ServiceTimeout,
            speechsdk.CancellationErrorCode.ServiceUnavailable,
        ):
            raise ConnectionError(f"Azure TTS transient error: {cd.error_code}. {cd.error_details}")
    return result


def _speak_ssml(ssml):
    """One synthesis call on a pooled synthesizer; the synthesizer goes back only if it worked."""
    synthesizer = _synth_pool.acquire()
    ok = False
    try:
        result = _azure_tts_call(synthesizer, ssml)
        ok = result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted
        return result
    finally:
        if ok:
            _synth_pool.release(synthesizer)
        else:
            logger.warning("Discarding synthesizer after failed TTS call")  # its connection may be broken


# ── Speaking a script in pieces ─────────────────────────────────────
# One Azure call renders a 200-word script in 3–5 s and nothing can play until it
# returns. So the script is cut into pieces — the first one a single sentence —
# and the pieces are rendered in parallel (bounded by the synthesizer pool).
# They are handed back in order as they finish and the browser plays them back
# to back, so the first words are heard about a second after the first sentence
# exists. Because the cutter is incremental, that sentence can be the first one
# the *model* has written, while it is still writing the rest.

FIRST_PIECE_MIN_CHARS = 40
PIECE_TARGET_CHARS = (FIRST_PIECE_MIN_CHARS, 220, 380)   # a short first piece, then bigger ones
PARAGRAPH_BREAK = '<break time="400ms"/> '
PIPELINE_SPAN = "Speech Synthesis Pipeline"

_SPLIT_TAG_RE = re.compile(r"<[^<>]*>|<|[^<]+")      # total: no character is ever skipped
_OPENING_TAGS = ("prosody", "emphasis", "p", "s")
_SENT_END_RE = re.compile(r"[.!?…]+[\"”’)\]]*(?=\s)")
_CLOSE_AFTER_SENTENCE_RE = re.compile(r"[.!?…][\"”’)\]]*\s*</[^>]+>$")
_PARAGRAPH_RE = re.compile(r"\n[ \t]*\n\s*")


def _scan(text: str) -> tuple:
    """
    Split `text` into complete sentences plus the unfinished remainder.

    A sentence ends at . ! ? … followed by whitespace, or at a closing tag that
    ends one — and only at tag depth 0, so a <prosody> span never straddles two
    sentences. The remainder keeps its original whitespace so more text can be
    appended to it later.
    """
    # A tag can arrive in pieces ("<pros", "ody rate=…>"): hold an unfinished trailing tag
    # back rather than treating it as text, or the model's annotation would be spoken aloud.
    partial = ""
    cut = text.rfind("<")
    if cut != -1 and ">" not in text[cut:] and len(text) - cut <= 160:
        text, partial = text[:cut], text[cut:]
    out, buf, depth = [], "", 0
    for m in _SPLIT_TAG_RE.finditer(text):
        tok = m.group(0)
        if tok.startswith("<") and tok.endswith(">"):
            name = re.match(r"</?\s*([A-Za-z]+)", tok)
            tag = name.group(1).lower() if name else ""
            if tag in _OPENING_TAGS and not tok.endswith("/>"):
                depth = max(0, depth + (-1 if tok.startswith("</") else 1))
            buf += tok
            if depth == 0 and tok.startswith("</") and _CLOSE_AFTER_SENTENCE_RE.search(buf):
                out.append(buf.strip())
                buf = ""
            continue
        if depth > 0:
            buf += tok
            continue
        pos = 0
        for end in _SENT_END_RE.finditer(tok):
            buf += tok[pos:end.end()]
            out.append(buf.strip())
            buf, pos = "", end.end()
        buf += tok[pos:]
    return [s for s in out if s], buf + partial


def _visible_len(text: str) -> int:
    """Length of the words that will actually be spoken (tags and notes excluded)."""
    return len(re.sub(r"<[^>]*>|\[[^\]]*\]", "", text).strip())


class PieceCutter:
    """
    Cut an annotated script into speakable pieces, incrementally.

    feed() takes text as it arrives and returns the pieces that became ready;
    flush() returns the rest. Piece sizes grow (PIECE_TARGET_CHARS): the first is
    about one sentence so audio starts fast, later ones are bigger to keep
    prosody natural and round-trips few. A paragraph break always ends a piece
    and starts the next with a pause.
    """

    def __init__(self, targets=PIECE_TARGET_CHARS):
        self._targets = targets
        self._tail = ""                 # received but not yet part of a finished sentence
        self._cur = ""                  # the piece being assembled
        self._made = 0                  # pieces handed out so far
        self._new_paragraph = False     # the next piece opens a paragraph

    def feed(self, text: str) -> list:
        self._tail += text
        return self._cut(final=False)

    def flush(self) -> list:
        return self._cut(final=True)

    def _target(self) -> int:
        return self._targets[min(self._made, len(self._targets) - 1)]

    def _take(self) -> str:
        piece, self._cur = self._cur.strip(), ""
        if not piece:
            return ""
        if self._new_paragraph:
            piece, self._new_paragraph = PARAGRAPH_BREAK + piece, False
        self._made += 1
        return piece

    def _add(self, sentence: str, out: list) -> None:
        self._cur = f"{self._cur} {sentence}".strip() if self._cur else sentence
        if _visible_len(self._cur) >= self._target():
            out.append(self._take())

    def _cut(self, final: bool) -> list:
        out: list = []
        while True:                                       # paragraph breaks are boundaries of their own
            m = _PARAGRAPH_RE.search(self._tail)
            if not m:
                break
            paragraph, self._tail = self._tail[:m.start()], self._tail[m.end():]
            sentences, rest = _scan(paragraph)
            for sentence in sentences + ([rest.strip()] if rest.strip() else []):
                self._add(sentence, out)
            piece = self._take()
            if piece:
                out.append(piece)
            self._new_paragraph = True
        sentences, self._tail = _scan(self._tail)
        for sentence in sentences:
            self._add(sentence, out)
        if final:
            if self._tail.strip():
                self._add(self._tail.strip(), out)
            self._tail = ""
            piece = self._take()
            if piece:
                out.append(piece)
        return out


def split_script(tts_script: str, targets=PIECE_TARGET_CHARS) -> list:
    """Cut a whole script at once (the same logic the stream uses)."""
    cutter = PieceCutter(targets)
    return cutter.feed(tts_script or "") + cutter.flush()


def _render_piece(index: int, piece: str, voice: str, valid_style, degree: float, annotate: bool) -> dict:
    """Synthesize one piece in a worker thread; returns the bytes and its timings."""
    t0 = time.perf_counter()
    clean = strip_all_ssml(piece)
    plain_fragment = _escape_text(clean)
    if annotate:
        fragment, direction = direct_tts_script(piece)
    else:
        fragment, direction = plain_fragment, {"fallback": False, "tags_kept": 0, "notes": 0, "notes_dropped": 0,
                                               "tags_dropped": 0, "clamped": 0, "repaired": 0}
    directed = annotate and not direction.get("fallback") and fragment != plain_fragment
    info = {"index": index, "chars": len(clean), "directed": directed, "direction": direction,
            "ok": False, "bytes": b"", "duration": 0.0, "started": t0, "ended": t0, "error": None}
    if not clean:
        info["ended"] = time.perf_counter()
        return info
    try:
        ssml = build_ssml(clean_text=fragment, voice=voice, speaking_style=valid_style, style_degree=degree)
        result = _speak_ssml(ssml)
        if result.reason == speechsdk.ResultReason.Canceled and directed:
            logger.warning("Azure rejected directed piece %d — retrying plain.", index + 1)
            ssml = build_ssml(clean_text=plain_fragment, voice=voice, speaking_style=valid_style, style_degree=degree)
            result = _speak_ssml(ssml)
            info["directed"] = False
        if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
            info.update(ok=True, bytes=result.audio_data,
                        duration=result.audio_duration.total_seconds() if result.audio_duration else 0.0)
        else:
            cd = getattr(result, "cancellation_details", None)
            info["error"] = f"{getattr(cd, 'reason', result.reason)}: {getattr(cd, 'error_details', '')}".strip(": ")
    except Exception as err:
        info["error"] = f"{type(err).__name__}: {err}"
    info["ended"] = time.perf_counter()
    return info


async def synthesize_pieces(pieces: AsyncIterator[str], voice=DEFAULT_VOICE, target_style=None,
                            annotate=True, use_style=True, base_name=None):
    """
    Async generator over a stream of script pieces (see PieceCutter).

    Pieces are rendered in parallel as they arrive — up to the pool size — and
    yielded in order: ("segment", info) as each one's MP3 lands, then
    ("done", summary) with the concatenated file. Per-piece timings are
    recorded in the request's trace under PIPELINE_SPAN (worker threads must
    not touch the profiler's span stack, so they report back and the event
    loop records them).
    """
    if use_style:
        style, degree = infer_speaking_style(target_style) if target_style else ("friendly", 0.8)
        valid_style = get_valid_style_for_voice(voice, style)
    else:
        valid_style, degree = None, 1.0

    base = base_name or f"cadence_coach_{uuid.uuid4().hex[:8]}"
    sem = asyncio.Semaphore(max(1, TTS_POOL_SIZE))
    ordered: asyncio.Queue = asyncio.Queue()      # render tasks, in script order
    all_tasks: list = []
    began = time.perf_counter()

    async def render(i, piece):
        async with sem:
            return await asyncio.to_thread(_render_piece, i, piece, voice, valid_style, degree, annotate)

    async def feed():
        try:
            i = 0
            async for piece in pieces:
                task = asyncio.create_task(render(i, piece))
                all_tasks.append(task)
                await ordered.put(task)
                i += 1
        finally:
            await ordered.put(None)

    feeder = asyncio.create_task(feed())
    parts: list = []
    first_audio_at = None
    try:
        while True:
            task = await ordered.get()
            if task is None:
                break
            info = await task
            i = info["index"]
            add_span(f"TTS piece {i + 1}", info["started"], info["ended"], category="network",
                     parent=PIPELINE_SPAN, depth=1, chars=info["chars"], directed=info["directed"],
                     ok=info["ok"], audio_sec=round(info["duration"], 2), error=info["error"] or "")
            parts.append(info)
            if not info["ok"]:
                yield ("segment", {"index": i, "ok": False, "error": info["error"]})
                continue
            filename = f"{base}_{i + 1:02d}.mp3"
            await asyncio.to_thread((AUDIO_OUTPUT_DIR / filename).write_bytes, info["bytes"])
            if first_audio_at is None:
                first_audio_at = time.perf_counter()
            yield ("segment", {"index": i, "ok": True, "filename": filename, "duration": round(info["duration"], 2),
                               "directed": info["directed"], "chars": info["chars"],
                               "ready_ms": round((time.perf_counter() - began) * 1000, 1)})
    finally:
        feeder.cancel()
        for t in all_tasks:
            if not t.done():
                t.cancel()

    good = [p for p in parts if p["ok"]]
    finished = time.perf_counter()
    if parts:
        add_span(PIPELINE_SPAN, min(p["started"] for p in parts), finished, category="io", parent=None, depth=0,
                 pieces=len(parts), failed=len(parts) - len(good), voice=voice)
    if not good:
        yield ("done", {"success": False, "segments": len(parts),
                        "message": next((p["error"] for p in parts if p["error"]), "Text is empty after processing.")})
        return

    filename = f"{base}.mp3"
    await asyncio.to_thread((AUDIO_OUTPUT_DIR / filename).write_bytes, b"".join(p["bytes"] for p in good))
    direction = {k: sum(int(p["direction"].get(k, 0)) for p in good)
                 for k in ("notes", "notes_dropped", "tags_kept", "tags_dropped", "clamped", "repaired")}
    direction["fallback"] = any(p["direction"].get("fallback") for p in good)
    since_request = elapsed_ms()
    yield ("done", {
        "success": True,
        "file_path": str((AUDIO_OUTPUT_DIR / filename).resolve()),
        "filename": filename,
        "duration": round(sum(p["duration"] for p in good), 2),
        "voice": voice,
        "style": valid_style,
        "directed": any(p["directed"] for p in good),
        "direction": direction,
        "char_count": sum(p["chars"] for p in parts),
        "segments": len(parts),
        "failed_segments": len(parts) - len(good),
        # milliseconds since the request began (so it includes the model's writing time)
        "first_audio_ms": round(since_request - (finished - first_audio_at) * 1000, 1)
        if since_request is not None and first_audio_at else None,
        "message": "Audio synthesized successfully.",
    })


async def speak_script(tts_script: str, voice=DEFAULT_VOICE, target_style=None, annotate=True, use_style=True) -> dict:
    """Render a whole script through the same pipeline and return the summary (the concatenated file)."""
    pieces = split_script(tts_script)

    async def source():
        for piece in pieces:
            yield piece

    result: dict = {}
    async for kind, info in synthesize_pieces(source(), voice=voice, target_style=target_style,
                                              annotate=annotate, use_style=use_style):
        if kind == "done":
            result = info
    return result


def cleanup_audio_output(max_age_hours: float = AUDIO_RETENTION_HOURS) -> int:
    """
    Delete generated MP3s older than `max_age_hours` — nobody replays a
    yesterday's take. Returns the number of files removed. Never raises.
    """
    removed = 0
    now = time.time()
    try:
        for path in AUDIO_OUTPUT_DIR.glob("*.mp3"):
            if now - path.stat().st_mtime > max_age_hours * 3600:
                path.unlink(missing_ok=True)
                removed += 1
    except Exception as err:
        logger.warning("Audio cleanup skipped: %s", err)
    if removed:
        logger.info("Audio cleanup removed %d stale file(s).", removed)
    return removed
