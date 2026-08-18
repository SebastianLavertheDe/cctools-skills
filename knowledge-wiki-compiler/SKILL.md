---
name: knowledge-wiki-compiler
description: Incrementally compile raw knowledge sources from `mymind/article` and `mymind/post` into a structured Markdown wiki under `mymind/wiki`. Use when Codex needs to build concept pages, theme pages, indexes, and lint reports over the local corpus.
allowed-tools: Bash,Write,Read
# desktop-app 执行配置
title: 知识库编译
group: 分析
cwd: knowledge-wiki-compiler
runner: uv
args:
  - run
  - python
  - main.py
permission: edit
outputArtifacts:
  - mymind/wiki/
timeoutMs: 3600000
---

Desktop App contract: use `cctools.skill.yaml` as the runnable contract.
`mymindRoot` is supplied by `CCTOOLS_MYMIND_ROOT`; compiler state is stored
below `CCTOOLS_SKILL_DATA_DIR`. The compiler does not read another Skill's
`.env` file. Its AI adapter is vendored inside the installed Python package
and does not import the workspace `_shared` directory.

# Knowledge Wiki Compiler

Builds a local Markdown knowledge base from the raw source layer:

- `mymind/article/`: long-form raw articles
- `mymind/post/`: social/timeline raw captures

The compiler writes a structured wiki to:

- `mymind/wiki/themes/` — reading navigation (broad topics)
- `mymind/wiki/concepts/` — glossary-style method/term pages
- `mymind/wiki/sources/posts/` — lightweight notes for X post batches only
- `mymind/wiki/index/` — browse indexes; `emerging.md` links to `_state/review/` queues
- `mymind/wiki/_state/` — registry metadata (tags, summaries) for all sources

Legacy empty dirs (`entities/`, `emerging/`, `domains/`, `families/`) are no longer created when entities are disabled.

Article metadata lives in `_state/registry.json`. Theme/concept pages link directly to `mymind/article/` (`generate_source_notes: false`).

Entity pages are **disabled by default** (`generate_entities: false`). When disabled, the compiler skips entity extraction, entity lifecycle state, and entity review artifacts.

## Quick Start

```bash
cd /path/to/cctools-skills/knowledge-wiki-compiler
uv sync
uv run python main.py
```

Useful flags:

```bash
uv run python main.py --force
uv run python main.py --only articles
uv run python main.py --only posts
uv run python main.py --limit 50
uv run python main.py --dry-run
uv run python main.py --only-taxonomy   # rebuild theme/concept pages from existing source notes
```

## Taxonomy model

- **Theme**: broad reading bucket. Promoted at 8+ sources (emerging) or 15+ sources across 4+ dates (published). Seeded themes from config still auto-publish.
- **Concept**: reusable AI method/term. **Only published** concepts get pages (12+ sources, 5+ dates, AI-scope). Emerging concepts stay in review reports only.
- **Overlap rule**: when a name exists as both theme and concept, **theme wins** — no duplicate concept page.
- **Simple pages** (`simple_group_pages: true`): template pages with overview + article links; no AI group summaries.

Manual overrides live in `mymind/wiki/_state/taxonomy_decisions.yaml`:

```yaml
merge:
  MCP: Model Context Protocol
promote_theme:
  - Agent Memory
reject:
  - Technical Documentation
```

## Notes

- LLM tagging uses the `default` provider from `config/providers.yaml` (no multi-provider failover).
- The wiki is designed to be easy to browse in Obsidian.
