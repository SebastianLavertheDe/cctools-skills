from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .runtime_paths import require_skill_data_dir, require_content_root, resolve_data_path, resolve_content_path


@dataclass(slots=True)
class AppConfig:
    skill_root: Path
    repo_root: Path
    article_dir: Path
    post_dir: Path
    post_json_filename: str
    wiki_dir: Path
    state_dir: Path
    sources_dir: Path
    concepts_dir: Path
    entities_dir: Path
    themes_dir: Path
    index_dir: Path
    legacy_queries_dir: Path
    emerging_dir: Path
    families_dir: Path
    domains_dir: Path
    link_style: str
    generate_queries: bool
    generate_entities: bool
    generate_source_notes: bool
    generate_post_source_notes: bool
    simple_group_pages: bool
    theme_wins_overlap: bool
    concept_min_sources: int
    concept_emerging_min_sources: int
    concept_emerging_min_dates: int
    concept_published_min_sources: int
    concept_published_min_dates: int
    entity_min_sources: int
    theme_min_sources: int
    theme_emerging_min_dates: int
    theme_published_min_sources: int
    theme_published_min_dates: int
    evergreen_min_sources: int
    recent_limit: int
    stale_days: int
    review_days: int
    high_signal_window_days: int
    post_max_groups: int
    post_group_min_size: int
    post_tweets_per_group: int
    concept_related_limit: int
    entity_related_limit: int
    theme_related_limit: int
    source_limit_per_section: int
    shared_ai_env_file: Path
    entity_max_input_chars: int
    entity_max_count: int


def load_config(
    config_path: Path,
    content_root: str | Path | None = None,
    skill_data_dir: str | Path | None = None,
) -> AppConfig:
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    skill_root = config_path.parent
    root = require_content_root(str(content_root) if content_root is not None else None)
    data_dir = require_skill_data_dir(str(skill_data_dir) if skill_data_dir is not None else None)

    raw_cfg = raw.get("raw", {})
    wiki_cfg = raw.get("wiki", {})
    compiler_cfg = raw.get("compiler", {})
    relations_cfg = raw.get("relations", {})
    ai_cfg = raw.get("ai", {})

    wiki_dir = resolve_content_path(str(wiki_cfg.get("root_dir", "wiki")), root, "wiki.root_dir")
    # The wiki is user content; compiler state is app-private and must not
    # contaminate the content tree.
    state_dir = data_dir / "wiki-state"

    return AppConfig(
        skill_root=skill_root,
        # Kept for the compiler's relative-link compatibility code. It now
        # means the bound content root, never a repository discovered by walk-up.
        repo_root=root,
        article_dir=resolve_content_path(str(raw_cfg.get("article_dir", "article")), root, "raw.article_dir"),
        post_dir=resolve_content_path(str(raw_cfg.get("post_dir", "post")), root, "raw.post_dir"),
        post_json_filename=str(raw_cfg.get("post_json_filename", "posts.json")),
        wiki_dir=wiki_dir,
        state_dir=state_dir,
        sources_dir=wiki_dir / "sources",
        concepts_dir=wiki_dir / "concepts",
        entities_dir=wiki_dir / "entities",
        themes_dir=wiki_dir / "themes",
        index_dir=wiki_dir / "index",
        legacy_queries_dir=wiki_dir / "queries",
        emerging_dir=wiki_dir / "emerging",
        families_dir=wiki_dir / "families",
        domains_dir=wiki_dir / "domains",
        link_style=str(wiki_cfg.get("link_style", "markdown_relative") or "markdown_relative"),
        generate_queries=bool(wiki_cfg.get("generate_queries", False)),
        generate_entities=bool(compiler_cfg.get("generate_entities", False)),
        generate_source_notes=bool(compiler_cfg.get("generate_source_notes", False)),
        generate_post_source_notes=bool(compiler_cfg.get("generate_post_source_notes", True)),
        simple_group_pages=bool(compiler_cfg.get("simple_group_pages", True)),
        theme_wins_overlap=bool(compiler_cfg.get("theme_wins_overlap", True)),
        concept_min_sources=int(compiler_cfg.get("concept_min_sources", 5)),
        concept_emerging_min_sources=int(compiler_cfg.get("concept_emerging_min_sources", 5)),
        concept_emerging_min_dates=int(compiler_cfg.get("concept_emerging_min_dates", 3)),
        concept_published_min_sources=int(compiler_cfg.get("concept_published_min_sources", 12)),
        concept_published_min_dates=int(compiler_cfg.get("concept_published_min_dates", 5)),
        entity_min_sources=int(compiler_cfg.get("entity_min_sources", 2)),
        theme_min_sources=int(compiler_cfg.get("theme_min_sources", 8)),
        theme_emerging_min_dates=int(compiler_cfg.get("theme_emerging_min_dates", 2)),
        theme_published_min_sources=int(compiler_cfg.get("theme_published_min_sources", 15)),
        theme_published_min_dates=int(compiler_cfg.get("theme_published_min_dates", 4)),
        evergreen_min_sources=int(compiler_cfg.get("evergreen_min_sources", 3)),
        recent_limit=int(compiler_cfg.get("recent_limit", 40)),
        stale_days=int(compiler_cfg.get("stale_days", 30)),
        review_days=int(compiler_cfg.get("review_days", 14)),
        high_signal_window_days=int(compiler_cfg.get("high_signal_window_days", 7)),
        post_max_groups=int(compiler_cfg.get("post_max_groups", 8)),
        post_group_min_size=int(compiler_cfg.get("post_group_min_size", 3)),
        post_tweets_per_group=int(compiler_cfg.get("post_tweets_per_group", 8)),
        concept_related_limit=int(relations_cfg.get("concept_related_limit", 24)),
        entity_related_limit=int(relations_cfg.get("entity_related_limit", 20)),
        theme_related_limit=int(relations_cfg.get("theme_related_limit", 30)),
        source_limit_per_section=int(relations_cfg.get("source_limit_per_section", 8)),
        # Do not read another Skill's .env. A compatibility env file, when
        # explicitly created, belongs only to this Skill's private data.
        shared_ai_env_file=resolve_data_path("provider.env", data_dir, "provider env"),
        entity_max_input_chars=int(ai_cfg.get("max_input_chars", 6000)),
        entity_max_count=int(ai_cfg.get("max_entities", 8)),
    )
