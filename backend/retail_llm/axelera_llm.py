"""Axelera Metis / axllm backend for the query + answer LLM calls.

Only imported when RETAIL_LLM_BACKEND=axelera. Mirrors the working
`sql-demo-metis/sql_demo_app.py` reference in the SDK, plus the deployment
guide's hard-won rules:
  - load AxInstance + tokenizer + ChatEncoder ONCE at startup
  - the Metis device serves ONE generation at a time -> one lock
  - always pass the FULL compiled max_tokens to stream_response (a smaller
    value makes the static Llama build return an empty completion)
  - encode() with EMPTY history — the pipeline builds a self-contained prompt
  - temperature 0 for everything
  - one ChatEncoder per system prompt (shared encoders drift across calls)
  - the model emits RAW text (SQL or a short sentence), not JSON
"""
import threading

from .config import AXELERA_DEVICE, AXELERA_NETWORK_YAML, VOYAGER_SDK_ROOT

_lock = threading.Lock()
_state = {"model": None, "tokenizer": None, "encoders": {}, "max_tokens": 1024,
          "min_response_space": None, "eos_id": None, "end_id": None,
          "loaded": False, "error": None, "last_ttft": None}


def _build():
    from pathlib import Path

    from axelera.llm import utils
    from axelera.llm.axllm import load_tokenizer
    from axelera.llm.model_instance import AxInstance

    sdk = Path(VOYAGER_SDK_ROOT)
    net = utils.load_yamlfile(AXELERA_NETWORK_YAML)
    model_cfg = next(iter(net["models"].values()))
    extra = model_cfg.get("extra_kwargs", {}).get("llm", {})

    model = AxInstance(
        yaml=net,
        build_root=sdk / "build",
        ddr_requirement_gb=extra.get("ddr_requirement_gb", 4),
        device_selector=AXELERA_DEVICE,          # string, e.g. "0"
    )
    tokenizer = load_tokenizer(
        tokenizer_dir=None,
        tokenizer_url=extra.get("tokenizer_url"),
        tokenizer_md5=extra.get("tokenizer_md5", ""),
        model_name=extra.get("model_name"),
        build_root=sdk / "build",
    )
    eos_id = tokenizer.eos_token_id
    end_id = next((tid for tid, tok in tokenizer.added_tokens_decoder.items()
                   if tok.content == "<|end|>"), None)

    _state.update(model=model, tokenizer=tokenizer,
                  max_tokens=extra.get("max_tokens", 1024),
                  min_response_space=extra.get("min_response_space", None),
                  eos_id=eos_id, end_id=end_id, encoders={}, loaded=True, error=None)


def _encoder_for(system_prompt: str):
    """One ChatEncoder per distinct system prompt (they drift if shared)."""
    from axelera.llm.conversation import ChatEncoder
    key = hash(system_prompt)
    enc = _state["encoders"].get(key)
    if enc is None:
        enc = ChatEncoder(
            _state["tokenizer"], _state["max_tokens"],
            embedding_processor=_state["model"].embedding_processor,
            system_prompt=system_prompt,
            min_response_space=_state["min_response_space"],
        )
        _state["encoders"][key] = enc
    return enc


# ---- public API (mirrors llm.py's llamacpp backend) --------------------
def load():
    with _lock:
        if _state["loaded"]:
            return
        try:
            _build()
        except Exception as e:  # pragma: no cover - device dependent
            _state["error"] = str(e)
            raise


def available() -> bool:
    return _state["loaded"]


def status() -> dict:
    return {"backend": "axelera", "loaded": _state["loaded"],
            "device": AXELERA_DEVICE, "yaml": AXELERA_NETWORK_YAML,
            "max_tokens": _state["max_tokens"], "last_ttft_s": _state["last_ttft"],
            "error": _state["error"]}


def _run(system: str, user: str):
    """Yield DELTA text pieces. `stream_response`'s `piece` is cumulative."""
    from axelera.llm.conversation import stream_response
    with _lock:
        if not _state["loaded"]:
            raise RuntimeError("Axelera model not loaded yet")
        enc = _encoder_for(system)
        enc.reset(preserve_system_prompt=True)
        input_ids, embedding_features = enc.encode(user, [])   # [] = no history
        prev = ""
        for piece, stats in stream_response(
                _state["model"], enc, _state["tokenizer"],
                input_ids, embedding_features,
                _state["max_tokens"],      # ALWAYS the full compiled budget
                0,                         # temperature
                _state["eos_id"], _state["end_id"]):
            if stats and stats.get("ttft") is not None and prev == "":
                _state["last_ttft"] = round(stats["ttft"], 2)
            if piece and piece != prev:
                yield piece[len(prev):]
                prev = piece


def complete(system: str, user: str, is_retry: bool = False, max_tokens: int = 512) -> str:
    # is_retry / max_tokens ignored: greedy always, full budget always. The
    # pipeline changes the *prompt* on retry, which produces a different attempt.
    return "".join(_run(system, user)).strip()


def complete_stream(system: str, user: str, max_tokens: int = 256):
    yield from _run(system, user)
