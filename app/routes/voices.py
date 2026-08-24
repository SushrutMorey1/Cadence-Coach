from fastapi import APIRouter
from app.config import VOICES, DEFAULT_VOICE

router = APIRouter()


@router.get("/voices")
async def get_voices():
    return {"available_voices": VOICES, "default_voice": DEFAULT_VOICE}
