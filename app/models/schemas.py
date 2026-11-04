from typing import Optional, List
from pydantic import AliasChoices, BaseModel, Field


# Rewrite models

class RewriteRequest(BaseModel):
    """What the client sends to /api/rewrite."""
    user_text: str = Field(
        ...,
        min_length=1,
        max_length=50_000,
        description="Raw text to rewrite OR a command/instruction to generate content from scratch.",
        examples=[
            "Today we launch a product that will change the industry.",
            "Write a speech for Gandhi Jayanti at school",
        ],
    )
    target_style: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="The speaking persona, tone, or stylistic archetype.",
        examples=["Inspirational TED Talk speaker"],
    )


# Synthesize models

class SynthesizeRequest(BaseModel):
    """Direct TTS synthesis from text."""
    tts_script: str = Field(
        ...,
        min_length=1,
        max_length=50_000,
        description="The text to synthesize into audio.",
    )
    voice: Optional[str] = Field(
        default=None,
        description=(
            "Voice alias ('davis', 'aria', 'jenny', 'guy', "
            "'sara', 'jason') or full Azure voice name."
        ),
    )
    target_style: Optional[str] = Field(
        default=None,
        description=(
            "Speaking style description (e.g., 'TED Talk speaker'). "
            "Used to select the Azure speaking persona."
        ),
    )
    plain: bool = Field(
        default=False,
        description="Flat read: ignore all voice direction and speaking style (for A/B comparison).",
    )


# Rewrite + Speak (end-to-end)

class RewriteAndSpeakRequest(BaseModel):
    """End-to-end: rewrite + synthesize in one call."""
    user_text: str = Field(
        ...,
        min_length=1,
        max_length=50_000,
        description="Raw text to rewrite OR a command/instruction to generate content from scratch.",
        examples=[
            "Today we launch a product that will change the industry.",
            "Write a speech for Gandhi Jayanti at school",
        ],
    )
    target_style: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="The speaking persona, tone, or stylistic archetype.",
        examples=["Inspirational TED Talk speaker"],
    )
    voice: Optional[str] = Field(
        default=None,
        max_length=100,
        description="Azure neural voice name or alias.",
    )


# Communication Coach models

class CoachRequest(BaseModel):
    """What the client sends to /api/coach."""
    user_text: str = Field(
        ...,
        min_length=1,
        max_length=50_000,
        description="The speech or spoken text to analyze.",
        examples=["Today we launch a product that will change the industry."],
    )
    context: Optional[str] = Field(
        default="Not provided — infer from the text.",
        max_length=1_000,
        description=(
            "Optional context: audience, occasion, skill level. "
            "Helps tailor coaching feedback."
        ),
        examples=["Audience: investors. Occasion: Series A pitch. Level: intermediate."],
    )
    speech_metrics: Optional[dict] = Field(
        default=None,
        description=(
            "Measured delivery data from /api/transcribe "
            "(wpm, duration_sec, word_count, filler_count, filler_rate_pct, filler_words, "
            "long_pauses, longest_pause_sec, fastest_stretch). "
            "When present the coach cites the numbers."
        ),
    )
    source: Optional[str] = Field(
        default=None, max_length=16,
        description="Where the text came from: 'text' or 'audio'. Stored with the result.",
    )


class WeakWord(BaseModel):
    """A weak word with its suggested replacement."""
    word: str
    suggestion: str
    reason: str


class VocabularyAnalysis(BaseModel):
    """Vocabulary coaching feedback."""
    score: float
    strengths: List[str]
    improvements: List[str]
    weak_words: List[WeakWord]
    power_words_used: List[str]


class ToneAndEmotion(BaseModel):
    """Tone and emotional arc analysis."""
    score: float
    detected_tone: str
    emotional_arc: str
    feedback: List[str]


class ModulationSuggestion(BaseModel):
    """A single voice modulation coaching point."""
    text_segment: str
    # Models sometimes swap the two look-alike field names; accept either rather than pay for a retry.
    instruction: str = Field(validation_alias=AliasChoices("instruction", "technique"))
    type: str


class VoiceModulation(BaseModel):
    """Voice modulation coaching feedback."""
    score: float
    suggestions: List[ModulationSuggestion]


class PacingAndRhythm(BaseModel):
    """Pacing and rhythm analysis."""
    score: float
    avg_sentence_length: str
    variation_quality: str
    feedback: List[str]


class KeyMoment(BaseModel):
    """A critical delivery moment in the text."""
    text_segment: str
    technique: str = Field(validation_alias=AliasChoices("technique", "instruction"))
    reason: str


class EmphasisAndDelivery(BaseModel):
    """Emphasis and delivery coaching."""
    score: float
    key_moments: List[KeyMoment]


class ClarityAndStructure(BaseModel):
    """Clarity and structural analysis."""
    score: float
    opening_strength: str
    closing_strength: str
    transitions: str
    feedback: List[str]


class CoachResponse(BaseModel):
    """Full coaching analysis result."""
    overall_score: float
    summary: str
    delivery_notes: Optional[str] = ""
    vocabulary: VocabularyAnalysis
    tone_and_emotion: ToneAndEmotion
    voice_modulation: VoiceModulation
    pacing_and_rhythm: PacingAndRhythm
    emphasis_and_delivery: EmphasisAndDelivery
    clarity_and_structure: ClarityAndStructure
    top_3_actions: List[str]
