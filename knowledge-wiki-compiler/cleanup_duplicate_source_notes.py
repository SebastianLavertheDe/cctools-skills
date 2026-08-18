#!/usr/bin/env python3

from __future__ import annotations

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from knowledge_wiki_compiler.app import (  # noqa: E402
    collect_existing_source_records,
    compiler_build_id,
    manifest_record,
    rename_existing_source_notes,
    scan_raw,
    write_json,
)
from knowledge_wiki_compiler.config import load_config  # noqa: E402


def main() -> int:
    config = load_config((ROOT_DIR / "config.yaml").resolve())

    deduped_articles = scan_raw(config, only="articles")
    kept_article_ids = {
        entry.source_id for entry in deduped_articles if entry.source_kind == "article"
    }

    existing_records = collect_existing_source_records(config)
    removed_note_paths: list[Path] = []
    removed_source_ids: list[str] = []

    for source_id, record in sorted(existing_records.items()):
        if str(record.get("source_kind", "")).strip() != "article":
            continue
        if source_id in kept_article_ids:
            continue

        note_path_value = str(record.get("note_path", "") or "").strip()
        if not note_path_value:
            continue
        note_path = config.repo_root / note_path_value
        if note_path.exists():
            note_path.unlink()
            removed_note_paths.append(note_path)
        removed_source_ids.append(source_id)

    rename_existing_source_notes(config)

    registry = collect_existing_source_records(config)
    all_entries = scan_raw(config, only="all")
    build_id = compiler_build_id(config)
    manifest = {
        entry.source_id: manifest_record(entry, compiler_build=build_id)
        for entry in all_entries
    }

    write_json(config.state_dir / "registry.json", registry)
    write_json(config.state_dir / "manifest.json", manifest)

    print(
        f"Removed {len(removed_source_ids)} duplicate article source records and {len(removed_note_paths)} note files."
    )
    print(f"Registry records: {len(registry)}")
    print(f"Manifest records: {len(manifest)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
