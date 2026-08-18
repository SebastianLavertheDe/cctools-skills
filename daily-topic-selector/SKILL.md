---
name: daily-topic-selector
description: Filter AI-related topics from mymind daily-summary files for WeChat Official Account, and also summarize the same day's X/Twitter posts from mymind/post/YYYYMMDD/posts.json. Use when user asks to pick AI topics, choose daily publishing angles, generate AI-focused daily topic markdown, or output filtered topic blocks with links and source files.
allowed-tools: Bash,Read,Write
# desktop-app 执行配置
title: 每日选题
group: 创作
cwd: .
runner: python
args:
  - daily-topic-selector/scripts/generate_daily_topics.py
  - --date
  - ${date}
  - --env-file
  - ../daily-article-summarizer/.env
permission: edit
outputArtifacts:
  - mymind/daily-topic/${date}_daily_topic.md
timeoutMs: 1800000
---

Desktop App contract: use `cctools.skill.yaml` as the runnable contract.
Resolve content below the Broker-bound `CCTOOLS_MYMIND_ROOT`; put LLM cache
below `CCTOOLS_SKILL_DATA_DIR` and receive Provider Profile values through
neutral `CCTOOLS_PROVIDER_*` bindings. The AI adapter is vendored beside the
entrypoint, so an installed package does not import the workspace `_shared`
directory.

# Daily Topic Selector

Select topics from `mymind/daily-summary/YYYYMMDD_daily_summary.md` and generate filtered AI-topic output for:
- 微信公众号

Always include `**链接**:` and `**源文件**:` for every selected topic.
Preserve `**作者**:` when it exists in the daily summary entry.
Use AI-only analysis (no keyword fallback).
Also summarize the same day's `mymind/post/YYYYMMDD/posts.json` into a short "当天 Post 总结" section when available.
If the source daily summary contains Reddit items, apply the same topic-selection rules to them and render selected items in the compact `Reddit 热帖` format used by `daily-article-summarizer`.

## Usage

```bash
cd /path/to/cctools
python3 daily-topic-selector/scripts/generate_daily_topics.py
```

Optional date:

```bash
python3 daily-topic-selector/scripts/generate_daily_topics.py --date 20260226
```

Optional counts:

```bash
python3 daily-topic-selector/scripts/generate_daily_topics.py --top-wechat 3
```

Default behavior:
- No fixed count limit (`top-wechat=0` by default)
- Select all suitable topics above AI scoring thresholds:
  `fit` + `AI relevance` + `viral potential` + `AI breakout potential`
- Exclude articles whose source is WeChat Official Account / 微信公众号 before AI selection
- Summarize same-day X/Twitter posts from `mymind/post/YYYYMMDD/posts.json`
- Filter same-day Reddit items with the normal AI topic-selection rules and render selected items in compact Reddit format
- Do not cap candidate posts by default (`post-candidate-limit=0` means no cap)
- Do not cap same-author post count during post recall
- Priority post authors are only soft-priority signals, not hard filters; non-priority authors can still be selected if the post quality is higher
- Prefer these AI directions: `AI技巧`、`AI使用技巧`、`AI产品使用技巧`、`AI领域新产品`、`AI新模型发布`、`AI Agent相关`、`AI领域新闻`、`AI公司相关的新闻`、`Prompt使用技巧`、`AI开源项目`、`AI+行业相关新闻`、`AI+伦理`、`AI+安全`、`AI领域易于传播的爆款`
- Extra priority entities and themes: `Claude Code`、`Cursor`、`LangChain`、`Codex`、`Anthropic`、`OpenAI`、`GLM`、`MiniMax`、`AI Agent Harness`、`AI工程`
- These priority terms are only provided to the model as context. They do not trigger deterministic keyword retention.

Optional threshold override:

```bash
python3 daily-topic-selector/scripts/generate_daily_topics.py \
  --min-breakout-score 7
```

Optional env file (the provider itself comes from `config/providers.yaml`):

```bash
python3 daily-topic-selector/scripts/generate_daily_topics.py \
  --env-file ../daily-article-summarizer/.env
```

Optional post summary controls:

```bash
python3 daily-topic-selector/scripts/generate_daily_topics.py \
  --post-candidate-limit 0
```

```bash
python3 daily-topic-selector/scripts/generate_daily_topics.py \
  --no-post-summary
```

## AI Configuration

The AI provider comes from `config/providers.yaml` (`default` entry) — the same source as `daily-article-summarizer`. The `--provider` flag is accepted for backward compatibility but ignored. There is no multi-provider failover; the single `default` provider is used.

## Output

The script writes:

`mymind/daily-topic/YYYYMMDD_daily_topic.md`

### Writing Style

Apply the same "Kill the AI Tone" rule from `creative-writing` to topic summaries and post summaries:

- Never use templated phrasing like `不是...但是...`, `应该...而非...`, `在于...而非...`, `不在于...而在于...`, `不...而...`, `不...而是...`, `不...而在于...`, `更多是...而非...`, `不应是...而应该是...`, `不只是...还有...`, `不只有...还有...`, `不是...而是...`, `不再是...而是...`, `之所以...是因为...`, or `既是...也是...`.
- Avoid sentences that first negate something and then pivot with `而` / `而是` / `而在于`.
- State the point directly. Use causal links such as `因为` / `所以`.
- Prefer concrete scenes, examples, product names, actions, and conclusions over abstract principles.
- Write like talking to a friend: natural Chinese, short sentences, no mechanical list-like phrasing inside generated summaries.

Output structure:
- Daily file header
- A short "当天 Post 总结" section from `mymind/post/YYYYMMDD/posts.json`
- A filtered `Reddit 热帖` section rendered in the same compact style as `daily-article-summarizer`
- Source summary reference
- Filtered AI topic blocks copied in daily-summary style
- `**作者**:` when present on the source daily-summary item
- `**链接**:` and `**源文件**:` on each topic item

## Workflow

1. Read `mymind/daily-summary/YYYYMMDD_daily_summary.md`.
2. Parse each topic item: title, score, summary, key points, link, source file.
3. Remove topics whose article source is WeChat Official Account / 微信公众号.
4. If present, read `mymind/post/YYYYMMDD/posts.json`, pick high-signal posts, and ask AI to generate a short daily post summary.
5. Ask AI to filter items that are truly AI-related and suitable for publication, using the same AI provider chain as `daily-article-summarizer`.
6. If Reddit items are selected, render them as a compact Reddit hot-post section grouped by subreddit.
7. Keep the selected non-Reddit topics in daily-summary order and preserve their original summary content.
8. Save markdown to `mymind/daily-topic/`.
