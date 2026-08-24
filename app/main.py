from pathlib import Path
import os
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, JSONResponse
from starlette.middleware.sessions import SessionMiddleware
from authlib.integrations.starlette_client import OAuth
from dotenv import load_dotenv

from app.services.database import init_db
from app.services.user_service import upsert_user

from app.services.rate_limiter import rate_limiter, get_user_key
from app.config import AUDIO_OUTPUT_DIR, PROJECT_ROOT, OAUTH_REDIRECT_URI
from app.routes import rewrite, synthesize, voices, coach, transcribe, metrics
from app.routes import stream

load_dotenv()

logger = logging.getLogger(__name__)


# ── Application Lifespan ──
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle using the modern lifespan pattern."""
    # Startup
    init_db()
    logger.info("Database initialized successfully.")
    yield
    # Shutdown (cleanup if needed)
    logger.info("Application shutting down.")


app = FastAPI(
    title="Cadence Coach",
    description=(
        "AI-powered Speech Director & Communication Coach API. "
        "Rewrite text into polished speech scripts, synthesize audio with "
        "Azure Neural TTS, transcribe spoken audio with Whisper, and get "
        "expert coaching feedback — all from one platform."
    ),
    version="2.1.0",
    lifespan=lifespan,
)


# ── Rate-limiting endpoints (only LLM-consuming routes) ──
_RATE_LIMITED_PATHS = {"/api/rewrite", "/api/coach", "/api/rewrite-and-speak",
                      "/api/rewrite/stream", "/api/coach/stream"}


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

# 1. Add session middleware AFTER rate_limit_middleware so it is outermost (LIFO)
#    This ensures the session scope is available when the rate limiter runs.
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SESSION_SECRET_KEY"))

# 2. Configure Google OAuth
oauth = OAuth()
oauth.register(
    name='google',
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)

# API routes
app.include_router(rewrite.router,     prefix="/api", tags=["Rewrite"])
app.include_router(synthesize.router,  prefix="/api", tags=["Synthesize"])
app.include_router(voices.router,      prefix="/api", tags=["Voices"])
app.include_router(coach.router,       prefix="/api", tags=["Coach"])
app.include_router(transcribe.router,  prefix="/api", tags=["Transcribe"])
app.include_router(metrics.router,     prefix="/api", tags=["Metrics"])
app.include_router(stream.router,      prefix="/api", tags=["Streaming"])


@app.get("/api/health")
async def health_check():
    return {"status": "online", "service": "Cadence Coach", "version": "2.1.0"}


# 3. Route to trigger the Google Login Screen
@app.get("/api/auth/login")
async def login(request: Request):
    # This URL must exactly match what you put in the Google Cloud Console
    return await oauth.google.authorize_redirect(request, OAUTH_REDIRECT_URI)

# 4. Route Google hits after a successful login
@app.get("/api/auth/callback")
async def auth_callback(request: Request):
    # Catch the token from Google
    token = await oauth.google.authorize_access_token(request)
    user_info = token.get('userinfo')
    
    # Save the user info into the encrypted session cookie
    if user_info:
        request.session['user'] = dict(user_info)
        email = user_info.get("email")
        if email:
            name = user_info.get("name", "")
            picture = user_info.get("picture", "")
            await upsert_user(email, name, picture)
        
    # Send them back to your main frontend dashboard
    return RedirectResponse(url="/")

# 5. Route for your frontend to check if someone is currently logged in
@app.get("/api/auth/me")
async def check_auth(request: Request):
    user = request.session.get('user')
    if user:
        return {"authenticated": True, "user": user}
    return {"authenticated": False}


# 6. Logout — destroy the session
@app.post("/api/auth/logout")
async def logout(request: Request):
    """Clear the session cookie and log the user out."""
    request.session.clear()
    return {"authenticated": False, "message": "Logged out successfully."}


# 7. Token usage status for the authenticated user
@app.get("/api/auth/usage")
async def token_usage(request: Request):
    """Return the current user's token consumption within the rate-limit window."""
    user_key = get_user_key(request)
    return await rate_limiter.get_usage(user_key)


# Serve audio files
app.mount(
    "/audio",
    StaticFiles(directory=str(AUDIO_OUTPUT_DIR)),
    name="audio",
)

# Serve frontend (local dev — Nginx handles this in production)
FRONTEND_DIR = PROJECT_ROOT / "frontend"
if FRONTEND_DIR.is_dir():
    app.mount(
        "/",
        StaticFiles(directory=str(FRONTEND_DIR), html=True),
        name="frontend",
    )
