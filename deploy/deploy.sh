#!/usr/bin/env bash
# Deploy the retail LLM system on the Axelera Metis host (bare-metal, no Docker).
#
# Run this ON the Metis host, from the repo root, after copying the repo over.
#
#   ./deploy/deploy.sh /path/to/voyager-sdk
#
# It: installs app deps into the SDK's venv, builds the frontend, seeds the DB,
# and prints the command to start the service (or use the systemd unit).
set -euo pipefail

SDK="${1:-${VOYAGER_SDK_ROOT:-/voyager-sdk}}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$SDK/axelera-env"

[ -d "$VENV" ] || { echo "SDK venv not found at $VENV"; exit 1; }
echo "== SDK: $SDK"
echo "== repo: $REPO"

# ---- 1. app deps into the SDK venv (needs axelera.llm + these at once) ----
source "$VENV/bin/activate"
python -c "import axelera.llm" 2>/dev/null || { echo "axelera.llm not importable in $VENV"; exit 1; }
pip install -r "$REPO/backend/requirements-axelera.txt"

# ---- 2. confirm the device is visible ----
axdevice || { echo "no Metis device — check metis-dkms / nothing else holding it"; exit 1; }

# ---- 3. frontend (needs Node; skip if dist/ already shipped) ----
if command -v npm >/dev/null && [ ! -f "$REPO/frontend/dist/index.html" ]; then
  ( cd "$REPO/frontend" && npm ci && npm run build )
fi

# ---- 4. seed the synthetic DB ----
( cd "$REPO/backend" && ${RETAIL_NOW:+RETAIL_NOW="$RETAIL_NOW"} python -m retail_llm.generate_data )

# ---- 5. env file for the service ----
cat > "$REPO/deploy/retail-llm.env" <<EOF
RETAIL_LLM_BACKEND=axelera
VOYAGER_SDK_ROOT=$SDK
AXELERA_DEVICE=0
RETAIL_AXELERA_YAML=$SDK/ax_models/zoo/llm/llama-3-2-3b-1024-4core-static.yaml
RETAIL_DB_PATH=$REPO/retail.db
EOF
# Uncomment to pin "now" for a reproducible demo instead of tracking the real clock:
# echo "RETAIL_NOW=2026-08-27T20:00:00" >> "$REPO/deploy/retail-llm.env"
echo "== wrote deploy/retail-llm.env"

echo
echo "Start it now with:"
echo "  cd $REPO/backend && env \$(grep -v '^#' $REPO/deploy/retail-llm.env | xargs) \\"
echo "    $VENV/bin/python -m uvicorn retail_llm.server:app --host 0.0.0.0 --port 8000"
echo
echo "Or install the systemd unit — see deploy/README.md."
