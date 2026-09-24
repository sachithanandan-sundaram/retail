"""FastAPI backend: deterministic dashboard + LLM-backed NL query (streaming).

    cd backend
    uvicorn retail_llm.server:app --port 8000

Serves the built Vite frontend from ../frontend/dist at / when present.
CORS is open so `npm run dev` (port 5173) can talk to it directly.
"""
import json
import threading
import time

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import dashboard as dash
from . import llm
from .config import FRONTEND_DIR, LLM_BACKEND, now
from .db import run_readonly
from .pipeline import QueryError, answer, answer_stream

app = FastAPI(title="Retail LLM Query Demo")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# --- model warm-up ----------------------------------------------------
# First load can be minutes on Metis (weights download + compile cache).
# Load in a background thread so /health answers immediately; /ready gates
# on the real state. If the load fails (e.g. Metis DDR stuck), keep retrying
# with backoff so the service auto-recovers once the card is fixed — no
# `systemctl restart` needed.
_warm = {"started": False, "done": False, "error": None, "attempts": 0, "t0": time.time()}


def _warm_up():
    _warm["started"] = True
    delay = 20
    while not llm.available():
        _warm["attempts"] += 1
        try:
            llm.warm_up()
        except Exception as e:  # pragma: no cover - device dependent
            _warm["error"] = str(e)
        if llm.available():
            _warm["error"] = None
            break
        _warm["done"] = True          # first attempt finished (failed) — /ready can report
        time.sleep(delay)
        delay = min(delay * 2, 300)
    _warm["done"] = True


@app.on_event("startup")
def _start_warm_up():
    threading.Thread(target=_warm_up, name="llm-warmup", daemon=True).start()


class HistoryTurn(BaseModel):
    question: str
    answer: str
    sql: str | None = None


class ChatRequest(BaseModel):
    question: str
    history: list[HistoryTurn] = []


@app.post("/chat")
def chat(req: ChatRequest):
    if not req.question.strip():
        raise HTTPException(400, "empty question")
    try:
        return answer(req.question, [h.model_dump() for h in req.history])
    except QueryError as e:
        raise HTTPException(422, str(e))
    except Exception as e:  # pragma: no cover
        raise HTTPException(500, str(e))


@app.post("/chat/stream")
def chat_stream(req: ChatRequest):
    if not req.question.strip():
        raise HTTPException(400, "empty question")
    history = [h.model_dump() for h in req.history]

    def sse():
        try:
            for event, payload in answer_stream(req.question, history):
                yield f"event: {event}\ndata: {json.dumps(payload, default=str)}\n\n"
        except Exception as e:
            yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream")


# legacy single-shot endpoint (no history)
class Ask(BaseModel):
    question: str


@app.post("/ask")
def ask(req: Ask):
    if not req.question.strip():
        raise HTTPException(400, "empty question")
    try:
        return answer(req.question)
    except QueryError as e:
        raise HTTPException(422, str(e))


@app.get("/dashboard")
def dashboard(days: int = 14):
    try:
        return dash.summary(days=days)
    except Exception as e:  # pragma: no cover
        raise HTTPException(500, f"{e} — did you run `python -m retail_llm.generate_data`?")


@app.get("/bill/{bill_no}")
def bill(bill_no: int):
    b = dash.bill(bill_no)
    if b is None:
        raise HTTPException(404, f"no bill #{bill_no}")
    return b


@app.post("/speech-to-text")
async def speech_to_text(audio: UploadFile):
    """Transcribe a voice query with faster-whisper. The dependency is optional
    — if it isn't installed the push-to-talk button just reports voice is off,
    and typed questions are unaffected."""
    import importlib.util
    if importlib.util.find_spec("faster_whisper") is None:
        raise HTTPException(501, "Voice input isn't enabled on this backend "
                                 "(pip install faster-whisper).")
    from . import speech
    import tempfile
    from pathlib import Path as _P
    suffix = _P(audio.filename or "audio.webm").suffix or ".webm"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await audio.read())
        tmp_path = tmp.name
    try:
        text = speech.transcribe(tmp_path)
    except Exception as e:
        raise HTTPException(500, f"Transcription failed: {e}")
    finally:
        _P(tmp_path).unlink(missing_ok=True)
    if not text:
        raise HTTPException(422, "Couldn't make out any speech.")
    return {"text": text}


@app.get("/health")
def health():
    """Liveness — the process is up. Does not wait for the model."""
    try:
        run_readonly("SELECT 1")
        db_ok = True
    except Exception:
        db_ok = False
    return {"status": "ok", "db": db_ok, "llm": llm.available(),
            "backend": LLM_BACKEND, "now": now().isoformat()}


@app.get("/ready")
def ready():
    """Readiness — the model has finished loading. Fast paths work before
    this is true; the general LLM query path does not."""
    loaded = llm.available()
    payload = {
        "ready": loaded,
        "backend": LLM_BACKEND,
        "warmup_started": _warm["started"],
        "warmup_done": _warm["done"],
        "warmup_attempts": _warm["attempts"],
        "warmup_error": _warm["error"],
        "elapsed_s": round(time.time() - _warm["t0"], 1),
        "model": llm.status(),
    }
    return payload if loaded else JSONResponse(payload, status_code=503)


_DIST = FRONTEND_DIR / "dist"
if _DIST.exists():
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="frontend")
elif FRONTEND_DIR.exists():
    # dev fallback: serve source dir (works for the plain-HTML build)
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
