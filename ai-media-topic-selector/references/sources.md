# Source Rules

## Default Source Constraint

Use only local files and user-provided content by default. Do not search the internet, open URLs, fetch webpages, or browse public sources unless the user explicitly allows internet use for the current run.

If the user provides a URL without pasted content, treat it as a reference string only. Do not open it unless the user explicitly says to browse or fetch links.

## Collection Order

1. User-provided materials
2. Local mymind sources for the target date:
   - `mymind/daily-summary/YYYYMMDD_daily_summary.md`
   - `mymind/post/YYYYMMDD/posts.json`
   - `mymind/reddit/YYYYMMDD/`
   - `mymind/article/YYYYMMDD/`
3. Recent local adjacent dates when the target date has too little material
4. Public web sources only when the user explicitly allows internet use

## Source Exclusions

Exclude WeChat Official Account / 微信公众号 articles before building the candidate hotspot pool or scoring candidates.

Treat an item as WeChat-sourced if its URL, source link, source file metadata, title/source label, or pasted source note contains any of:

- `mp.weixin.qq.com`
- `weixin.qq.com`
- `微信公众号`
- `微信公众平台`
- `微信公众`

Do not recover a WeChat-sourced article through adjacent-date lookup, article files, summaries, or platform-agent selection.

## Public Source Priorities

Use this section only when the user explicitly allows internet use.

Prefer:

- Official company blogs and docs: OpenAI, Anthropic, Google DeepMind, Meta AI, Microsoft, GitHub, Hugging Face, xAI, Mistral, Alibaba Qwen, Zhipu, MiniMax, Moonshot, ByteDance, Tencent, Baidu
- Product release notes, GitHub releases, model cards, technical reports, papers
- Named founder/researcher/executive posts
- Reputable technology media with clear sourcing
- Reddit/Hacker News/X posts only when clearly labeled as community signal or anecdotal evidence

## Reliability Labels

Use one of these labels in the candidate hotspot pool:

- `高`: official source, paper, release note, named direct statement, or verifiable product page
- `中`: reputable media report, multiple independent reports, or community data with visible evidence
- `低`: single social post, anonymous leak, screenshot, rumor, gossip, or unverifiable claim

## Risk Control

For every important claim:

- Keep the source link or local source file path.
- Do not convert rumor into fact.
- Mark uncertainty with phrases like `未确认`, `疑似`, `社区传闻`, `单一来源`, or `需要继续验证`.
- Avoid defamatory framing about people or companies.
- Avoid investment advice.
- Avoid definitive claims about unreleased products unless official sources confirm them.
- Avoid fake precision: do not invent exact user counts, revenue, benchmark scores, dates, prices, or roadmap details.

## Candidate Scoring

Score each candidate from 1-10:

- `传播潜力`: hook, shareability, emotional charge, public curiosity
- `写作价值`: depth, usefulness, originality, fit for the account
- `框架价值`: does it propose a reusable model, framework, methodology, or original insight about AI+human+organization dynamics? Higher score means the content can outlive the news cycle and be cited/referenced later
- `证据强度`: source quality and factual confidence
- `制作成本`: lower cost scores higher when material is ready
- `风险可控`: higher score means safer to write now

Do not pick the highest heat item automatically. Pick the topic with the best combined editorial value. A deep-thinking topic with high `框架价值` can outweigh a trending news item with higher `传播潜力` but lower long-term value.
