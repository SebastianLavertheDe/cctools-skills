#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="$ROOT_DIR/logs/backfill-20260408-20260410.log"

mkdir -p "$ROOT_DIR/logs"
cd "$ROOT_DIR"

while pgrep -af "main.py --date 20260408" | grep -v "pgrep -af" | grep -v "$0" >/dev/null; do
  sleep 30
done

for date in 20260408 20260409 20260410; do
  echo "[backfill] $(date -u +%Y-%m-%dT%H:%M:%SZ) start $date"
  uv run --env-file .env python -u main.py --date "$date"
  echo "[backfill] $(date -u +%Y-%m-%dT%H:%M:%SZ) done $date"
done
