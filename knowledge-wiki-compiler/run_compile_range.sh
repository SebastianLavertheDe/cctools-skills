#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# 内容根由子进程从环境读取（OPENMIND_ROOT，旧名 CCTOOLS_MYMIND_ROOT）。
# auto-commit 已移除，产出经 openmind-app 或手动提交。
START_DATE="${1:-20260209}"
END_DATE="${2:-20260409}"

validate_date() {
  if [[ ! "$1" =~ ^[0-9]{8}$ ]]; then
    echo "invalid date: $1 (expected YYYYMMDD)" >&2
    exit 1
  fi
}

validate_date "$START_DATE"
validate_date "$END_DATE"

cd "$ROOT_DIR"
mkdir -p logs

if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
else
  echo "python interpreter not found (expected python3 or python)" >&2
  exit 1
fi

DATES="$(
  "$PYTHON_BIN" - "$START_DATE" "$END_DATE" <<'PY'
from datetime import datetime, timedelta
import sys

start = datetime.strptime(sys.argv[1], "%Y%m%d").date()
end = datetime.strptime(sys.argv[2], "%Y%m%d").date()

if start > end:
    raise SystemExit("start_date must be <= end_date")

current = start
while current <= end:
    print(current.strftime("%Y%m%d"))
    current += timedelta(days=1)
PY
)"

while IFS= read -r date; do
  [[ -n "$date" ]] || continue
  log_file="logs/wiki-compile-${date}.log"
  echo "[run] ${date} -> ${log_file}"
  uv run python main.py --date "$date" 2>&1 | tee "$log_file"
done <<< "$DATES"

if [[ "$START_DATE" == "$END_DATE" ]]; then
  COMMIT_DATE_LABEL="$START_DATE"
else
  COMMIT_DATE_LABEL="${START_DATE}-${END_DATE}"
fi

echo "[run_compile_range] done: ${COMMIT_DATE_LABEL} (auto-commit 已移除，请经 openmind-app 或手动提交)"
