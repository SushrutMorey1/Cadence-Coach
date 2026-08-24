"""
Test script for the TTS pipeline.

Usage:
  1. Start the server:  python run.py
  2. Run this script:   python -m tests.test_tts

Tests three endpoints:
  - /api/voices           → list available voices
  - /api/synthesize       → direct TTS from a tts_script
  - /api/rewrite-and-speak → full pipeline (rewrite + TTS)
"""

import requests
import json
import sys

BASE = "http://localhost:8000"


def test_voices():
    print("=" * 60)
    print("TEST 1: GET /api/voices")
    print("=" * 60)
    r = requests.get(f"{BASE}/api/voices", timeout=10)
    print(f"Status: {r.status_code}")
    print(json.dumps(r.json(), indent=2))
    print()


def test_synthesize():
    print("=" * 60)
    print("TEST 2: POST /api/synthesize (direct TTS)")
    print("=" * 60)

    sample_tts_script = (
        '[deep breath] <prosody pitch="low" rate="slow">'
        'Today, <break time="300ms"/> '
        'we don\'t just <emphasis level="strong">launch</emphasis> a product.'
        '</prosody> '
        '<break time="500ms"/> '
        '[voice rises] '
        '<prosody pitch="high" rate="medium" volume="loud">'
        'We ignite a <emphasis level="strong">revolution</emphasis>.'
        '</prosody> '
        '<break time="750ms"/> '
        '[controlled intensity] '
        '<prosody pitch="low" rate="x-slow" volume="x-loud">'
        'And revolutions? They don\'t ask for permission.'
        '</prosody> '
        '<break time="1000ms"/>'
    )

    payload = {
        "tts_script": sample_tts_script,
        "voice": "male_professional",
    }

    print(f"Sending tts_script ({len(sample_tts_script)} chars) ...")
    r = requests.post(f"{BASE}/api/synthesize", json=payload, timeout=120)

    if r.status_code == 200:
        output_file = "test_synthesized.mp3"
        with open(output_file, "wb") as f:
            f.write(r.content)
        duration = r.headers.get("X-Audio-Duration", "unknown")
        print(f"✅ Audio saved to: {output_file}")
        print(f"   Duration: {duration}s")
        print(f"   File size: {len(r.content):,} bytes")
    else:
        print(f"❌ Error: {r.status_code}")
        print(json.dumps(r.json(), indent=2))
    print()


def test_rewrite_and_speak():
    print("=" * 60)
    print("TEST 3: POST /api/rewrite-and-speak (full pipeline)")
    print("=" * 60)

    payload = {
        "user_text": (
            "We are launching a new product next week. "
            "It will help people save time and money. "
            "Our team has worked really hard on this."
        ),
        "target_style": "Inspirational TED Talk speaker",
        "voice": "male_professional",
    }

    print("Sending rewrite-and-speak request ...")
    r = requests.post(f"{BASE}/api/rewrite-and-speak", json=payload, timeout=120)
    print(f"Status: {r.status_code}")

    if r.status_code == 200:
        data = r.json()
        print(f"\n📝 Display Script:\n{'-'*40}")
        print(data["display_script"][:500])
        print(f"\n🔊 Audio Result:\n{'-'*40}")
        audio = data["audio"]
        print(f"  Success:   {audio['success']}")
        print(f"  Voice:     {audio['voice']}")
        print(f"  Duration:  {audio.get('duration', 'N/A')}s")
        print(f"  Message:   {audio['message']}")
    else:
        print(json.dumps(r.json(), indent=2))
    print()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        test_name = sys.argv[1]
        {"voices": test_voices, "synthesize": test_synthesize, "full": test_rewrite_and_speak}.get(
            test_name, lambda: print(f"Unknown: {test_name}. Use: voices|synthesize|full")
        )()
    else:
        test_voices()
        test_synthesize()
        test_rewrite_and_speak()
