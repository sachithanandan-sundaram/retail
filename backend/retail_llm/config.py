import os
from pathlib import Path

# repo root — backend/retail_llm/config.py -> up 3
ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = Path(__file__).resolve().parents[1]
FRONTEND_DIR = ROOT / "frontend"
DB_PATH = Path(os.environ.get("RETAIL_DB_PATH", ROOT / "retail.db"))

# Local GGUF model for the query-writing / answer-phrasing LLM calls.
# Qwen2.5-3B-Instruct Q4_K_M (fits the RTX 3050 4GB). Auto-detected from the
# bundled models/ folder; override with RETAIL_GGUF_MODEL_PATH.
_DEFAULT_GGUF = ROOT / "models" / "Qwen2.5-3B-Instruct-GGUF" / "qwen2.5-3b-instruct-q4_k_m.gguf"
GGUF_MODEL_PATH = os.environ.get(
    "RETAIL_GGUF_MODEL_PATH",
    str(_DEFAULT_GGUF) if _DEFAULT_GGUF.exists() else "",
)
N_CTX = int(os.environ.get("RETAIL_N_CTX", "4096"))
N_GPU_LAYERS = int(os.environ.get("RETAIL_N_GPU_LAYERS", "-1"))

# LLM backend: "llamacpp" (local GGUF, dev box) or "axelera" (Metis / axllm).
LLM_BACKEND = os.environ.get("RETAIL_LLM_BACKEND", "llamacpp").lower()

# --- Axelera Metis / Voyager SDK (only used when LLM_BACKEND == "axelera") ---
VOYAGER_SDK_ROOT = os.environ.get("VOYAGER_SDK_ROOT", "/voyager-sdk")
AXELERA_DEVICE = os.environ.get("AXELERA_DEVICE", "0")   # kept as str (device_selector)
# Pre-compiled zoo config. Llama-3.2-3B, 1024-ctx, 4 cores, static shape.
AXELERA_NETWORK_YAML = os.environ.get(
    "RETAIL_AXELERA_YAML",
    f"{VOYAGER_SDK_ROOT}/ax_models/zoo/llm/llama-3-2-3b-1024-4core-static.yaml",
)

# Whisper size for voice input (kept small — Metis host has no GPU).
WHISPER_MODEL = os.environ.get("RETAIL_WHISPER_MODEL", "base")

# Store opens at 09:00, closes at 21:00 in the synthetic data.
STORE_OPEN_HOUR = 9
STORE_CLOSE_HOUR = 21

# Hard cap on rows returned by any generated query.
MAX_ROWS = 500

# "Fixed" now for reproducible demos. Set RETAIL_NOW=2026-08-27T14:30:00 to pin it.
_NOW_ENV = os.environ.get("RETAIL_NOW", "")


def now():
    from datetime import datetime
    if _NOW_ENV:
        return datetime.fromisoformat(_NOW_ENV)
    return datetime.now()
