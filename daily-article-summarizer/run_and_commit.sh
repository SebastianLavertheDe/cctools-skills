#!/usr/bin/env bash
set -euo pipefail

# Run the summarizer only. Content root is read from the environment by
# main.py (OPENMIND_ROOT); auto-commit has been removed — commit outputs
# through openmind-app or manually.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -z "${OPENMIND_ROOT:-}" ]]; then
  echo "run_and_commit.sh: 需要设置 OPENMIND_ROOT（指向数据仓根）" >&2
  exit 1
fi

if command -v uv >/dev/null 2>&1; then
  UV_BIN="$(command -v uv)"
elif [[ -x "$HOME/.local/bin/uv" ]]; then
  UV_BIN="$HOME/.local/bin/uv"
else
  echo "uv not found in PATH or at \$HOME/.local/bin/uv" >&2
  exit 127
fi

cd "$ROOT_DIR"
# .env 仅在存在时加载（uv 对缺失 env 文件会直接报错）。
UV_ENV_ARGS=()
if [[ -f "$ROOT_DIR/.env" ]]; then
  UV_ENV_ARGS+=(--env-file "$ROOT_DIR/.env")
fi
"$UV_BIN" run "${UV_ENV_ARGS[@]}" python main.py "$@"
