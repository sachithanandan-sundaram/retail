#!/usr/bin/env bash
set -euo pipefail

VENV="${VOYAGER_SDK_ROOT:-/voyager-sdk}/axelera-env"
PY="$VENV/bin/python"

[ -x "$PY" ] || { echo "SDK venv not bind-mounted at $VENV"; exit 1; }
"$PY" -c "import axelera.llm" || { echo "axelera.llm not importable — check the bind mount"; exit 1; }

# install the app deps into the SDK venv once (idempotent, ~fast if satisfied)
"$PY" -m pip install -q -r /app/backend/requirements-axelera.txt

cd /app/backend
[ -f "${RETAIL_DB_PATH:-/app/retail.db}" ] || "$PY" -m retail_llm.generate_data

exec "$PY" -m uvicorn retail_llm.server:app --host 0.0.0.0 --port 8000
