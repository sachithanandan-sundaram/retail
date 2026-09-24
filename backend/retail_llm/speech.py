"""Optional local speech-to-text for the push-to-talk chat input.

Needs `faster-whisper` (not in requirements.txt by default):
    pip install faster-whisper

The model (default "base", ~150 MB) auto-downloads on first use.
Override with RETAIL_WHISPER_MODEL=small / medium / distil-small.en etc.
"""
import os
import threading

_model = None
_lock = threading.Lock()


def _load():
    global _model
    if _model is not None:
        return _model
    with _lock:
        if _model is None:
            from faster_whisper import WhisperModel
            from .config import WHISPER_MODEL
            # CPU/int8 by default — the Metis host has no GPU, and "auto" +
            # medium would try a large download and can stall the event loop.
            device = os.environ.get("RETAIL_WHISPER_DEVICE", "cpu")
            _model = WhisperModel(WHISPER_MODEL, device=device, compute_type="int8")
    return _model


def transcribe(path: str) -> str:
    model = _load()
    segments, _ = model.transcribe(path, language="en", vad_filter=True)
    return " ".join(s.text.strip() for s in segments).strip()
