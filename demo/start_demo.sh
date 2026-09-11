#!/usr/bin/env bash
# One-click demo launcher: verifies the KB is seeded, then starts the browser demo.
# Run:  ./demo/start_demo.sh   (or double-click)
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${PORT:-8001}"
URL="http://localhost:${PORT}"

# Seed the KB on first run (needs network once to fetch ~130MB model weights).
if [ -z "$(ls -A chroma_data 2>/dev/null)" ]; then
  echo "KB empty — seeding it (first run downloads model weights, then cached)..."
  ./venv/bin/python -m scripts.seed_knowledge_base
fi

echo "Starting demo at ${URL}  (Ctrl+C to stop)"
echo
./venv/bin/python scripts/demo_server.py