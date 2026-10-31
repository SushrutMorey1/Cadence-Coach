"""
main.py — Cadence Coach Application
======================================
FastAPI app wiring: lifespan (database, audio cleanup, TTS warm-up, model
check), middleware (rate limiting, sessions, static cache policy), Google
OAuth with a guest fallback, and the routers for the three tools —
Speech Director, Communication Coach, Latency Lab.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, JSONResponse
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware
from authlib.integrations.starlette_client import OAuth

from app.config import (
    AUDIO_OUTPUT_DIR, PROJECT_ROOT, OAUTH_REDIRECT_URI, SESSION_SECRET_KEY,
    GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, AUTH_REQUIRED, AZURE_SPEECH_KEY, TTS_POOL_SIZE,
    LLM_MODEL, WHISPER_MODEL, integration_status,
)
from app.services.database import init_db, dialect
from app.services.user_service import upsert_user
from app.services.rate_limiter import rate_limiter, get_user_key
from app.services.llm import check_model_available, model_status, warm_llm
from app.routes import synthesize, voices, coach, transcribe, metrics, stream

# uvicorn configures its own loggers but leaves the root logger bare, which
# hides every INFO line from `app.*`. Give the root a handler once.
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

logger = logging.getLogger(__name__)

APP_VERSION = "3.1.0"


# ── Application Lifespan ──
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle using the modern lifespan pattern."""
    backend = init_db()
    logger.info("Database initialized (%s).", backend)

    # Generated MP3s are disposable — drop yesterday's takes.
    try:
        from app.services.tts import cleanup_audio_output
        await asyncio.to_thread(cleanup_audio_output)
    except Exception as err:
        logger.warning("Audio cleanup skipped: %s", err)

    # Pay the first-request costs now rather than on the user's first click: the Groq client
    # build + first connection (~1 s) and the Azure connections (~0.4 s on the first sentence).
    # They are independent, so they run together. Tests and offline runs set STARTUP_CHECKS=0.
    if os.getenv("STARTUP_CHECKS", "1") != "0":
        warmups = [check_model_available(), warm_llm()]
        if AZURE_SPEECH_KEY:
            from app.services.tts import _synth_pool
            warmups.append(asyncio.to_thread(_synth_pool.warm, TTS_POOL_SIZE))
        try:                                        # never let a slow warm-up hold the server hostage
            for outcome in await asyncio.wait_for(asyncio.gather(*warmups, return_exceptions=True), timeout=20):
                if isinstance(outcome, Exception):
                    logger.warning("Startup warm-up step skipped: %s", outcome)
        except asyncio.TimeoutError:
            logger.warning("Startup warm-ups were still running after 20 s — continuing without waiting for them.")

    logger.info("Cadence Coach v%s ready. Model: %s · Whisper: %s · Integrations: %s",
                APP_VERSION, LLM_MODEL, WHISPER_MODEL, integration_status())
    yield
    logger.info("Application shutting down.")


app = FastAPI(
    title="Cadence Coach",
    description=(
        "Three tools for people who have to speak well. The VOXIS Speech Director rewrites "
        "text into a script written for the ear and performs it with directed Azure Neural "
        "voices; the Communication Coach scores a speech on six dimensions, grounded in the "
        "measured delivery of a recording; the Latency Lab shows every hop of every call."
    ),
    version=APP_VERSION,
    lifespan=lifespan,
)


# ── Rate-limiting endpoints (LLM-consuming HTTP routes) ──
_RATE_LIMITED_PATHS = {
    "/api/rewrite", "/api/coach", "/api/rewrite-and-speak",
    "/api/rewrite/stream", "/api/rewrite-and-speak/stream", "/api/coach/stream",
}


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """Enforce per-user token budget on LLM-consuming endpoints."""
    if request.url.path in _RATE_LIMITED_PATHS:
        user_key = get_user_key(request)
        allowed, remaining, resets_in = await rate_limiter.check(user_key)

        if not allowed:
            logger.warning(
                "Rate limit exceeded: user=%s path=%s resets_in=%.0fs",
                user_key, request.url.path, resets_in,
            )
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Token budget exhausted. Please wait before making more requests.",
                    "tokens_remaining": 0,
                    "resets_in_seconds": round(resets_in, 1),
                },
                headers={"Retry-After": str(int(resets_in) + 1)},
            )

        response = await call_next(request)
        # Attach remaining budget in response headers for frontend awareness
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Limit"] = str(rate_limiter.max_tokens)
        return response

    return await call_next(request)


@app.middleware("http")
async def revalidate_static_middleware(request: Request, call_next):
    """
    Always revalidate the app's own files: the browser keeps its copy but asks
    first, and StaticFiles answers 304 (ETag) when nothing changed. That is one
    tiny round-trip instead of re-downloading ~120 KB, and an edited file is
    never served stale.
    """
    response = await call_next(request)
    if request.url.path.endswith((".js", ".css", ".html")) or request.url.path == "/":
        response.headers["Cache-Control"] = "no-cache"
    return response


# 1. Session middleware is added AFTER the custom middlewares so it is outermost (LIFO):
#    the session scope is populated before the rate limiter runs.
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET_KEY, same_site="lax")

# 2. Google OAuth (only registered when credentials exist; otherwise guest mode)
oauth = OAuth()
if AUTH_REQUIRED:
    oauth.register(
        name="google",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )

# API routes
app.include_router(synthesize.router,  prefix="/api", tags=["Synthesize"])
app.include_router(voices.router,      prefix="/api", tags=["Voices"])
app.include_router(coach.router,       prefix="/api", tags=["Coach"])
app.include_router(transcribe.router,  prefix="/api", tags=["Transcribe"])
app.include_router(metrics.router,     prefix="/api", tags=["Metrics"])
app.include_router(stream.router,      prefix="/api", tags=["Streaming"])


@app.get("/api/health")
async def health_check():
    return {
        "status": "online",
        "service": "Cadence Coach",
        "version": APP_VERSION,
        "db": dialect(),
        "auth_required": AUTH_REQUIRED,
        "integrations": integration_status(),
        "llm": model_status(),
        "whisper_model": WHISPER_MODEL,
    }


# 3. Route to trigger the Google Login Screen
@app.get("/api/auth/login")
async def login(request: Request):
    if not AUTH_REQUIRED:
        return RedirectResponse(url="/")
    # This URL must exactly match what you put in the Google Cloud Console
    return await oauth.google.authorize_redirect(request, OAUTH_REDIRECT_URI)


# 4. Route Google hits after a successful login
@app.get("/api/auth/callback")
async def auth_callback(request: Request):
    if not AUTH_REQUIRED:
        return RedirectResponse(url="/")
    token = await oauth.google.authorize_access_token(request)
    user_info = token.get("userinfo")

    if user_info:
        request.session["user"] = dict(user_info)
        email = user_info.get("email")
        if email:
            await upsert_user(email, user_info.get("name", ""), user_info.get("picture", ""))

    return RedirectResponse(url="/")


# 5. Route for the frontend to check who is logged in
@app.get("/api/auth/me")
async def check_auth(request: Request):
    user = request.session.get("user")
    if user:
        return {"authenticated": True, "guest": False, "user": user}
    if not AUTH_REQUIRED:
        return {"authenticated": True, "guest": True, "user": {"name": "Guest", "email": None, "picture": None}}
    return {"authenticated": False, "guest": False}


# 6. Logout — destroy the session
@app.post("/api/auth/logout")
async def logout(request: Request):
    request.session.clear()
    return {"authenticated": not AUTH_REQUIRED, "message": "Logged out successfully."}


# 7. Token usage status for the current user
@app.get("/api/auth/usage")
async def token_usage(request: Request):
    return await rate_limiter.get_usage(get_user_key(request))


# Serve generated audio files
app.mount("/audio", StaticFiles(directory=str(AUDIO_OUTPUT_DIR)), name="audio")

# Serve frontend (local dev — Nginx handles this in production). Only this mount is
# gzipped: text compresses ~4x, while MP3s are already compressed and need Range support.
FRONTEND_DIR = PROJECT_ROOT / "frontend"
if FRONTEND_DIR.is_dir():
    app.mount("/", GZipMiddleware(StaticFiles(directory=str(FRONTEND_DIR), html=True), minimum_size=1024), name="frontend")
