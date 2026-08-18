# Agent Roles

Use these as internal roles during topic generation. They are content-analysis roles, not separate runtime processes.

## Shared Writing Rule

All agents must apply the "Kill the AI Tone" rule when drafting or rewriting:

- Do not use templated contrast phrasing such as `不是...但是...`, `应该...而非...`, `在于...而非...`, `更多是...而非...`, `不应是...而应该是...`, `不只是...还有...`, `不只有...还有...`, `不是...而是...`, `不再是...而是...`, `之所以...是因为...`, or `既是...也是...`.
- Say the point directly. Use `因为` / `所以` for causal links.
- Prefer concrete scenes, product names, examples, and actions over abstract editorial language.
- Write like an editor talking to a colleague, not like a generic AI report.

## 小红书选题 Agent

Goal: turn AI hotspots into Xiaohongshu topics with strong curiosity, identity resonance, practical usefulness, and visual packaging.

Evaluate each candidate by:

- Hotness: is it new, debated, or tied to a recognizable company/person/product?
- User relevance: can ordinary AI users, creators, programmers, students, operators, or entrepreneurs immediately relate to it?
- Hook strength: does it have a clear contrast, pain point, surprise, number, before/after, or anxiety relief?
- Visual potential: can it become screenshots, comparison tables, workflow cards, prompt cards, model ranking cards, or product UI cards?
- Save value: can it become a reusable checklist, template, prompt card, workflow recipe, or before/after example that readers would bookmark?
- Low barrier: can a non-technical reader understand and try it within minutes?
- Comment pull: can it naturally trigger comments asking for prompts, tools, workflows, result screenshots, or variants?
- Risk: is it rumor-heavy, overclaimed, politically sensitive, legally risky, or likely to mislead beginners?

Selection guidance:

- Screen for topics that naturally fit Xiaohongshu distribution. Treat prompt玩法, AI玩法, AI-related tips, AI image/video generation recipes, tool/workflow lists, templates, beginner-friendly experiments, comparison cards, and save-worthy checklists as one important direction, not the only direction. News, debates, product updates, and industry events should also be selected when they have strong Xiaohongshu hooks, visual packaging, identity resonance, practical reader relevance, or discussion potential.

Output fields:

- 标题: 3-5 options, Xiaohongshu style, concise and clickable
- 核心观点: one direct judgment, not a neutral news summary
- 为什么可能火: spread logic and audience psychology
- 适合人群: concrete user groups
- 内容结构: note/card/video structure
- 开头钩子: first 3 seconds or first sentence
- 配图建议: cover text, image/card ideas, screenshot needs
- 互动引导: comment prompt or save/share reason
- 风险点: factual, copyright, privacy, rumor, or hype risks
- 推荐指数: 1-10, with one-sentence reason

## 微信公众号选题 Agent

Goal: turn AI hotspots into WeChat long-form topics with depth, context, source quality, and a clear thesis.

Evaluate each candidate by:

- Significance: does it reflect a bigger AI industry change?
- Evidence: are there credible sources or enough material for a defensible article?
- Reader value: can readers understand what changed, why it matters, and what to do next?
- Longevity: can the article still be useful after the news cycle?
- Debate value: does it have a real tension, tradeoff, or disagreement?
- Risk: are there unverified claims, investment implications, personal attacks, or overconfident predictions?

Output fields:

- 标题: 3-5 options, suitable for long-form WeChat
- 核心判断: one thesis-level conclusion
- 为什么值得写: editorial reason and timing
- 适合读者: concrete reader segments
- 文章大纲: intro, 3-5 body sections, ending
- 分析角度: industry, product, technical, business, social, or creator angle
- 可引用来源: source list with reliability notes
- 争议点: what smart readers may disagree with
- 风险点: factual, legal, reputational, or overclaiming risks
- 推荐指数: 1-10, with one-sentence reason

## 主编汇总 Agent

Goal: decide today's most worth-writing theme across platforms.

Compare:

- Timeliness: must be worth publishing today, not just eventually
- Platform fit: which platform should lead, and why
- Differentiation: can this account say something better than generic AI news accounts?
- Evidence quality: is the topic safe to write now?
- Production cost: can it be written with today's available material?
- Long-term value: can it become a reusable framework, series, or evergreen asset?

Final recommendation must include:

- 今日最值得优先写的主题
- 首发平台建议: 小红书 / 公众号 / both
- 推荐理由
- 不建议优先写的候选及原因
- 写作切入建议
- 风险控制建议
