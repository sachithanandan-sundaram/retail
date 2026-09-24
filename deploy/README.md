# Deploying the Retail LLM system on Axelera Metis

Runs the query/answer LLM on a **Metis card** with **Llama-3.2-3B** (Voyager
SDK zoo model) instead of the dev box's local llama.cpp / Qwen2.5-3B. Everything
else — the SQLite DB, dashboard, fast paths, deterministic layers, frontend —
is unchanged.

## What switches with `RETAIL_LLM_BACKEND=axelera`

| | dev box (`llamacpp`) | Metis (`axelera`) |
|---|---|---|
| model | Qwen2.5-3B GGUF (llama.cpp, CUDA) | Llama-3.2-3B, `llama-3-2-3b-1024-4core-static` |
| loader | `retail_llm/llm.py` → `Llama(...)` | `retail_llm/axelera_llm.py` → `AxInstance` + `ChatEncoder` + `stream_response` |
| context | 4096 | **1024 (static)** → compact prompts (`*_COMPACT` in `schema_prompt.py`) |
| history in prompt | yes | **no** (guide §4 — degrades tight builds; deterministic layers carry follow-ups) |
| `max_new_tokens` | per-call | **always the full compiled budget** (a smaller value → empty completion) |
| sampling | greedy, retry samples | greedy always (retry changes the *prompt*, not the temperature) |
| generation lock | 1 lock | 1 lock (device serves one generation at a time) |
| row cap into phrasing prompt | 40 | 10; deterministic phrasing for >6-row tables |
| voice (`faster-whisper`) | GPU/auto | CPU / int8 / `base` |

Fast paths, `find_product` value-linking, date override, SQL repair,
self-correction, and the answer safety-net are **backend-independent** — they
do most of the reliability work regardless of which model writes the SQL.

## Prerequisites (on the host)

- Metis PCIe/M.2, Ubuntu 24.04, `metis-dkms` kernel module loaded
- Voyager SDK (built against v1.8.0-rc4) at `$VOYAGER_SDK_ROOT` (default `/voyager-sdk`)
- `source $VOYAGER_SDK_ROOT/axelera-env/bin/activate && axdevice` shows `Device 0: metis-...`
- Nothing else holding the device (it's exclusive — one process at a time)
- Node.js if you need to build the frontend on the host (or ship `frontend/dist`)

## Option A — bare metal (systemd)

```bash
# on the host, from the repo root
./deploy/deploy.sh /voyager-sdk
#  -> installs backend/requirements-axelera.txt INTO the SDK venv
#     (NOT a separate .venv — the process needs axelera.llm + app deps together)
#  -> builds frontend/dist, seeds retail.db, writes deploy/retail-llm.env

# then either run it directly (command printed by the script) or:
sudo cp deploy/retail-llm.service /etc/systemd/system/
sudo $EDITOR /etc/systemd/system/retail-llm.service   # fix User + paths
sudo cp -r . /opt/retail                              # or symlink; match WorkingDirectory
sudo systemctl daemon-reload && sudo systemctl enable --now retail-llm
journalctl -u retail-llm -f
```

## Option B — Docker

```bash
export VOYAGER_SDK_ROOT=/voyager-sdk
docker compose -f deploy/docker-compose.yml up --build
```

The image is small — it bind-mounts the host's `axelera-env` and full `/dev`
(privileged), installs the app deps into the mounted venv at start, and
persists downloaded/compiled weights in the `retail-build` volume.

## Verifying

```bash
curl http://HOST:8000/health     # {"status":"ok","llm":<bool>,"backend":"axelera",...}  — instant
curl http://HOST:8000/ready      # 503 until the model finishes loading, then {"ready":true,...}
```

- `/health` answers immediately; the model warms up in a background thread.
- **First load is slow** (weights download + MD5 + compile-cache) — minutes.
  Every load after is fast (`~0.75 s` TTFT for this build).
- Fast-path questions ("revenue today", "below threshold") work **before**
  `/ready` is true. The general LLM query path returns a clear error until then.

Open `http://HOST:8000/` for the dashboard + chat.

## Expected latency (per the axllm guide)

- clean first-try query: ~1.5–2.5 s end to end
- with one self-correction retry: ~9–12 s
- fast paths: <10 ms

## Gotchas carried into the code

- **`axelera_llm.py` always passes the full `max_tokens`** to `stream_response`.
  Never lower it — the static Llama build silently returns 0 tokens.
- **Empty history** into `encoder.encode(prompt, [])`. Follow-ups ("that bill",
  "those 8", "price of X") are resolved deterministically *before* the prompt is
  built, so each prompt is self-contained.
- **One `ChatEncoder` per system prompt** (query vs answer) — shared encoders
  drift across sequential calls.
- If the service "never becomes ready": another process (an old run, another
  container) is holding the Metis device. It releases when that process exits.
- If you compile your own model instead of using the zoo YAML: validate
  generated text against a reference — wrong quant flags caused digit
  corruption (`2026 → 2226`) in some pre-compiled artifacts on v1.8.0-rc4.
