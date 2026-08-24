import re
import uuid
import asyncio
import logging
from typing import Optional

import azure.cognitiveservices.speech as speechsdk

from app.config import (
    AZURE_SPEECH_KEY,
    AZURE_SPEECH_REGION,
    AUDIO_OUTPUT_DIR,
    VOICES,
    DEFAULT_VOICE,
)
from app.services.profiler import trace, sizeof_fmt
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


def add_paragraph_pauses(text: str) -> str:
    """Add break pauses between paragraphs."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if len(paragraphs) <= 1:
        return text
    return ' <break time="350ms"/> '.join(paragraphs)


def build_ssml(clean_text, voice, speaking_style=None, style_degree=1.0, lang="en-US"):
    """Build the final SSML document with optional express-as wrapping."""
    text_with_pauses = add_paragraph_pauses(clean_text)

    # Escape ampersands for XML
    text_with_pauses = re.sub(r"&(?!amp;|lt;|gt;|apos;|quot;)", "&amp;", text_with_pauses)

    if speaking_style:
        inner = (
            f'<mstts:express-as style="{speaking_style}" '
            f'styledegree="{style_degree:.1f}">'
            f'{text_with_pauses}'
            f'</mstts:express-as>'
        )
    else:
        inner = text_with_pauses

    ssml = (
        f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis"'
        f' xmlns:mstts="https://www.w3.org/2001/mstts" xml:lang="{lang}">'
        f'<voice name="{voice}">'
        f'{inner}'
        f'</voice></speak>'
    )
    return ssml


def prepare_for_synthesis(text, voice=DEFAULT_VOICE, target_style=None):
    """Full prep pipeline: strip SSML -> infer style -> validate -> build SSML."""
    clean_text = strip_all_ssml(text)
    if not clean_text:
        return ""

    if target_style:
        style, degree = infer_speaking_style(target_style)
    else:
        style, degree = ("friendly", 0.8)

    valid_style = get_valid_style_for_voice(voice, style)

    logger.info(
        "TTS: voice=%s, style=%s->%s (degree=%.1f), len=%d",
        voice, style, valid_style or "(none)", degree, len(clean_text),
    )

    return build_ssml(
        clean_text=clean_text,
        voice=voice,
        speaking_style=valid_style,
        style_degree=degree,
    )


def _get_speech_config(output_format=None):
    """Create Azure SpeechConfig."""
    if not AZURE_SPEECH_KEY:
        raise ValueError("AZURE_SPEECH_KEY not found. Set it in .env.")

    config = speechsdk.SpeechConfig(
        subscription=AZURE_SPEECH_KEY,
        region=AZURE_SPEECH_REGION,
    )
    if output_format is None:
        output_format = speechsdk.SpeechSynthesisOutputFormat.Webm24Khz16BitMonoOpus
    config.set_speech_synthesis_output_format(output_format)
    return config


def resolve_voice(voice_input):
    """Map an alias like 'davis' to a full Azure voice name."""
    if voice_input is None:
        return DEFAULT_VOICE
    return VOICES.get(voice_input, voice_input)


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


def synthesize_to_file(tts_script, voice=DEFAULT_VOICE, target_style=None, filename=None, output_format=None):
    """Text -> clean -> express-as -> Azure TTS -> audio file. Deep profiled."""

    # Step 1: Strip SSML tags from input
    with trace("Strip SSML Tags", category="compute") as span:
        clean_text = strip_all_ssml(tts_script)
        span["meta"]["input_chars"] = len(tts_script)
        span["meta"]["clean_chars"] = len(clean_text)
        span["meta"]["stripped_chars"] = len(tts_script) - len(clean_text)

    if not clean_text:
        return {"success": False, "file_path": None, "duration": None, "message": "Text is empty after processing."}

    # Step 2: Infer speaking style
    with trace("Infer Speaking Style", category="compute",
                target_style=target_style or "(none)") as span:
        if target_style:
            style, degree = infer_speaking_style(target_style)
        else:
            style, degree = ("friendly", 0.8)
        valid_style = get_valid_style_for_voice(voice, style)
        span["meta"]["inferred_style"] = style
        span["meta"]["valid_style"] = valid_style or "(none — unsupported)"
        span["meta"]["style_degree"] = degree
        span["meta"]["voice"] = voice

    # Step 3: Build SSML XML
    with trace("Build SSML XML", category="compute") as span:
        ssml = build_ssml(
            clean_text=clean_text,
            voice=voice,
            speaking_style=valid_style,
            style_degree=degree,
        )
        span["meta"]["ssml_chars"] = len(ssml) if ssml else 0
        span["meta"]["ssml_size"] = sizeof_fmt(len(ssml.encode("utf-8"))) if ssml else "0 B"

    if not ssml:
        return {"success": False, "file_path": None, "duration": None, "message": "SSML generation failed."}

    # Step 4: Initialize Azure Speech Config
    with trace("Azure Speech Config Init", category="io") as span:
        speech_config = _get_speech_config(output_format)
        span["meta"]["region"] = AZURE_SPEECH_REGION
        span["meta"]["output_format"] = str(output_format or "Webm24Khz16BitMonoOpus")

    # Step 5: Initialize Synthesizer
    with trace("Azure Synthesizer Init", category="io") as span:
        if filename is None:
            filename = f"cadence_coach_{uuid.uuid4().hex[:8]}.webm"
        file_path = AUDIO_OUTPUT_DIR / filename
        audio_config = speechsdk.audio.AudioOutputConfig(filename=str(file_path))
        synthesizer = speechsdk.SpeechSynthesizer(
            speech_config=speech_config,
            audio_config=audio_config,
        )
        span["meta"]["output_file"] = filename
        span["meta"]["output_path"] = str(file_path)

    # Step 6: Azure TTS Network Call (with retry on transient errors)
    with trace("Azure TTS Network Call", category="network",
                voice=voice, style=valid_style or "default") as span:
        result = _azure_tts_call(synthesizer, ssml)
        span["meta"]["result_reason"] = str(result.reason)

    if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
        duration = result.audio_duration.total_seconds() if result.audio_duration else None
        logger.info("Audio saved to %s (%.1fs)", file_path, duration or 0)
        return {
            "success": True,
            "file_path": str(file_path.resolve()),
            "filename": filename,
            "duration": duration,
            "message": "Audio synthesized successfully.",
            "voice": voice,
            "char_count": len(clean_text),
        }

    elif result.reason == speechsdk.ResultReason.Canceled:
        cd = result.cancellation_details
        msg = f"Speech synthesis canceled: {cd.reason}. "
        if cd.reason == speechsdk.CancellationReason.Error:
            msg += f"Error: {cd.error_code}. {cd.error_details}"
        logger.error(msg)
        return {
            "success": False, "file_path": None, "duration": None, "message": msg,
            "voice": voice, "char_count": len(clean_text),
        }

    return {
        "success": False, "file_path": None, "duration": None,
        "message": f"Unexpected: {result.reason}",
        "voice": voice, "char_count": len(clean_text),
    }


async def async_synthesize_to_file(tts_script, voice=DEFAULT_VOICE, target_style=None, filename=None, output_format=None):
    """Async wrapper: runs TTS synthesis in a thread pool, then logs metrics asynchronously."""
    from app.services.metrics import log_tts_call

    result = await asyncio.to_thread(
        synthesize_to_file, tts_script, voice, target_style, filename, output_format
    )

    # Log TTS metrics asynchronously (not blocking)
    tts_voice = result.get("voice", voice)
    char_count = result.get("char_count", 0)
    if result["success"]:
        await log_tts_call(voice=tts_voice, char_count=char_count, duration_sec=result.get("duration"), status="success")
    else:
        await log_tts_call(voice=tts_voice, char_count=char_count, status=f"failed: {result.get('message', 'unknown')}")

    return result
