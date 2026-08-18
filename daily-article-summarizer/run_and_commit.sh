#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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
ORIGINAL_ARGS=("$@")

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
      shift 2
      ;;
    --date=*)
      TARGET_DATE="${1#*=}"
      shift
      ;;
    *)
      shift
      ;;
  esac
done

if [[ -z "$TARGET_DATE" ]]; then
  TARGET_DATE="$(date +%Y%m%d)"
fi

cd "$ROOT_DIR"
"$UV_BIN" run --env-file .env python main.py "${ORIGINAL_ARGS[@]}"

"$REPO_ROOT/scripts/auto_commit_paths.sh" \
  "chore(daily-article-summarizer): update ${TARGET_DATE} summary" \
  "mymind/daily-summary/${TARGET_DATE}_daily_summary.md"
