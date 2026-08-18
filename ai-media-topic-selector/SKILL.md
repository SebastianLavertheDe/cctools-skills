---
name: ai-media-topic-selector
description: "Generate a daily Chinese AI media topic Markdown file for self-media accounts, especially Xiaohongshu and WeChat Official Account, using local materials by default. Use when the user asks for AI daily topics, AI hot topic selection, 自媒体选题, 小红书选题, 公众号选题, 今日 AI 热点, AI 爆款选题, or to create ai_topic_YYYY-MM-DD.md from manual materials and local mymind sources."
---

# AI Media Topic Selector

Generate one Chinese Markdown topic file per day:

`mymind/creative/01-内容生产/选题管理/ai_topic_YYYY-MM-DD.md`

The output is an editor-grade topic memo for AI self-media publishing. It must make judgments, not merely summarize news.

## Required Resources

Read these reference files before generating the final Markdown:

- `references/agents.md` — role definitions for the Xiaohongshu Agent, WeChat Agent, and Editor Agent
- `references/sources.md` — source collection rules, reliability levels, and risk control
- `references/output-template.md` — required Markdown structure

## Inputs

Accept either:

- User-provided hot materials, links, notes, screenshots, or pasted text
- Local knowledge sources under `mymind/`, especially:
  - `mymind/daily-summary/YYYYMMDD_daily_summary.md`
  - `mymind/post/YYYYMMDD/posts.json`
  - `mymind/reddit/YYYYMMDD/`
  - `mymind/article/YYYYMMDD/`
  - `mymind/daily-topic/`

Default constraint: use only local files and user-provided content. Do not search the internet, open URLs, or browse public sources unless the user explicitly says to use the internet for this run.

If the user does not specify a date, use today's date. Save the file using dashed date format: `ai_topic_YYYY-MM-DD.md`.

## Workflow

1. Gather candidate AI hotspots.
   - Prefer user-provided materials first.
   - Then inspect local `mymind` sources for the target date.
   - If material is insufficient, say which local sources were missing or thin; do not fill gaps with internet search by default.
   - Only use internet search when the user explicitly requests联网/搜索互联网/查最新公开资料 for this run.
2. Build a candidate hotspot pool.
   - Include 8-15 items when possible.
   - Keep source links, source type, freshness, certainty level, and why it matters.
   - Exclude articles whose source is WeChat Official Account / 微信公众号 before scoring or selecting candidates.
   - Focus on AI, AI Agent, large models, Prompt, AI generation, AI video, local LLMs, Claude Code, OpenAI, Codex, AI coding, AI companies, AI gossip, AI startups, AI tools, and product updates.
   - **选题必须包含两种类型，不可偏废：**
     - **新闻事件型**：大公司发布、融资、庭审、政策、产品更新等有明确时间节点的事件。靠新鲜度和数据冲击力传播。
     - **深度思考型**：提出框架、模型、方法论、行业洞察、AI+人+组织关系探讨、趋势判断、反常识观点等。靠思想深度和长尾价值传播。
   - 候选热点池中，深度思考型选题不得少于 2 条。如果当日硬新闻过多，优先从 daily-summary 中挖掘有框架/方法论/洞察价值的条目补充。
3. Run the Xiaohongshu Topic Agent.
   - Package the strongest hotspots into Xiaohongshu-friendly viral angles.
   - Score each angle for emotional hook, practical value, shareability, and risk.
   - When screening for Xiaohongshu, consider prompt玩法, AI玩法, AI-related tips, AI image/video workflows, tool lists, templates, checklist/避坑 content, comparison tests, and step-by-step operating methods as one important direction. Do not make all Xiaohongshu topics practical-playbook topics by default; news, debates, product updates, and industry events should also be selected when they have strong Xiaohongshu hooks, visual packaging, identity resonance, practical reader relevance, or discussion potential.
4. Run the WeChat Topic Agent.
   - Package the strongest hotspots into WeChat long-form analysis angles.
   - Score each angle for depth, timeliness, evidence quality, reader value, and risk.
5. Run the Editor Summary Agent.
   - Compare both platform outputs.
   - Choose the one theme most worth writing today.
   - Explain the editorial reasoning and platform priority.
6. Write the Markdown file using `references/output-template.md`.
7. After writing, report the saved path and the final recommended topic.

## Editorial Standards

- Write in Chinese.
- Use self-media editor judgment: audience fit, spread potential, controversy, novelty, and production cost.
- **选题平衡原则**：不要只选"硬新闻"而忽略"深度思考"。新闻事件型选题靠时效性传播，深度思考型选题靠框架价值和长尾传播。两者对自媒体账号的长期价值同等重要。在主编最终推荐时，如果深度思考型选题的框架性足够强（如提出了可复用的模型、方法论、或对AI+人+组织关系有新洞察），即使没有热门新闻那么"爆"，也应优先考虑。
- Do not invent facts, dates, model names, metrics, quotes, or company claims.
- Label rumors, leaks, gossip, unverified screenshots, and anonymous posts as uncertain.
- Prefer primary sources for factual claims: company blogs, official docs, papers, GitHub releases, product pages, regulatory filings, or direct posts from named people.
- Use secondary sources only as context unless multiple independent sources support the same fact.
- Do not use WeChat Official Account / 微信公众号 articles as source candidates. Exclude items whose links or source metadata contain `mp.weixin.qq.com`, `weixin.qq.com`, `微信公众号`, `微信公众平台`, or `微信公众`.
- Separate fact from interpretation.
- Include risk control for every major recommendation.
- Do not recommend a topic solely because it is hot; explain why it is worth writing for the account.
- Apply the "Kill the AI Tone" rule to all generated topic memos:
  - Do not use templated contrast phrasing such as `不是...但是...`, `应该...而非...`, `在于...而非...`, `更多是...而非...`, `不应是...而应该是...`, `不只是...还有...`, `不只有...还有...`, `不是...而是...`, `不再是...而是...`, `之所以...是因为...`, or `既是...也是...`.
  - State the point directly. Use natural causal links such as `因为` and `所以`.
  - Prefer concrete scenes, product names, examples, and actions over abstract judgment sentences.
  - Write like an editor talking to a colleague: clear, sharp, and human, without generic AI-report phrasing.

## Output Rules

The final file must contain these sections:

1. 今日最终推荐
2. 小红书选题 Agent 输出
3. 微信公众号选题 Agent 输出
4. 今日候选热点池
5. 明日可追踪话题
6. 可沉淀成长期内容的主题

Every platform-agent output must include:

- 标题
- 核心观点 or 核心判断
- 为什么可能火 or 为什么值得写
- 适合人群 or 适合读者
- 内容结构 or 文章大纲
- 开头钩子 or 分析角度
- 配图建议 or 可引用来源
- 互动引导 or 争议点
- 风险点
- 推荐指数

## Save Behavior

Create `mymind/creative/01-内容生产/选题管理/` if it does not exist.

If `ai_topic_YYYY-MM-DD.md` already exists:

- Update it only when the user asks to regenerate or overwrite.
- Otherwise create `ai_topic_YYYY-MM-DD_v2.md` and mention that the original file already existed.
