#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# 内容根：用户在 openmind-app 选择的数据根，即数据仓根本身（扁平布局，无 mymind/ 层）。
# auto-commit 已移除，产出经 openmind-app 或手动提交。
CONTENT_ROOT="${OPENMIND_ROOT:-}"
if [[ -z "$CONTENT_ROOT" ]]; then
  echo "run_and_commit.sh: 需要设置 OPENMIND_ROOT（指向数据仓根）" >&2
  exit 1
fi
LOG_DIR="$ROOT_DIR/logs"
TARGET_DATE=""
CONTENT_ARG="$CONTENT_ROOT"
SKILL_DATA_DIR="$ROOT_DIR/data"
PASSTHROUGH_ARGS=()

setup_daily_log() {
  mkdir -p "$LOG_DIR"

  local log_date="$1"
  local daily_log="$LOG_DIR/cron-${log_date}.log"
  local legacy_log="$LOG_DIR/cron.log"
  local current_stdout_target=""
  local current_stderr_target=""

  if [[ -e "/proc/$$/fd/1" ]]; then
    current_stdout_target="$(readlink "/proc/$$/fd/1" 2>/dev/null || true)"
  fi
  if [[ -e "/proc/$$/fd/2" ]]; then
    current_stderr_target="$(readlink "/proc/$$/fd/2" 2>/dev/null || true)"
  fi

  if [[ "${KNOWLEDGE_WIKI_COMPILER_LOG_INITIALIZED:-0}" != "1" ]]; then
    export KNOWLEDGE_WIKI_COMPILER_LOG_INITIALIZED=1
    exec >>"$daily_log" 2>&1
  fi

  if [[ "$current_stdout_target" == "$legacy_log" || "$current_stderr_target" == "$legacy_log" ]]; then
    : > "$legacy_log"
  fi

  # Auto-delete daily logs older than 7 days
  find "$LOG_DIR" -name "cron-*.log" -mtime +7 -delete 2>/dev/null || true
}

if command -v uv >/dev/null 2>&1; then
  UV_BIN="$(command -v uv)"
elif [[ -x "$HOME/.local/bin/uv" ]]; then
  UV_BIN="$HOME/.local/bin/uv"
else
  echo "uv not found in PATH or at \$HOME/.local/bin/uv" >&2
  exit 127
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --date)
      if [[ $# -lt 2 ]]; then
        echo "--date requires a value" >&2
        exit 1
      fi
      TARGET_DATE="$2"
      PASSTHROUGH_ARGS+=("$1" "$2")
      shift 2
      ;;
    --date=*)
      TARGET_DATE="${1#*=}"
      PASSTHROUGH_ARGS+=("$1")
      shift
      ;;
    --content-root)
      if [[ $# -lt 2 ]]; then
        echo "--content-root requires a value" >&2
        exit 1
      fi
      CONTENT_ARG="$2"
      shift 2
      ;;
    --content-root=*)
      CONTENT_ARG="${1#*=}"
      shift
      ;;
    --skill-data-dir)
      if [[ $# -lt 2 ]]; then
        echo "--skill-data-dir requires a value" >&2
        exit 1
      fi
      SKILL_DATA_DIR="$2"
      shift 2
      ;;
    --skill-data-dir=*)
      SKILL_DATA_DIR="${1#*=}"
      shift
      ;;
    *)
      PASSTHROUGH_ARGS+=("$1")
      shift
      ;;
  esac
done

if [[ -z "$TARGET_DATE" ]]; then
  TARGET_DATE="$(date +%Y%m%d)"
  PASSTHROUGH_ARGS+=(--date "$TARGET_DATE")
fi

setup_daily_log "$TARGET_DATE"

mkdir -p "$SKILL_DATA_DIR"

cd "$ROOT_DIR"
"$UV_BIN" run python main.py \
  --content-root "$CONTENT_ARG" \
  --skill-data-dir "$SKILL_DATA_DIR" \
  "${PASSTHROUGH_ARGS[@]}"
