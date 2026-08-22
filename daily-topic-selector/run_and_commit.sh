#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# 本 Skill 仓库根（skill 平铺在顶层；安装副本为 .agent/skills/cctools/）
SKILL_REPO_ROOT="$(cd "$ROOT_DIR/.." && pwd)"
# 内容根：用户在 openmind-app 选择的数据根，即数据仓根本身（扁平布局，无 mymind/ 层）。
# 旧名 CCTOOLS_MYMIND_ROOT 仍被接受。auto-commit 已移除，产出经 openmind-app 或手动提交。
CONTENT_ROOT="${OPENMIND_ROOT:-${CCTOOLS_MYMIND_ROOT:-}}"
if [[ -z "$CONTENT_ROOT" ]]; then
  echo "run_and_commit.sh: 需要设置 OPENMIND_ROOT 或 CCTOOLS_MYMIND_ROOT（指向数据仓根）" >&2
  exit 1
fi
BASE_DIR="$CONTENT_ROOT"
SKILL_DATA_DIR="$ROOT_DIR/data"
# Args forwarded verbatim to generate_daily_topics.py. --base-dir is consumed
# by this wrapper as a legacy alias for the content root and passed as
# --content-root; the script reserves --base-dir for a logical subdir.
PASSTHROUGH_ARGS=()

# Rotate cron.log if over 5MB
LOG_FILE="$ROOT_DIR/logs/cron.log"
if [[ -f "$LOG_FILE" ]]; then
  LOG_SIZE=$(stat -c%s "$LOG_FILE" 2>/dev/null || echo 0)
  if [[ "$LOG_SIZE" -gt 5000000 ]]; then
    tail -c 5000000 "$LOG_FILE" > "$LOG_FILE.tmp" && mv "$LOG_FILE.tmp" "$LOG_FILE"
  fi
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --base-dir)
      if [[ $# -lt 2 ]]; then
        echo "--base-dir requires a value" >&2
        exit 1
      fi
      BASE_DIR="$2"
      shift 2
      ;;
    --base-dir=*)
      BASE_DIR="${1#*=}"
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

mkdir -p "$SKILL_DATA_DIR"

cd "$ROOT_DIR"
uv run --project "$SKILL_REPO_ROOT/daily-topic-selector" python "$SKILL_REPO_ROOT/daily-topic-selector/scripts/generate_daily_topics.py" \
  --content-root "$BASE_DIR" \
  --skill-data-dir "$SKILL_DATA_DIR" \
  "${PASSTHROUGH_ARGS[@]}"
