"""Explicit runtime path resolution for the installed Run Reporter."""

from __future__ import annotations

import os
from pathlib import Path


class RuntimePathError(ValueError):
    pass


def require_mymind_root(cli_value: str | None = None) -> Path:
    raw = (cli_value or os.environ.get("CCTOOLS_MYMIND_ROOT") or os.environ.get("MYMIND_ROOT") or "").strip()
    if not raw:
        raise RuntimePathError("mymind root is required; pass --mymind-root or CCTOOLS_MYMIND_ROOT")
    root = Path(raw).expanduser().resolve()
    if not root.is_dir():
        raise RuntimePathError(f"mymind root is not an existing directory: {root}")
    return root


def optional_run_dir(cli_value: str | None = None) -> Path | None:
    raw = (cli_value or os.environ.get("CCTOOLS_RUN_DIR") or "").strip()
    if not raw:
        return None
    return Path(raw).expanduser().resolve()
