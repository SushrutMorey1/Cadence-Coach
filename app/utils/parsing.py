"""
parsing.py — Shared LLM Output Parsing Utilities
====================================================
Centralised helpers for extracting structured data from
raw LLM responses (e.g. stripping markdown fences).
"""

import re
import json


def parse_llm_json(raw_output: str) -> dict:
    """Strip markdown code fences, think tags, and parse JSON from LLM output.

    Handles common LLM formatting issues:
    - Thought/reasoning tags (<think>...</think>)
    - Markdown code fences (```json ... ```)
    - Surrounding prose or conversational preambles
    - Smart/curly quotes (“ ” ‘ ’)
    - Trailing commas before closing braces/brackets
    - Unescaped control characters inside strings (strict=False)
    """
    if not raw_output or not raw_output.strip():
        raise json.JSONDecodeError("Empty output from LLM", raw_output or "", 0)

    cleaned = raw_output.strip()

    # 1. Remove reasoning / thought blocks
    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", cleaned, flags=re.IGNORECASE).strip()

    # 2. Strip markdown code fences
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    cleaned = cleaned.strip()

    # 3. Extract the outermost JSON object
    match = re.search(r"(\{[\s\S]*\})", cleaned)
    if match:
        cleaned = match.group(1)

    # 4. Normalize smart quotes (replace curly double quotes with single quotes to avoid closing JSON strings prematurely)
    cleaned = cleaned.replace("“", "'").replace("”", "'").replace("‘", "'").replace("’", "'")

    # 5. Remove trailing commas before } or ]
    cleaned = re.sub(r",\s*([\}\]])", r"\1", cleaned)

    try:
        return json.loads(cleaned, strict=False)
    except json.JSONDecodeError:
        # Secondary fallback: fix unescaped internal newlines inside strings
        try:
            def sanitize_newlines(m):
                return m.group(0).replace("\n", "\\n").replace("\r", "")
            repaired = re.sub(r'"[^"\\]*(?:\\.[^"\\]*)*"', sanitize_newlines, cleaned)
            return json.loads(repaired, strict=False)
        except Exception:
            # Re-raise original error if repair fails
            return json.loads(cleaned, strict=False)

