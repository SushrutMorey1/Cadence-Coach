"""
parsing.py — Shared LLM Output Parsing Utilities
====================================================
Centralised helpers for extracting structured data from
raw LLM responses (e.g. stripping markdown fences).
"""

import re
import json


def parse_llm_json(raw_output: str) -> dict:
    """Strip markdown code fences and parse JSON from LLM output.

    LLMs frequently wrap JSON in ```json ... ``` blocks.
    This helper removes those fences before calling json.loads().
    """
    cleaned = re.sub(r"^```(?:json)?\s*\n?", "", raw_output.strip())
    cleaned = re.sub(r"\n?```\s*$", "", cleaned).strip()
    return json.loads(cleaned)
