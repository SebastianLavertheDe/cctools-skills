"""Explicit runtime path resolution for the installed Asset Collector."""

from __future__ import annotations

import os
from pathlib import Path


class RuntimePathError(ValueError):
    """Raised when an installed Skill is missing a safe path binding."""


def require_mymind_root(cli_value: str | None = None) -> Path:
    raw = (cli_value or os.environ.get("CCTOOLS_MYMIND_ROOT") or os.environ.get("MYMIND_ROOT") or "").strip()
    if not raw:
        raise RuntimePathError("mymind root is required; pass --mymind-root or CCTOOLS_MYMIND_ROOT")
    root = Path(raw).expanduser().resolve()
    if not root.is_dir():
        raise RuntimePathError(f"mymind root is not an existing directory: {root}")
    return root


def resolve_mymind_path(value: str, root: Path, label: str) -> Path:
    raw = value.strip()
    if not raw:
        raise RuntimePathError(f"{label} must not be empty")
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        parts = candidate.parts
        if parts and parts[0].lower() == "mymind":
            candidate = Path(*parts[1:])
        candidate = root / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise RuntimePathError(f"{label} must stay inside mymind root: {raw}") from exc
    return resolved
