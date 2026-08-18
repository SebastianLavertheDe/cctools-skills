#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# 本 Skill 仓库根（skill 平铺在顶层）
SKILL_REPO_ROOT="$(cd "$ROOT_DIR/.." && pwd)"
# 内容仓库（cctools）：mymind 数据与 auto_commit 脚本所在地。
# 优先级：CCTOOLS_REPO_ROOT > CCTOOLS_MYMIND_ROOT 的父目录 > 报错。
REPO_ROOT="${CCTOOLS_REPO_ROOT:-}"
if [[ -z "$REPO_ROOT" ]]; then
  if [[ -n "${CCTOOLS_MYMIND_ROOT:-}" ]]; then
    REPO_ROOT="$(cd "$CCTOOLS_MYMIND_ROOT/.." && pwd)"
  else
    echo "run_and_commit.sh: 需要设置 CCTOOLS_REPO_ROOT 或 CCTOOLS_MYMIND_ROOT（指向 cctools 仓库）" >&2
    exit 1
  fi
fi
TARGET_DATE=""
BASE_DIR="${CCTOOLS_MYMIND_ROOT:-$REPO_ROOT/mymind}"
SKILL_DATA_DIR="$ROOT_DIR/data"
# Args forwarded verbatim to generate_daily_topics.py. --base-dir is consumed by
# this wrapper (it names the mymind root) and remapped to --mymind-root below.
# generate_daily_topics.py reserves --base-dir for a logical subdir under the
# root, so forwarding the absolute path would be a semantic mismatch.
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

if [[ -z "$TARGET_DATE" ]]; then
  TARGET_DATE="$(date +%Y%m%d)"
fi

mkdir -p "$SKILL_DATA_DIR"

cd "$REPO_ROOT"
uv run --project "$SKILL_REPO_ROOT/daily-topic-selector" python "$SKILL_REPO_ROOT/daily-topic-selector/scripts/generate_daily_topics.py" \
  --mymind-root "$BASE_DIR" \
  --skill-data-dir "$SKILL_DATA_DIR" \
  "${PASSTHROUGH_ARGS[@]}"

REL_BASE_DIR="$(python3 - "$REPO_ROOT" "$BASE_DIR" <<'PY'
import os
import sys

repo_root = os.path.abspath(sys.argv[1])
base_dir = os.path.abspath(sys.argv[2])
rel = os.path.relpath(base_dir, repo_root)
if rel.startswith(".."):
    print("")
else:
    print(rel)
PY
)"

if [[ -z "$REL_BASE_DIR" ]]; then
  echo "[auto-commit] skip: base dir is outside repo: $BASE_DIR"
  exit 0
fi

"$REPO_ROOT/scripts/auto_commit_paths.sh" \
  "chore(daily-topic-selector): update ${TARGET_DATE} topics" \
  "${REL_BASE_DIR}/daily-topic/${TARGET_DATE}_daily_topic.md"
