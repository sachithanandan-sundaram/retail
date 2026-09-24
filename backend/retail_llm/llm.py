"""LLM backend dispatcher.

Two backends, selected by RETAIL_LLM_BACKEND:
  - "llamacpp" (default): local GGUF via llama.cpp — the dev box (Qwen2.5-3B).
  - "axelera": Llama-3.2-3B on a Metis card via axllm — see axelera_llm.py.

Public API (used by pipeline.py, unchanged across backends):
  available()      -> bool   model is loaded and usable
  ready()          -> bool   alias of available() (for /ready)
  warm_up()                  load the model now (blocking); safe to call twice
  status()         -> dict
  complete(system, user, is_retry=False, max_tokens=512) -> str
  complete_stream(system, user, max_tokens=256)          -> iterator[str deltas]

A shared model is NOT safe for concurrent inference — each backend serialises
with its own lock.
"""
import threading

from .config import GGUF_MODEL_PATH, LLM_BACKEND, N_CTX, N_GPU_LAYERS

# ======================================================================
# llama.cpp backend (dev box)
# ======================================================================
_cpp_lock = threading.Lock()
_cpp = None
_cpp_failed = False


def _cpp_load():
    global _cpp, _cpp_failed
    if _cpp is not None or _cpp_failed:
        return _cpp
    if not GGUF_MODEL_PATH:
        _cpp_failed = True
        return None
    try:
        import os as _os
        if hasattr(_os, "add_dll_directory"):
            try:
                import torch
                _os.add_dll_directory(_os.path.join(_os.path.dirname(torch.__file__), "lib"))
            except Exception:
                pass
        from llama_cpp import Llama
        _cpp = Llama(model_path=GGUF_MODEL_PATH, n_gpu_layers=N_GPU_LAYERS,
                     n_ctx=N_CTX, verbose=False)
    except Exception as e:  # pragma: no cover - env dependent
        print(f"[llm] llama.cpp load failed: {e}")
        _cpp_failed = True
    return _cpp


def _cpp_complete(system, user, is_retry=False, max_tokens=512):
    gen = ({"temperature": 0.4, "top_p": 0.9, "top_k": 50}
           if is_retry else {"temperature": 0.0})
    with _cpp_lock:
        llm = _cpp_load()
        if llm is None:
            raise RuntimeError("llama.cpp backend not available")
        out = llm.create_chat_completion(
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            max_tokens=max_tokens, repeat_penalty=1.1, **gen)
    return out["choices"][0]["message"]["content"].strip()


def _cpp_stream(system, user, max_tokens=256):
    with _cpp_lock:
        llm = _cpp_load()
        if llm is None:
            raise RuntimeError("llama.cpp backend not available")
        stream = llm.create_chat_completion(
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            max_tokens=max_tokens, temperature=0.0, repeat_penalty=1.1, stream=True)
        for chunk in stream:
            piece = chunk["choices"][0]["delta"].get("content")
            if piece:
                yield piece


# ======================================================================
# dispatch
# ======================================================================
_IS_AXELERA = LLM_BACKEND == "axelera"


def _ax():
    from . import axelera_llm
    return axelera_llm


def warm_up():
    """Block until the model is loaded. Call from a startup thread."""
    if _IS_AXELERA:
        _ax().load()
    else:
        with _cpp_lock:
            _cpp_load()


def available() -> bool:
    if _IS_AXELERA:
        try:
            return _ax().available()
        except Exception:
            return False
    with _cpp_lock:
        return _cpp_load() is not None


ready = available


def status() -> dict:
    if _IS_AXELERA:
        try:
            return _ax().status()
        except Exception as e:
            return {"backend": "axelera", "loaded": False, "error": str(e)}
    return {"backend": "llamacpp", "loaded": available(), "model_path": GGUF_MODEL_PATH}


def complete(system: str, user: str, is_retry: bool = False, max_tokens: int = 512) -> str:
    if _IS_AXELERA:
        return _ax().complete(system, user, is_retry, max_tokens)
    return _cpp_complete(system, user, is_retry, max_tokens)


def complete_stream(system: str, user: str, max_tokens: int = 256):
    if _IS_AXELERA:
        yield from _ax().complete_stream(system, user, max_tokens)
    else:
        yield from _cpp_stream(system, user, max_tokens)
