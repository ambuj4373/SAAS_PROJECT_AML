#!/usr/bin/env bash
# frontend/run.sh — Probitas local dev launcher.
#
# Starts the FastAPI server on http://localhost:8000 and serves the
# static frontend from frontend/public/.
#
# Usage:
#     ./frontend/run.sh                # default port 8000
#     PROBITAS_PORT=3000 ./frontend/run.sh

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Sanity: ensure dependencies are present
python3 -c "import fastapi, uvicorn" 2>/dev/null || {
  echo "→ Installing FastAPI dependencies (one-time)…"
  python3 -m pip install --quiet fastapi 'uvicorn[standard]' python-dotenv
}

# Sanity: warn if admin code not set
if ! grep -q '^PROBITAS_ADMIN_CODE=' .env 2>/dev/null; then
  echo "⚠  PROBITAS_ADMIN_CODE is not set in .env."
  echo "   Add a line like:"
  echo "       PROBITAS_ADMIN_CODE=your-secret-here"
  echo "   …then restart. Without it you can't run reports until Stripe is wired."
  echo ""
fi

PORT="${PROBITAS_PORT:-8000}"

echo ""
echo "  ┌─────────────────────────────────────────────┐"
echo "  │  Probitas · local dev                       │"
echo "  │                                             │"
echo "  │  http://localhost:${PORT}                       │"
echo "  │                                             │"
echo "  │  Ctrl+C to stop                             │"
echo "  └─────────────────────────────────────────────┘"
echo ""

exec python3 -m uvicorn frontend.api.main:app \
  --host 0.0.0.0 --port "$PORT" --reload \
  --reload-dir frontend
