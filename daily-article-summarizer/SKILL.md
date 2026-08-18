---
name: daily-article-summarizer
description: Summarize daily articles from project-root mymind and write a daily Markdown summary. Use when Codex needs to scan today's saved article files and Reddit subreddit markdown files, generate AI summaries, cache results, and append them to `mymind/daily-summary/YYYYMMDD_daily_summary.md`.
allowed-tools: Bash,Write,Read
# desktop-app 执行配置
title: 每日文章总结
group: 分析
cwd: daily-article-summarizer
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
  - mymind/daily-summary/${date}_daily_summary.md
timeoutMs: 3600000
---

Desktop App contract: use `cctools.skill.yaml` as the runnable contract. The
Broker supplies `CCTOOLS_MYMIND_ROOT`, `CCTOOLS_SKILL_DATA_DIR`,
`CCTOOLS_RUN_DIR` and neutral `CCTOOLS_PROVIDER_*` bindings. Do not load a
sibling Skill's `.env`; summary cache belongs to Skill data, not the package
or mymind. The AI adapter is vendored under this Skill's `src/` package; the
installed package does not depend on the workspace `_shared` directory.

# Daily Article Summarizer

Automatically scans and summarizes daily articles from `mymind/article/` under the project root plus same-day Reddit subreddit markdown files from `mymind/reddit/`, then writes a daily Markdown summary.

## Features

- 📅 **Daily Scanning**: Automatically finds today's articles in `mymind/article/YYYYMMDD/`
- 🤖 **AI Summarization**: Uses `mimo-v2.5-pro` first, then falls back to `glm-5.1` and finally Doubao
- 💾 **Smart Caching**: Tracks summarized articles to avoid reprocessing
- 🧵 **Incremental X Post Summary**: Same-day `posts.json` is summarized per `tweet_id`; newly fetched posts are added on later runs
- 📝 **Markdown Output**: Appends each summary to `mymind/daily-summary/YYYYMMDD_daily_summary.md`

## Usage

```bash
cd /path/to/cctools-skills/daily-article-summarizer

# Install dependencies
uv sync

# Run the summarizer
uv run --env-file .env python main.py

# Only summarize Reddit posts for a date
uv run --env-file .env python main.py --date 20260428 --reddit-only
```

## Configuration

### 1. AI Provider (`config/providers.yaml`)

AI providers (API key, base URL, model, protocol) are configured once in `config/providers.yaml` — the single source of truth read by both desktop-app runs and crontab runs. Copy `config/providers.yaml.example` to `config/providers.yaml` and fill in real keys. The active provider is the file's `default` entry; per-skill provider overrides and multi-provider failover are not supported.

### 2. Config File (config.yaml)

```yaml
# Article source directory
article_directory:
  - "mymind/article"

# Reddit source directory
reddit_directory:
  - "mymind/reddit"

# AI settings (provider comes from config/providers.yaml; only batch_size is read here)
ai:
  batch_size: 5

# Cache file
cache_file: "summary_cache.json"
```

## How It Works

1. **Scan**: Reads all markdown files from `mymind/article/YYYYMMDD/` and parses Reddit posts from `mymind/reddit/YYYYMMDD/*.md`
2. **Filter**: Checks cache to skip already summarized article and Reddit items
3. **Summarize**: Uses the `default` provider from `config/providers.yaml`
4. **Cache**: Saves article/Reddit summaries plus same-day post summaries to `summary_cache.json`
5. **Write**: Appends each summary to the daily Markdown summary file
6. **Finish**: Leaves downstream delivery to other workflow steps

## Output

### Writing Style

Apply the same "Kill the AI Tone" rule from `creative-writing` to all generated summaries:

- Never use templated phrasing like `不是...但是...`, `应该...而非...`, `在于...而非...`, `不在于...而在于...`, `不...而...`, `不...而是...`, `不...而在于...`, `更多是...而非...`, `不应是...而应该是...`, `不只是...还有...`, `不只有...还有...`, `不是...而是...`, `不再是...而是...`, `之所以...是因为...`, or `既是...也是...`.
- Avoid sentences that first negate something and then pivot with `而` / `而是` / `而在于`.
- State the point directly. Use causal links such as `因为` / `所以`.
- Prefer concrete scenes, examples, product names, actions, and conclusions over abstract principles.
- Write like talking to a friend: natural Chinese, short sentences, no mechanical list-like phrasing inside `summary` fields.

### Cache File Structure

```json
{
  "20260130": {
    "article_file.md": {
      "summary": "...",
      "key_points": ["...", "..."],
      "category": "AI",
      "score": 85,
      "processed_at": "2026-01-30 10:30:00"
    }
  }
}
```

The daily Markdown file contains grouped summaries with:
- **Title**
- **Score**
- **Summary**
- **Key points**
- **Link**
