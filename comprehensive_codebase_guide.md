# Cadence Coach — Comprehensive Codebase & Architecture Guide

This document provides an exhaustive, deep-dive analysis of the Cadence Coach project. It breaks down the architecture, explains *why* specific coding decisions were made, analyzes the problems solved by these approaches, and discusses alternative implementations.

---

## 1. High-Level Architecture & Design Philosophy

Cadence Coach is built as a **monolithic backend API** with a **decoupled vanilla frontend**, leveraging a modern asynchronous Python stack. 

### Why this architecture?
- **FastAPI (Backend):** Chosen for its native `async/await` support, high performance (running on Starlette/Uvicorn), and automatic OpenAPI (Swagger) documentation via Pydantic schemas. Alternative: *Django* (too heavy, synchronous by default) or *Flask* (requires plugins for async and validation).
- **Vanilla HTML/JS/CSS (Frontend):** Bypasses the complexity of React/Vue build steps for a streamlined, highly performant single-page application (SPA). Alternative: *Next.js/React* (would add complexity but better component reusability for larger teams).
- **MySQL (Database):** Used for persistent metrics, rate-limiting, and user sessions. Chosen for robust relational data integrity. Alternative: *Redis* (better for rate-limiting speed, but worse for long-term analytics storage).

---

## 2. Directory Structure Deep Dive

```text
Cadence Coach/
├── app/                      # Core FastAPI Application
│   ├── main.py               # Application entry point & middleware setup
│   ├── config.py             # Environment variable loading & validation
│   ├── models/
│   │   └── schemas.py        # Pydantic models for API validation
│   ├── routes/               # API endpoint definitions (Controllers)
│   ├── services/             # Core business logic & 3rd party integrations
│   ├── utils/                # Helper functions (retry logic, parsing)
│   └── prompts/              # LLM prompt templates (LangChain)
├── frontend/                 # Client-side static assets
│   ├── index.html            # Main UI structure
│   ├── style.css             # Vanilla CSS styling
│   ├── app.js                # Frontend logic & API orchestration
│   └── nginx.conf            # Production web server config
├── tests/                    # Unit and integration tests
├── .env.example              # Template for environment secrets
├── docker-compose.yml        # Multi-container orchestration
└── render.yaml               # Cloud deployment blueprint
```

---

## 3. Core Application Entry & Middleware (`app/main.py`)

### The `lifespan` Context Manager
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield
```
**Why it’s written this way:** Modern FastAPI deprecates `@app.on_event("startup")`. The `lifespan` generator safely guarantees that the database pool is initialized *before* the application accepts traffic, and provides a clean teardown phase (after the `yield`) when the app shuts down.

### Rate Limiting Middleware
```python
@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    if request.url.path in _RATE_LIMITED_PATHS:
        user_key = get_user_key(request)
        allowed, remaining, resets_in = await rate_limiter.check(user_key)
        # ... logic to block or allow
```
**Problem Solved:** LLM APIs (Groq) and TTS (Azure) cost money. This middleware intercepts HTTP requests *before* they hit the route handlers. It acts as a shield.
**Alternative:** You could put rate limiting inside each route handler (e.g., `Depends(check_rate_limit)`), but middleware enforces it globally across defined paths, preventing accidental exposure of new endpoints.

### Authentication & Sessions (OAuth)
The app uses `authlib` to integrate Google OAuth.
```python
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SESSION_SECRET_KEY"))
```
**Minute Detail:** The `SessionMiddleware` is added *after* the custom rate limiter middleware in the code. Because Starlette middleware executes in a LIFO (Last-In, First-Out) order, this ensures the session is populated *before* the rate limiter checks `request.session.get('user')`.

---

## 4. Service Layer: The Engine of the App

The `app/services/` directory is where the actual work happens. It is heavily optimized for asynchronous execution.

### A. The Database Wrapper (`services/database.py`)
FastAPI is asynchronous, but the `mysql-connector-python` library is **synchronous**. If you run a sync database query in an async FastAPI route, it freezes the entire server until the query finishes.

**The Solution:**
```python
async def async_execute(query: str, params: tuple = ()) -> None:
    await asyncio.to_thread(_sync_execute, query, params)
```
**Why it’s written this way:** `asyncio.to_thread()` offloads the blocking MySQL call to a separate worker thread. This allows the main FastAPI event loop to continue serving other users while waiting for the database.
**Alternative:** Rewrite the DB layer using an async driver like `aiomysql` or SQLAlchemy with `asyncmy`. The `to_thread` approach was chosen as a pragmatic refactor to keep existing sync code intact while fixing the blocking issue.

### B. LLM Orchestration (`services/llm.py`)
Uses `ChatGroq` (LangChain) to interface with the Llama 3 70B model.

**Token Streaming (SSE):**
```python
async def stream_rewrite(...):
    async for chunk in llm.astream(prompt_value):
        yield f"data: {chunk.content}\n\n"
```
**Problem Solved:** LLMs take seconds to generate full responses. Users hate waiting. Server-Sent Events (SSE) yield text chunks as they arrive from Groq, allowing the frontend to create a "typing" effect. 
**Minute Detail:** Notice the `\n\n`. This is the mandatory protocol formatting for SSE. The browser's `EventSource` API relies on this exact formatting to parse incoming chunks.

### C. Text-to-Speech (`services/tts.py`)
Integrates Azure Cognitive Services. 
**Minute Detail - SSML Processing:**
```python
def build_ssml(clean_text, voice, speaking_style=None, style_degree=1.0, lang="en-US"):
    text_with_pauses = re.sub(r"&(?!amp;|lt;|gt;|apos;|quot;)", "&amp;", text_with_pauses)
    # ... builds XML string
```
**Why it’s written this way:** Azure TTS requires Speech Synthesis Markup Language (SSML). If the LLM generates a text containing an unescaped `&` (e.g., "R&D"), Azure's XML parser will crash. The regex specifically escapes `&` only if it isn't already a valid XML entity.

### D. Resiliency (`utils/retry.py`)
```python
def async_retry(max_retries=3, base_delay=1.0, backoff_factor=2.0, jitter=True):
```
**Problem Solved:** External networks (Groq, Azure) fail randomly (502 Bad Gateway, Timeout). If an API fails, the app shouldn't instantly crash.
**Why it’s written this way:** It uses **Exponential Backoff with Jitter**. If it fails, it waits 1s, then 2s, then 4s. The `jitter` (random +/- time) prevents a "thundering herd" problem where multiple failed requests all retry at the exact same millisecond and crash the server again.

---

## 5. Data Validation (`models/schemas.py`)

```python
class RewriteRequest(BaseModel):
    user_text: str = Field(..., min_length=1, max_length=50_000)
    target_style: str = Field(..., min_length=1, max_length=500)
```
**Why it’s written this way:** Pydantic is used to strictly enforce incoming JSON payloads. 
**Problem Solved:** Security and Abuse. Without `max_length`, a malicious user could send a 100-Megabyte string to the `/rewrite` endpoint, causing the server to run out of memory (OOM) or racking up massive Groq API bills. The `50_000` character limit safely accommodates very long speeches while neutralizing abuse vectors.

---

## 6. Frontend Mechanics (`frontend/`)

### `app.js` Orchestration
The frontend is a massive Vanilla JavaScript file (`~50KB`).
- **DOM Manipulation:** Uses standard `document.getElementById` and `classList.add/remove`. 
- **SSE Consumption:** Uses the native browser `EventSource` API (or custom fetch streams) to read the `/api/.../stream` endpoints and append text to the UI dynamically.
- **Audio Playback:** Captures the Blob URL returned by the synthesize endpoint and mounts it to a hidden `<audio>` HTML tag to play the Azure TTS output.

**Alternative:** In a more complex app, this 50KB file would become unmaintainable. Splitting it into ES6 Modules (`import/export`) or adopting a lightweight reactive framework like Alpine.js or Vue would be the next logical step for a production team.

---

## 7. Deployment Strategy

### Docker Compose (`docker-compose.yml`)
Runs the app and MySQL in isolated containers connected via a private Docker bridge network (`cadence_network`). 
**Minute Detail:** The backend has a `depends_on: mysql` with `condition: service_healthy`. This ensures the FastAPI container won't even attempt to boot until MySQL is fully initialized and accepting connections, preventing crash loops on startup.

### Render Blueprint (`render.yaml`)
Enables Infrastructure-as-Code (IaC) deployment. It automatically spins up a managed MySQL instance and a Docker web service on Render, linking the database credentials directly into the FastAPI environment variables automatically.

---

## Conclusion & Portfolio Value

This codebase is structured like a **Senior-level Microservice**. The attention to detail—such as unblocking the event loop with `asyncio.to_thread`, implementing exponential backoff for network resilience, preventing abusive payloads with Pydantic boundaries, and providing real-time streaming via SSE—are hallmarks of enterprise-grade software engineering. 

When discussing this in an interview, highlighting the **transition from blocking DB calls to thread-pooled async calls**, and the **custom retry decorator for fault tolerance**, will strongly signal advanced engineering maturity.
