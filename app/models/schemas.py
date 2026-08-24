from typing import Optional, List
from pydantic import BaseModel, Field


# Rewrite models

class RewriteRequest(BaseModel):
    """What the client sends to /api/rewrite."""
    user_text: str = Field(
        ...,
        min_length=1,
        max_length=50_000,
        description="The raw baseline text to be rewritten.",
        examples=["Today we launch a product that will change the industry."],
    )
    target_style: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="The speaking persona, tone, or stylistic archetype.",
        examples=["Inspirational TED Talk speaker"],
    )


class LatencyTrace(BaseModel):
    name: str
    latency_ms: float
    category: Optional[str] = "general"
    depth: Optional[int] = 0
    parent: Optional[str] = None
    meta: Optional[dict] = {}

class RewriteResponse(BaseModel):
    """Successful rewrite — two scripts."""
    display_script: str
    tts_script: str
    detailed_latencies: Optional[List[LatencyTrace]] = None


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


class SynthesizeResponse(BaseModel):
    """TTS synthesis result metadata."""
    success: bool
    file_path: Optional[str] = None
    duration: Optional[float] = None
    message: str
    detailed_latencies: Optional[List[LatencyTrace]] = None


# Rewrite + Speak (end-to-end)

class RewriteAndSpeakRequest(BaseModel):
    """End-to-end: rewrite + synthesize in one call."""
    user_text: str = Field(
        ...,
        min_length=1,
        max_length=50_000,
        description="The raw baseline text to be rewritten.",
        examples=["Today we launch a product that will change the industry."],
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


# Error

class ErrorResponse(BaseModel):
    """LLM-detected input error."""
    error: bool
    error_code: str
    message: str


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


class WeakWord(BaseModel):
    """A weak word with its suggested replacement."""
    word: str
    suggestion: str
    reason: str


class VocabularyAnalysis(BaseModel):
    """Vocabulary coaching feedback."""
    score: int
    strengths: List[str]
    improvements: List[str]
    weak_words: List[WeakWord]
    power_words_used: List[str]


class ToneAndEmotion(BaseModel):
    """Tone and emotional arc analysis."""
    score: int
    detected_tone: str
    emotional_arc: str
    feedback: List[str]


class ModulationSuggestion(BaseModel):
    """A single voice modulation coaching point."""
    text_segment: str
    instruction: str
    type: str


class VoiceModulation(BaseModel):
    """Voice modulation coaching feedback."""
    score: int
    suggestions: List[ModulationSuggestion]


class PacingAndRhythm(BaseModel):
    """Pacing and rhythm analysis."""
    score: int
    avg_sentence_length: str
    variation_quality: str
    feedback: List[str]


class KeyMoment(BaseModel):
    """A critical delivery moment in the text."""
    text_segment: str
    technique: str
    reason: str


class EmphasisAndDelivery(BaseModel):
    """Emphasis and delivery coaching."""
    score: int
    key_moments: List[KeyMoment]


class ClarityAndStructure(BaseModel):
    """Clarity and structural analysis."""
    score: int
    opening_strength: str
    closing_strength: str
    transitions: str
    feedback: List[str]


class CoachResponse(BaseModel):
    """Full coaching analysis result."""
    overall_score: float
    summary: str
    vocabulary: VocabularyAnalysis
    tone_and_emotion: ToneAndEmotion
    voice_modulation: VoiceModulation
    pacing_and_rhythm: PacingAndRhythm
    emphasis_and_delivery: EmphasisAndDelivery
    clarity_and_structure: ClarityAndStructure
    top_3_actions: List[str]
    detailed_latencies: Optional[List[LatencyTrace]] = None
