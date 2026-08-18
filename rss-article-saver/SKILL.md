---
name: rss-article-saver
description: RSS article subscription, saves articles as local Markdown
allowed-tools: Bash,Write,Read
# desktop-app 执行配置
title: RSS 文章抓取
group: 采集
cwd: rss-article-saver
runner: uv
args:
  - run
  - --env-file
  - .env
  - python
  - main.py
envFile: .env
permission: edit
outputArtifacts:
  - mymind/article/${date}/
timeoutMs: 7200000
---

Desktop App contract: use `cctools.skill.yaml` as the runnable contract.
Article/post output is resolved below `CCTOOLS_MYMIND_ROOT`, while cache and
counter state are below `CCTOOLS_SKILL_DATA_DIR`; Provider Profile values
arrive through neutral `CCTOOLS_PROVIDER_*` bindings. AI adapters are
vendored inside this Skill's `src/` package and do not import the workspace
`_shared` directory.

# RSS Article Saver

Subscribes to RSS feeds (configured via OPML) and saves articles as local Markdown files (with images).

## Features

- 📡 **RSS Support**: Subscribe to feeds via OPML file
- 📝 **Markdown Export**: Saves articles as Markdown with embedded images to `mymind/article/` under the project root
- 🔄 **Deduplication**: Skips already processed articles using cache
- 🖼️ **Image Support**: Extracts and includes article images

## Usage

```bash
cd /path/to/cctools-skills/rss-article-saver
uv sync
uv run --env-file .env python main.py
```

## Configuration

### 1. OPML File (subscriptions.opml)

Define your RSS subscriptions in OPML format:

```xml
<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<opml version="2.0">
    <head>
        <title>My RSS Subscriptions</title>
    </head>
    <body>
        <outline text="ByteByteGo Newsletter" type="rss" xmlUrl="https://blog.bytebytego.com/feed"/>
        <outline text="Last Week in AI" type="rss" xmlUrl="https://lastweekin.ai/feed/"/>
        <!-- Add more feeds here -->
    </body>
</opml>
```

You can also keep large feed collections in a separate local OPML file and reference it from the main `subscriptions.opml`:

```xml
<outline
    text="WeChat Tech and AI Feeds"
    title="WeChat Tech and AI Feeds"
    type="opml"
    xmlUrl="subscriptions.wechat.opml"
/>
```

This keeps the main subscription file small while letting the monitor expand nested local OPML files automatically.

### 2. Config File (config.yaml)

Adjust settings in `config.yaml`:
- `opml_file`: Path to your OPML file
- `max_articles_per_feed`: Max articles per feed (default: 999)
- `ai.enabled`: AI features disabled by default

The AI provider itself is configured in `config/providers.yaml` (repo root), not here.

## Output

### Saved Articles

Articles are saved to `mymind/article/` under the project root as Markdown files:
```
YYYYMMDD_HHMMSS_Article Title.md
```

Each article contains:
- **元数据**: Link, author, published date, saved time
- **文章图片**: List of image URLs
- **正文**: Full content with images embedded in Markdown format

### Deduplication

- Uses `article_cache.json` to track processed articles
- Already processed articles are skipped automatically
