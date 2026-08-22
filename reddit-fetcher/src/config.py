from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .runtime_paths import require_skill_data_dir, require_content_root, resolve_bound_path


@dataclass(frozen=True)
class FetchConfig:
    timeout_seconds: int
    user_agent: str
    fetch_comments: bool
    comment_timeout_seconds: int
    top_level_comment_limit: int
    reply_limit: int
    max_comment_depth: int
    listing_retry_count: int
    comment_retry_count: int
    request_delay_seconds: float
    rss_only: bool
    sources: list[str]


@dataclass(frozen=True)
class StorageConfig:
    output_dir: Path
    cache_file: Path


@dataclass(frozen=True)
class AppConfig:
    fetch: FetchConfig
    storage: StorageConfig


def load_config(
    config_path: str = "config.yaml",
    content_root: str | None = None,
    skill_data_dir: str | None = None,
) -> AppConfig:
    config_abs = Path(config_path).expanduser().resolve()
    with open(config_abs, "r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    fetch_raw = raw.get("fetch", {})
    storage_raw = raw.get("storage", {})

    root = require_content_root(content_root)
    data_dir = require_skill_data_dir(skill_data_dir)

    # Default output_dir: <content_root>/reddit
    output_dir_raw = storage_raw.get("output_dir", "")
    if output_dir_raw:
        output_dir = resolve_bound_path(str(output_dir_raw), root, "storage.output_dir")
    else:
        output_dir = root / "reddit"

    cache_file_raw = storage_raw.get("cache_file", "")
    if cache_file_raw:
        cache_file = resolve_bound_path(str(cache_file_raw), data_dir, "storage.cache_file")
    else:
        cache_file = data_dir / ".reddit_fetch_cache.json"

    return AppConfig(
        fetch=FetchConfig(
            timeout_seconds=int(fetch_raw.get("timeout_seconds", 30)),
            user_agent=str(
                fetch_raw.get(
                    "user_agent",
                    "Mozilla/5.0 (compatible; reddit-fetcher/0.1)",
                )
            ),
            fetch_comments=bool(fetch_raw.get("fetch_comments", False)),
            comment_timeout_seconds=int(fetch_raw.get("comment_timeout_seconds", 20)),
            top_level_comment_limit=int(fetch_raw.get("top_level_comment_limit", 20)),
            reply_limit=int(fetch_raw.get("reply_limit", 3)),
            max_comment_depth=int(fetch_raw.get("max_comment_depth", 2)),
            listing_retry_count=int(fetch_raw.get("listing_retry_count", 3)),
            comment_retry_count=int(fetch_raw.get("comment_retry_count", 2)),
            request_delay_seconds=float(fetch_raw.get("request_delay_seconds", 0.2)),
            rss_only=bool(fetch_raw.get("rss_only", True)),
            sources=list(fetch_raw.get("sources", [])),
        ),
        storage=StorageConfig(
            output_dir=output_dir,
            cache_file=cache_file,
        ),
    )
