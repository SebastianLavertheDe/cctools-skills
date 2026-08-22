from __future__ import annotations

import argparse
import os
from pathlib import Path

from .config import load_config
from .fetcher.client import FetchError, fetch_timelines
from .parser.timeline import parse_timeline_payload
from .storage.cache import PostCache
from .storage.files import save_new_tweets
from .runtime_paths import RuntimePathError, optional_run_dir, require_skill_data_dir, require_content_root


def _ensure_local_cron_bindings() -> None:
    """Fill Broker-style bindings for bare local/cron invocations.

    Desktop Broker always injects these. Existing crontab calls
    ``python3 .../main.py`` with neither CLI flags nor env vars.
    """
    skill_dir = Path(__file__).resolve().parents[1]
    if not os.environ.get("OPENMIND_SKILL_DATA_DIR"):
        os.environ["OPENMIND_SKILL_DATA_DIR"] = str(skill_dir)
    # Installed copies live at <content-root>/.agent/skills/cctools/<skill>/;
    # the ancestor carrying the app-managed .agent/skills store is the
    # user-selected content root itself (flat layout, no mymind/ layer).
    for parent in [skill_dir, *skill_dir.parents]:
        if (parent / ".agent" / "skills").is_dir():
            if not os.environ.get("OPENMIND_ROOT"):
                os.environ["OPENMIND_ROOT"] = str(parent)
            break


def run() -> int:
    print("Fetching latest posts from X (Twitter)...\n")

    parser = argparse.ArgumentParser(description="Fetch X following posts into the bound content root.")
    parser.add_argument("--config", default="", help="Package config path; defaults to config.yaml inside this Skill.")
    parser.add_argument("--content-root", default="", help="Explicit content root; defaults to OPENMIND_ROOT.")
    parser.add_argument("--skill-data-dir", default="", help="App-private Skill data directory.")
    parser.add_argument("--run-dir", default="", help="App-private Run working directory.")
    args = parser.parse_args()
    try:
        _ensure_local_cron_bindings()
        root = require_content_root(args.content_root)
        data_dir = require_skill_data_dir(args.skill_data_dir)
        optional_run_dir(args.run_dir)
        config_path = Path(args.config).expanduser() if args.config else None
        config = load_config(config_path, root, data_dir)
    except RuntimePathError as exc:
        parser.error(str(exc))
    cache = PostCache(config.storage.cache_file)

    all_tweets = []
    managed_credential = os.environ.get("X_FETCHER_CREDENTIAL_JSON", "").strip()
    if managed_credential:
        credential_files = [None]
    else:
        credential_files = [curl_file for curl_file in config.compat.curl_files if curl_file.exists()]
        for curl_file in config.compat.curl_files:
            if not curl_file.exists():
                print(f"Skipping {curl_file.name}: not found")

    for credential_file in credential_files:
        label = credential_file.stem if credential_file is not None else "managed-credential"
        print(f"Fetching timeline from {label}...")
        try:
            responses = fetch_timelines(config, credential_file)
        except FetchError as exc:
            print(f"  Failed: {exc}")
            continue

        for response in responses:
            tweets = parse_timeline_payload(
                response.payload,
                include_promoted=config.fetch.include_promoted,
            )
            print(f"  Got {len(tweets)} tweets from {response.label}")
            all_tweets.extend(tweets)

    if not all_tweets:
        print("Failed to fetch tweets. No usable entries from any timeline.")
        return 1

    # Dedup by tweet_id
    seen: set[str] = set()
    unique = []
    for t in all_tweets:
        if t.tweet_id not in seen:
            seen.add(t.tweet_id)
            unique.append(t)

    dup_count = len(all_tweets) - len(unique)
    if dup_count:
        print(f"Removed {dup_count} duplicate tweets across timelines")

    new_count, total_count = save_new_tweets(unique, config, cache)
    print(f"\n完成！新增 {new_count} 条，总计 {total_count} 条")
    return 0
