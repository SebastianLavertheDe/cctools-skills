# AI Knowledge Wiki Taxonomy Engineering Plan

## 目标

本计划面向当前 `knowledge-wiki-compiler` 的工程化重构。目标不是做一个大而全的 Wikipedia，也不是做一个静态白名单知识库，而是把当前自动标签聚合器升级为一个长期可维护、能持续吸收新概念的 AI 个人知识库编译系统。

核心目标：

- wiki 只围绕 AI 相关知识展开，保留清晰边界。
- 新概念、新主题、新实体可以进入候选和趋势层，不会被固定白名单挡掉。
- 正式页面必须高质量、可读、可解释，不再输出几百上千个全量共现关系。
- taxonomy 有生命周期：发现、候选、趋势、发布、合并、废弃。
- 所有自动判断都有证据、评分、状态和 review 报告。
- 迁移过程可回滚，不一次性破坏现有 wiki。

非目标：

- 不做封闭 curated seed。
- 不把 stable themes 固化成永远不变的列表。
- 不靠手工维护所有概念。
- 不继续把所有共现标签直接发布成正式页面。

## 当前问题摘要

当前 pipeline 接近：

```text
raw source
-> extract terms
-> normalize
-> count threshold
-> generate concept/entity/theme pages
```

这个流程高召回、低精度，适合生成标签云，但不适合维护 AI 知识库。

主要问题：

- `concepts` 过度膨胀：普通背景词、近义词、细碎表达都会长成正式页。
- `entities` 被概念污染：如 `AI Alignment`、`Prompt Injection`、`Reinforcement Learning` 这类概念可能生成 entity 页。
- `themes` 与 `concepts` 重叠：`AI Safety`、`RAG`、`Reasoning` 等既像主题又像概念。
- 相关关系全量输出：核心页中出现数百个相关概念，页面失去知识组织价值。
- source 重复污染统计：重复标题和重复 canonical URL 会抬高 source_count，推动低质量节点晋级。
- 缺少趋势层：新概念要么直接污染正式页，要么被规则排除，没有可观察的中间状态。

## 目标架构

目标 pipeline：

```text
raw source
-> source normalization and dedupe
-> source quality scoring
-> AI scope gate
-> term extraction
-> taxonomy candidate registry
-> lifecycle scoring
-> canonical merge and alias resolution
-> relation edge generation
-> published page rendering
-> review reports
-> lint and regression checks
```

核心模块：

```text
knowledge_wiki_compiler/
  taxonomy/
    registry.py
    scope.py
    scoring.py
    lifecycle.py
    canonical.py
    relations.py
    review.py
  source_quality.py
  page_renderer.py
  linting.py
```

建议优先拆分，但可以先在现有 `app.py` 中做过渡实现，再逐步抽模块。

## 当前代码改造地图

当前实现集中在 `src/knowledge_wiki_compiler/app.py`，可以按下面的映射逐步拆出模块。这个列表是工程执行时的直接入口。

```text
compile_wiki()
  保持主编排入口，但插入 source quality、taxonomy registry、review decision、relation state。

generate_aggregate_pages()
  现在直接生成 concept/entity/theme/query/index。
  改为：构建 source registry -> taxonomy registry -> lifecycle -> relation state -> published pages -> indexes/review/lint。

build_concept_lifecycle()
  替换为 taxonomy/lifecycle.py 中的 concept lifecycle。
  状态从 approved/provisional/candidate/deprecated 迁移为 candidate/emerging/published/merged/deprecated/rejected。

filter_concept_index_for_formal_pages()
filter_entity_index_for_formal_pages()
  重命名为 filter_*_for_published_pages()。
  只允许 published_concept / published_entity 生成正式页面。

dedupe_article_entries()
dedupe_records()
normalized_canonical_url_key()
  扩展为 source_quality.py + source_registry.py。
  去重从 source_id 扩展到 canonical duplicate group。

is_ai_related_concept()
is_ai_related_entity()
record_has_ai_signal()
  迁移到 taxonomy/scope.py。
  输出 core_ai / adjacent_ai / background_context / out_of_scope。

is_concept_like_entity()
entity_detail_for_record()
common_entity_type()
  迁移到 taxonomy/entity_policy.py 或 lifecycle.py 的 entity policy 部分。

normalize_concept_name()
normalize_theme_name()
entity_name_key()
taxonomy_name_key()
  迁移到 taxonomy/canonical.py，统一 alias、case、punctuation 和 canonical key。

related_term_items()
related_link_lines()
wiki_links()
source_links()
rewrite_group_links()
rewrite_source_note_links()
  用 page_renderer.py + LinkRenderer 替换。
  默认 Markdown relative link，不再无条件改写为 Obsidian wikilink。

write_queries()
render_query_record()
AppConfig.queries_dir
  移除或停止生成。
  recent signals、watchlist、stale knowledge、evergreen candidates 这些内容改为写入对应 concept/entity/theme 页面中的“近期信号”“建议先读”“待复看”段落。

write_indexes()
  保留，但只做导航入口，不承载全量关系查询。

write_lint_report()
write_taxonomy_candidate_report()
  扩展为 linting.py + taxonomy/review.py。
```

### 配置迁移

现有 `config.yaml` 应新增：

```yaml
wiki:
  root_dir: "mymind/wiki"
  link_style: markdown_relative
  generate_queries: false

taxonomy:
  require_review_for_publish: true
  concept_published_min_sources: 5
  concept_emerging_min_sources: 3
  entity_published_min_sources: 5
  entity_emerging_min_sources: 3
  stable_theme_target_min: 30
  stable_theme_target_max: 50

relations:
  concept_related_limit: 24
  entity_related_limit: 20
  theme_related_limit: 30
  source_limit_per_section: 8
```

`generate_queries` 只用于迁移期兼容旧目录，最终默认 false，且不再创建 `mymind/wiki/queries/`。

## 数据模型

### Taxonomy Registry

新增：

```text
mymind/wiki/_state/taxonomy_registry.json
```

每个 taxonomy node 结构：

```json
{
  "id": "concept:ai-safety",
  "type": "concept",
  "canonical_name": "AI Safety",
  "aliases": ["AI safety", "AI 安全"],
  "status": "published",
  "layer": "published_concept",
  "scope": "core_ai",
  "importance": "core",
  "stability": "established",
  "parent_ids": ["theme:ai-safety-governance"],
  "child_ids": ["concept:red-teaming", "concept:model-alignment"],
  "merged_into": null,
  "source_count": 284,
  "unique_source_count": 271,
  "date_count": 37,
  "velocity_7d": 12,
  "velocity_30d": 48,
  "confidence": 0.91,
  "novelty_score": 0.34,
  "genericness_score": 0.08,
  "first_seen": "2026-05-01",
  "last_seen": "2026-06-15",
  "evidence_source_ids": [],
  "review_state": "human_accepted",
  "review_notes": []
}
```

### Node Types

```text
concept
entity
theme
source_topic
```

`source_topic` 用于短期趋势和临时聚类，不直接生成正式 wiki 页面。

### Status

```text
candidate
emerging
published
merged
deprecated
rejected
```

含义：

- `candidate`: 被发现，但证据不足，只进入候选报告。
- `emerging`: 最近增长快、AI 相关性强，进入趋势报告，可生成轻量趋势页。
- `published`: 已进入正式 wiki 的概念页或实体页。
- `merged`: 合并到 canonical node。
- `deprecated`: 曾经有效但现在过时或命名不再推荐。
- `rejected`: 明确不属于 AI 知识库或低价值噪声。

Theme 可以使用 type-specific layer，如 `theme_domain`、`stable_theme`、`emerging_theme`。Concept 和 entity 不再拆“稳定/正式”两套发布状态。

### Scope

```text
core_ai
adjacent_ai
background_context
out_of_scope
```

含义：

- `core_ai`: 直接解释 AI 技术、产品、模型、工作流、风险、产业结构。
- `adjacent_ai`: 与 AI 强相关，但本身不是 AI 核心概念，例如 `Copyright Law` 在 AI 版权语境下。
- `background_context`: 仅作为文章背景出现，不生成正式页。
- `out_of_scope`: 不纳入 AI 知识库。

## Phase 1: Source 去重与质量分层

### 目标

避免重复 source 抬高概念和实体的统计权重。

Source 是整个 AI 知识库的证据层，不只是原文归档。Concept、Entity、Theme 的所有判断都应该能回到 source。Source 层需要同时解决四件事：

- 证据可信度：这篇材料是否值得影响 taxonomy。
- AI 相关性：这篇材料是核心 AI 材料、AI 邻近材料，还是普通背景材料。
- 时间语义：材料发生时间、发布时间、收录时间、确认时间不能混用。
- 去重归并：同一 URL、同一论文、同一 GitHub repo、同一 X thread 的重复记录不能重复计权。

### 输入

- `mymind/wiki/sources/**/*.md`
- source frontmatter:
  - `source_id`
  - `canonical_url`
  - `title`
  - `display_title`
  - `source_path`
  - `date`

### 输出

```text
mymind/wiki/_state/source_quality.json
mymind/wiki/_state/source_duplicates.json
mymind/wiki/_state/source_registry.json
mymind/wiki/_state/review/source_quality_review.md
```

### Source Layer

Source 不建议按 candidate/emerging/published 生成独立阅读目录，而应该按证据角色分层：

```text
primary_source: 原始论文、官方公告、代码仓库、系统卡、产品文档、监管文件。
analysis_source: 高质量分析、访谈、深度报道、研究解读。
signal_source: 社交媒体、新闻、趋势信号、社区讨论。
reference_source: 背景资料、历史材料、术语解释。
duplicate_source: 重复记录，不参与统计计权。
failed_source: 抽取失败、被拦截、内容缺失。
out_of_scope_source: 非 AI 相关材料，只保留归档，不影响 AI taxonomy。
```

### Source Scope

每个 source 也需要 scope：

```text
core_ai_source
adjacent_ai_source
background_source
out_of_scope_source
```

示例：

- OpenAI system card、Anthropic model release、arXiv AI paper: `core_ai_source`
- 数据中心能源、版权诉讼、监管政策: 取决于内容，可为 `adjacent_ai_source`
- 天涯社区恢复访问这类历史互联网材料: 如果重点是 AI 数据资产、UGC 数据复用，可为 `adjacent_ai_source`；否则为 `background_source`
- 与 AI 无关的普通新闻或历史材料: `out_of_scope_source`

Source scope 影响后续权重：

```text
core_ai_source: 可推动 concept/entity/theme 晋级。
adjacent_ai_source: 只能推动 adjacent 或 emerging，通常需要更多证据。
background_source: 只提供背景引用，不推动正式 taxonomy。
out_of_scope_source: 不参与 AI taxonomy。
```

### Source 时间语义

当前 source 里已有 `date`、`first_seen`、`last_seen`、`last_confirmed`，但需要明确语义：

```text
event_date: 事件发生或材料主题对应的时间。
published_date: 原文发布时间。
captured_date: 被本地系统收录的时间。
compiled_at: 被 wiki 编译的时间。
last_seen: 最近一次在输入源中看到。
last_confirmed: 最近一次人工或自动确认仍有效。
revisit_after: 建议复看时间。
```

现有 `date` 应逐步拆成 `event_date` 和 `published_date`。如果无法区分：

- 文章发布时间明确时，写入 `published_date`。
- 历史事件材料可以保留 `event_date`。
- 本地收录时间必须写入 `captured_date`，不应用 `date` 代替。

这可以避免旧历史材料因为今天被收录而影响“最新趋势”，也可以避免新收录的旧材料被误认为新趋势。

### Source 目录建议

物理目录可以保持兼容：

```text
mymind/wiki/sources/articles/YYYYMMDD/
mymind/wiki/sources/posts/YYYYMMDD/
```

但建议新增 index 和 registry，而不是把 source 物理目录拆得过碎：

```text
mymind/wiki/sources/index/
  by-date.md
  by-source-type.md
  by-quality.md
  by-scope.md
  primary-sources.md
  high-signal-sources.md
  failed-sources.md
  duplicate-sources.md
```

机器状态放在：

```text
mymind/wiki/_state/source_registry.json
mymind/wiki/_state/source_quality.json
mymind/wiki/_state/source_duplicates.json
```

不建议把 source 文件移动到 `primary/analysis/signal` 等目录，因为 source 的角色可能变化，移动文件会破坏链接。角色应由 frontmatter 和 registry 表达。

### 去重 key

按强到弱：

```text
canonical_url_normalized
arxiv_id
github_repo_key
source_id
title_normalized + date
content_hash
```

补充 canonical normalizer：

```text
arxiv: 统一 abs/pdf/version URL。
github: 统一 repo URL，忽略 query、fragment、大小写差异。
youtube: 统一 video id。
x/twitter: 统一 status id。
medium/substack/newsletter: 去掉 tracking query。
wechat: 使用 biz + mid + idx + sn。
generic web: 去掉 utm、ref、spm、fbclid 等 tracking 参数。
```

### Source Quality Status

```text
primary
high_signal
normal
low_signal
duplicate
blocked_or_failed
out_of_scope
```

### Source Weight

不同 source 不应同权影响 taxonomy：

```text
primary_source: 1.0 到 1.5
analysis_source: 0.8 到 1.2
signal_source: 0.3 到 0.8
reference_source: 0.2 到 0.6
duplicate_source: 0
failed_source: 0
out_of_scope_source: 0
```

权重不是阅读价值判断，而是 taxonomy 晋级权重。社交信号可以推动 emerging，但不应单独把一个概念推成 published。

### Source Frontmatter 建议字段

新增或规范化：

```yaml
type: source
source_kind: article | post | paper | repo | doc | video | podcast | thread
source_role: primary_source | analysis_source | signal_source | reference_source
source_scope: core_ai_source | adjacent_ai_source | background_source | out_of_scope_source
quality: primary | high_signal | normal | low_signal | duplicate | failed | out_of_scope
duplicate_of: ""
canonical_group_id: ""
event_date: ""
published_date: ""
captured_date: ""
compiled_at: ""
source_weight: 1.0
primary_subjects: []
evidence_for:
  concepts: []
  entities: []
  themes: []
```

`primary_subjects` 用来区分“正文主体”和“顺带提及”。只有主体对象才应强力推动 entity 晋级。

### 工程任务

- 新增 `source_quality.py`。
- 在 aggregate 前先加载 source quality。
- `dedupe_records()` 不仅按 source_id，还要按 canonical duplicate group。
- 重复 source 不参与 lifecycle 晋级，但可保留为引用。
- lint report 中重复项分组展示 canonical representative。
- 新增 `source_registry.py`，为每个 source 生成稳定 registry item。
- 为 source 增加 `source_role`、`source_scope`、`source_weight`。
- source 的 `date` 逐步迁移为 `event_date`、`published_date`、`captured_date`。
- taxonomy scoring 使用 source_weight 和 unique canonical group。
- source index 页面只由 registry 生成，不手工维护。

### 验收

- 重复 canonical URL 不再增加 concept/entity/theme source_count。
- `lint_report.md` 中重复 source 从纯列表变成可操作的 duplicate groups。
- `taxonomy_registry.json` 中同时有 `source_count` 和 `unique_source_count`。
- `source_registry.json` 能解释每个 source 的 scope、quality、duplicate group 和 taxonomy weight。
- 旧历史材料不会因为新收录而错误推动 recent velocity。
- source platform、媒体渠道、普通背景材料不会仅凭高频影响 AI taxonomy。

## Phase 2: AI Scope Gate

### 目标

建立 AI 知识库边界，避免 wiki 变成通用 Wikipedia。

### Scope 判断维度

每个候选 concept/entity/theme 计算：

```text
direct_ai_signal
source_context_signal
core_node_connectivity
entity_type_signal
genericness_penalty
background_context_penalty
```

### 判定规则

`core_ai` 条件示例：

- 名称本身是 AI 领域术语。
- 多个 source 在 AI 上下文中直接解释它。
- 能回答 AI 领域中的机制、方法、风险、产品或产业问题。

`adjacent_ai` 条件示例：

- 本身是通用概念，但在 AI 语境中形成稳定议题。
- 例如 `Copyright Law` 只有在 `AI Copyright` 语境下才可以进入 adjacent。

`background_context` 示例：

- `Corporate Governance`
- `Capital Allocation`
- `Decision Making`
- `Community Management`

这些可以出现在 source metadata，但默认不生成正式 concept 页。

### 工程任务

- 新增 `taxonomy/scope.py`。
- 实现 `classify_taxonomy_scope(name, type, records, context)`.
- 在 concept/entity/theme lifecycle 前调用 scope gate。
- `background_context` 节点不进入正式页面，只进入 `_state` 报告。

### 验收

- `AI Safety` 为 `core_ai`。
- `Red Teaming` 为 `core_ai` 或 `adjacent_ai`。
- `Corporate Governance` 默认不是 published concept。
- `Decision Making` 不再因普通出现进入正式 AI 知识结构。

## Phase 3: Concept 生命周期

### 目标

允许新概念进入，但不允许一出现就污染正式 wiki。

Concept 是 AI 知识库的主体层，不应该做得太少。Theme 负责导航，Entity 负责命名对象，Concept 负责解释机制、方法、问题、范式、风险、能力和工作流。

更合理的结构是：

```text
concept_family: 概念族，用于组织一组相关概念，不一定直接生成普通页面。
published_concept: 已进入正式 wiki 的概念页，覆盖 AI 知识库的主要知识点。
emerging_concept: 新兴概念，进入每日/每周审核队列，但还不进入正式 wiki。
candidate_concept: 候选概念，只在 review 和 registry 中可见。
background_term: 背景词，不生成正式概念页。
rejected_concept: 明确拒绝的概念，保留拒绝原因以避免反复出现。
```

也就是说，Concept 不是 curated seed 的小集合。Seed 只是一批可信核心概念；真正的 concept 层应该能持续增长，只是增长必须经过 lifecycle、scope gate 和 canonical merge。

这里不再拆“长期稳定概念”和“正式概念”。两者在实际维护中容易重复，都会落到“已经进入正式 wiki”的页面集合里。稳定程度和核心程度改为页面元数据：

```yaml
status: published
layer: published_concept
importance: core | standard | edge
stability: established | active | volatile
reviewed_by: human | seed | auto_suggested
```

日常运行只自动发现和排序 `candidate_concept` / `emerging_concept`；是否晋升为 `published_concept` 由 review decision 明确控制。

### Concept Layer

```text
concept_family
published_concept
emerging_concept
candidate_concept
background_term
rejected_concept
```

### Concept Family

`concept_family` 用来解决“概念很多但导航不能乱”的问题。它比 theme 更窄，比单个 concept 更宽。

示例：

```text
Agent Architecture
Agent Evaluation
Agent Security
Context And Memory
Retrieval And Knowledge
Model Evaluation
Model Alignment
Inference Optimization
AI Safety Risks
AI Governance Mechanisms
AI Coding Workflows
Multimodal Generation
```

Concept family 可以作为页面内分组、index 分组或 relation cluster，不要求每个 family 都生成独立正文页。

### Concept 数量原则

正式 concept 不应被压到几十个。对个人 AI 知识库更合理的数量级是：

```text
published_concept: 300 到 800 个左右
emerging_concept: 100 到 500 个，可随热点波动
candidate_concept: 可以是数千个，但只出现在 review/state 中
```

这些不是硬上限，而是健康区间：

- 低于 200 个 published concept：通常说明知识粒度过粗，很多机制和方法被 theme 吞掉。
- 300 到 800 个 published concept：适合长期 AI 知识库，既有覆盖面，也还能维护。
- 超过 1000 个 published concept：触发 concept drift review，检查同义词、背景词和过细概念。
- candidate 可以很多，因为它是发现层，不是发布层。

### Concept Status Flow

```text
new term
-> candidate
-> emerging
-> published
```

并行流：

```text
candidate -> rejected
candidate -> merged
emerging -> rejected
emerging -> merged
published -> merged
published -> deprecated
```

默认不做 `candidate/emerging -> published` 的全自动晋升。系统只生成 promotion suggestions，人工 decision 文件决定是否发布：

```yaml
concept_decisions:
  - name: "Red Teaming"
    action: promote
    to: published_concept
    reason: "AI safety workflow; recurring high-signal sources"
  - name: "Decision Making"
    action: reject
    reason: "generic background term outside AI-specific scope"
```

### Scoring

建议总分：

```text
score =
  0.20 * unique_source_score
+ 0.15 * date_diversity_score
+ 0.20 * ai_scope_score
+ 0.15 * direct_signal_score
+ 0.15 * recent_velocity_score
+ 0.10 * novelty_score
+ 0.05 * source_quality_score
- 0.20 * genericness_penalty
- 0.20 * duplicate_penalty
```

### 晋级规则

`candidate`:

- 被抽取到，但 source 少或语义不稳定。

`emerging`:

- 最近 7 或 14 天增长明显。
- 至少 3 个 unique sources。
- scope 至少为 `adjacent_ai`。
- 与已有 published/core concept 或 stable theme 有强连接。

`published`:

- 默认需要人工 decision 晋升，或来自受信任 seed/bootstrap。
- 至少 5 个 unique sources，或 3 个 high-signal unique sources。
- 跨至少 2 个日期。
- scope 为 `core_ai`，或 `adjacent_ai` 且有人审确认。
- 不是已有 published concept 的同义词。

`importance` 和 `stability` 不影响是否生成页面，只影响导航、排序、复习频率和页面质量门槛。

### 工程任务

- 替换当前 `build_concept_lifecycle()` 的简单阈值逻辑。
- 新增 `taxonomy/scoring.py` 和 `taxonomy/lifecycle.py`。
- 保留现有 `concept_lifecycle.json`，但扩展字段。
- 输出 review 文件：

```text
mymind/wiki/_state/review/concept_candidates.md
mymind/wiki/_state/review/emerging_concepts.md
mymind/wiki/_state/review/concept_promotions.md
mymind/wiki/_state/review/concept_rejections.md
```

### 验收

- 新概念不会被硬白名单挡住。
- 新概念先进入 candidate 或 emerging。
- 正式 concept 的噪声下降，覆盖面不被压缩；candidate/emerging 可见。
- `AI Safety`、`RAG`、`Model Evaluation` 仍为 published concept。
- `Red Teaming`、`Model Alignment`、`Inference Optimization` 这类专门概念可以保留为 published concept，而不是被大 theme 吞掉。

## Phase 4: Canonical Merge 与 Alias

### 目标

解决近义概念、多种拼写、大小写重复和横杠变体。

### Alias 类型

```text
exact_alias
case_alias
punctuation_alias
semantic_alias
parent_child_not_alias
```

示例：

```text
LLM-as-Judge -> LLM-As-A-Judge
AI Hallucination -> Hallucination 或 Model Hallucination
AI Memory Systems -> Memory Systems
Agent Memory Systems -> Memory Systems
AI alignment -> AI Alignment
```

注意：不是所有相似词都应合并。例如：

```text
AI Safety
AI Alignment
Red Teaming
Biosecurity Risk
```

这些是父子或相邻关系，不是 alias。

### 工程任务

- 新增 `taxonomy/canonical.py`。
- 建立 `canonical_key(name)`。
- 加入 name embedding 或 LLM review 可以作为后续增强，但基础规则必须可运行。
- 输出 suspected merge report：

```text
mymind/wiki/_state/review/suspected_merges.md
```

### 验收

- 不再出现 `AI Alignment.md` 和 `AI alignment.md` 两个 entity。
- `LLM-as-Judge` 变体归并到一个 canonical concept。
- suspected merge 不自动破坏页面，先进入 review。

## Phase 5: Entity 严格化

### 目标

Entity 只表示命名对象，不再承载概念。

Entity 层也不能做得太少。AI 发展快，新的模型、产品、框架、公司、论文、协议、benchmark 会不断出现；如果 entity 层太窄，知识库会错过真实生态变化。

Entity 的问题不是数量多，而是类型混乱和 salience 缺失。正确做法是让 entity 多层存在：

```text
published_entity: 已进入正式 wiki 的 AI 对象，如 OpenAI、Anthropic、Claude Code。
emerging_entity: 新出现且增长快的模型、产品、项目、公司或协议。
observed_entity: 被提到但暂不生成页面，只进入 relation state。
background_entity: 背景对象，不进入正式 AI 知识库。
rejected_entity: 明确不是 entity 或不是 AI 知识库对象。
```

也就是说，Entity 不应该靠 `entity_min_sources=2` 直接生成正式页，而应该先进入 entity lifecycle。

这里也不再拆“长期稳定实体”和“正式实体”。正式对象统一为 `published_entity`；长期核心、热点波动、边缘对象用元数据表达：

```yaml
status: published
layer: published_entity
entity_type: model | product | organization | paper | benchmark | protocol
importance: core | standard | edge
stability: established | active | volatile
reviewed_by: human | seed | auto_suggested
```

日常审核只需要看 `observed_entity` 和 `emerging_entity`，然后决定是否晋升为 `published_entity`。

### Entity Layer

```text
published_entity
emerging_entity
observed_entity
background_entity
rejected_entity
```

### Entity Scope

`core_ai_entity` 示例：

```text
OpenAI
Anthropic
Claude Code
ChatGPT
Gemini
DeepSeek
Cursor
Model Context Protocol
SWE-bench
METR
Hugging Face
```

`adjacent_ai_entity` 示例：

```text
NVIDIA
Microsoft
Amazon
European Union
White House
Y Combinator
Andreessen Horowitz
```

这些对象只有在 AI 语境中有稳定角色时才生成正式页。

`background_entity` 示例：

```text
United States
China
TechCrunch
arXiv
GitHub
YouTube
```

这些对象可能高频出现，但很多时候只是来源平台、地理背景、媒体渠道或基础设施。默认不应仅凭高频生成正式 entity 页，除非它们在某个 AI 议题中成为核心对象。

### Entity 数量原则

Entity 可以比 theme 多，也可以接近或超过 concept，因为它承载生态变化。

```text
published_entity: 300 到 1200 个左右
emerging_entity: 100 到 1000 个，可随热点波动
observed_entity: 可以是数千个，只进入 relation state
```

健康区间解释：

- 低于 200 个 published entity：通常说明模型、产品、项目、组织跟踪不足。
- 300 到 1200 个 published entity：适合持续跟踪 AI 生态。
- 超过 1500 个 published entity：触发 entity drift review，检查媒体、国家、普通人物、概念化实体是否混入。
- observed_entity 可以很多，因为它只是 relation state 节点，不生成正式页。

### 允许 Entity 类型

```text
organization
person
product
model
framework
dataset
benchmark
paper
protocol
event
project
standard
```

### 禁止 Entity 类型

```text
method
concept
capability
risk
practice
topic
generic_term
```

### Concept-like Entity 规则

如果 entity name:

- 命中 concept registry。
- 或符合 concept pattern，如 `* Learning`、`* Alignment`、`* Evaluation`。
- 或主要类型被 LLM 标为 method/concept。

则不生成 entity 页，改写为 concept 或 candidate。

### Entity 晋级规则

`observed_entity -> emerging_entity` 条件：

- 最近 7 或 14 天增长明显。
- 类型属于允许 entity 类型。
- 与至少 2 个 published AI concept 或 stable theme 强关联。
- 不是 source platform 或普通媒体渠道。

`emerging_entity -> published_entity` 条件：

- 默认需要人工 decision 晋升，或来自受信任 seed/bootstrap。
- 至少 3 个 high-signal unique sources，或 5 个 normal unique sources。
- 至少跨 2 个日期。
- 在 source 中是主体对象，而不是顺带提及。
- scope 为 `core_ai_entity`，或 `adjacent_ai_entity` 且通过 review。

不再设置发布后的二次稳定态。长期持续出现、对生态的核心程度、是否需要重点复习，都由 `importance`、`stability`、`last_confirmed` 和 relation centrality 表达。

### Entity 页面生成规则

不是所有 entity 都生成页面：

```text
published_entity: 生成完整 entity 页。
emerging_entity: 可生成轻量趋势页，或只进入 emerging report。
observed_entity: 不生成页面，只进入 relation_edges.json。
background_entity: 不生成页面，只作为 source metadata。
```

### 工程任务

- 扩展 `is_concept_like_entity()`.
- entity normalize 后先查 taxonomy registry。
- 新增 entity lifecycle scoring。
- 为 entity detail 增加 `scope`、`layer`、`salience`、`is_primary_subject`。
- 将 source platform 类对象从正式 entity 中降级到 background 或 observed。
- 对现有 entities 做迁移报告：

```text
mymind/wiki/_state/review/entity_anomalies.md
mymind/wiki/_state/review/entity_to_concept_migrations.md
mymind/wiki/_state/review/emerging_entities.md
mymind/wiki/_state/review/background_entities.md
```

### 验收

- `OpenAI`、`Anthropic`、`Claude Code` 保留为 entity。
- `AI Alignment`、`Reinforcement Learning`、`Prompt Injection` 不再作为 entity。
- entity 大小写重复数降为 0。
- 新模型、新产品、新项目能进入 emerging_entity，不会被固定列表挡掉。
- `arXiv`、`TechCrunch`、`United States` 这类高频背景对象不会仅因高频成为正式 AI entity。

## Phase 6: Theme 分层系统

### 目标

Theme 稳定但不封闭。这里不能把 `stable_theme` 做得太少，否则 AI 知识库会被迫把大量不同问题塞进少数大桶，最后每个 theme 都变成泛化索引。

更合理的结构是：

```text
theme_domain: 顶层导航域，少而稳，通常 8 到 12 个。
stable_theme: 长期可复用主题，数量中等，通常 30 到 50 个。
emerging_theme: 趋势雷达，自动增长、观察、晋级或降级。
```

也就是说，少的是 `theme_domain`，不是 `stable_theme`。`stable_theme` 应该足够覆盖 AI 领域的主要知识面，否则无法支撑个人最新知识库。

### Theme 类型

```text
theme_domain
stable_theme
emerging_theme
retired_theme
```

### Theme Domain

`theme_domain` 是主导航域，不一定单独生成普通 theme 页面，可以作为 index grouping 使用。

建议初始 domain：

```text
AI Agents And Automation
Models And Research
AI Engineering And Infrastructure
AI Safety And Governance
Data And Knowledge
Products And Applications
Business And Ecosystem
Human And Society
```

这些 domain 的作用是让 30 到 50 个 stable theme 仍然可导航，而不是把 stable theme 压缩到十几个。

### Stable Theme

`stable_theme` 是正式主题页，应该比 domain 更细，能承载稳定阅读路径和知识地图。建议初始集合：

```text
AI Agents
Agent Systems
Agent Evaluation
Agent Security
Tool Use And Function Calling
Computer Use And Browser Agents
Workflow Automation
AI Coding
AI Engineering
Developer Workflow
Context Engineering
Memory Systems
RAG And Retrieval
Knowledge Workflows
Knowledge Bases
Model Development
Foundation Models
Reasoning Models
Model Evaluation
Benchmarks And Evals
Model Alignment And Interpretability
Multimodal AI
Generative Media
Data And Training
Synthetic Data
AI Infrastructure
Inference Systems
Model Serving And Routing
Local And Edge AI
AI Hardware
AI Safety & Governance
AI Security
AI Policy And Regulation
AI Ethics
Privacy And Data Governance
Content Authenticity
AI Products
Enterprise AI
Consumer AI
AI Business
AI Research
Open Source AI
AI Education
Future Of Work
AI Research Ecosystem
```

这不是硬白名单。它是稳定主题层，可通过 review 晋级、合并、拆分、退休。

数量原则：

- 低于 25 个：通常说明主题太粗，核心 AI 领域被压扁。
- 30 到 50 个：适合个人 AI 知识库，既能覆盖主要方向，又能保持导航可读。
- 高于 60 个：需要触发 theme drift review，检查是否有过细主题、重复主题或应该降级为 emerging theme 的主题。

### Emerging Theme

用于趋势雷达。示例：

```text
AI Browsers
Agentic Commerce
AI Companions
On-device AI
World Models
AI Coding Workflows
AI Operating Systems
Synthetic Users
Model Context Protocol Ecosystem
Personal AI Devices
AI Scientific Discovery
```

### Theme 晋级规则

`emerging_theme -> stable_theme` 条件：

- 连续 30 天内有稳定出现。
- 至少覆盖 3 个 published concepts。
- 至少 10 个 unique sources。
- 不是已有 stable theme 的同义扩写。
- 能归入某个 `theme_domain`，或者触发新增 domain 的 review。
- 人审确认。

### 工程任务

- 改造 `normalize_theme_name()`，允许 emerging candidate。
- 新增 `theme_lifecycle.json`。
- 输出：

```text
mymind/wiki/_state/review/emerging_themes.md
mymind/wiki/_state/review/theme_drift.md
mymind/wiki/_state/review/theme_promotions.md
```

### 验收

- stable theme 数量保持在 30 到 50 个左右，并通过 theme_domain 保持可导航。
- 新 theme 不会被丢弃，而是进入 emerging。
- concept 与 theme 同名冲突进入 review。

## Phase 7: 页面渲染重构

### 目标

正式页面是知识页，不是全量共现索引。

### Concept 页面结构

```text
概览
3 分钟理解
这是什么
解决什么问题
核心机制
典型例子
容易混淆
上位主题
关键子概念
相邻概念
代表实体
建议先读
最近变化
```

### Entity 页面结构

```text
概览
它是什么
为什么值得跟踪
当前变化
关联概念
相关产品或组织
建议先读
时间线
```

### Theme 页面结构

```text
主题说明
当前趋势
核心概念
新兴概念
代表实体
近期高信号 sources
进入该主题的阅读路径
```

### 数量上限

```text
关键子概念: 20
相邻概念: 12
代表实体: 20
相关主题: 5
建议先读: 8
最近变化: 8
```

关系渲染策略：

```text
当前 concept/entity/theme 页面：写入精选且可读的关系、近期信号、阅读路径。
mymind/wiki/_state/relation_edges.json：保存机器可读的全量关系。
```

不再生成单独的人读 `graph/` 或 `queries/` 目录。用户阅读时只需要打开对应的 `.md` 页面；程序需要的长尾关系保存在 `_state`。

每个 published 页面固定包含：

```text
建议先读
核心解释
关键关系
近期信号
代表 sources
可继续展开的相邻页面
```

### 旧 Query 页迁移

当前实现会生成 `mymind/wiki/queries/`，例如：

```text
recent-signals.md
repeated-theses.md
evergreen-candidates.md
high-actionability.md
watchlist.md
stale-knowledge.md
```

最终结构不保留这个阅读目录。迁移规则：

```text
recent-signals.md
  -> 写入相关 concept/entity/theme 页的“近期信号”段落。

repeated-theses.md
  -> 写入相关 concept/theme 页的“关键关系”或“反复出现的判断”段落。

evergreen-candidates.md
  -> 写入相关页面的“建议先读”。

high-actionability.md
  -> 写入 source 页或相关 entity/concept 页的“下一步行动/值得跟踪”。

watchlist.md
  -> 写入相关页面 frontmatter 的 `watch: true` 和正文“待观察”段落。

stale-knowledge.md
  -> 写入相关页面 frontmatter 的 `revisit_after`，并进入 review 报告。
```

迁移完成后：

- `write_queries()` 不再被 `generate_aggregate_pages()` 调用。
- `AppConfig.queries_dir` 删除，或仅在 migration command 中读取旧目录。
- 旧 `mymind/wiki/queries/` 移入 `_archive/pre-taxonomy-migration/YYYYMMDD/queries/`。
- `index/review.md` 只链接 `_state/review/*.md`，不链接旧 query 页。

### Link Rendering Policy

当前页面中存在这类链接：

```markdown
[[wiki/themes/AI Safety|AI 安全]]
```

它在数据上指向了 theme，但在很多编辑器里不可点击，尤其是 VS Code、普通 Markdown 预览、GitHub 或 Obsidian vault root 不是 `mymind` 的情况下。导航不可点击也是质量问题：证据链存在但用户无法顺畅跳转。

链接生成需要改为可配置，并默认使用标准 Markdown 相对链接：

```markdown
[AI 安全](../themes/AI%20Safety.md)
```

配置项：

```yaml
wiki:
  link_style: markdown_relative  # markdown_relative | obsidian_wikilink
  vault_root: "mymind"           # 仅 obsidian_wikilink 需要
```

推荐策略：

```text
markdown_relative: 默认。适配 VS Code、GitHub、普通 Markdown、Obsidian。
obsidian_wikilink: 可选。只在明确使用 Obsidian 且 vault_root 配置正确时启用。
```

工程要求：

- `related_link_lines()` 和 `wiki_links()` 不应直接硬编码 wikilink。
- `rewrite_source_note_links()` 不应无条件把 Markdown 链接转换成 `[[wiki/...]]`。
- 所有生成的 concept/entity/theme/source 链接必须通过统一 `LinkRenderer`。
- 链接目标存在时必须生成可点击链接；目标不存在时显示纯文本并进入 broken-link review。
- 链接路径含空格、中文、括号时必须正确 escape 或使用 Markdown 兼容路径。

新增 review 输出：

```text
mymind/wiki/_state/review/broken_links.md
mymind/wiki/_state/review/link_style_issues.md
```

### 工程任务

- 修改 `related_term_items()`，加入 relation type 与 score。
- 修改 `related_link_lines()`，支持 `limit` 和 `min_mentions`。
- 页面只渲染 curated top relations。
- 精选关系、近期信号和阅读路径写回当前页面。
- 全量机器关系写入 relation edges state。
- 新增统一 `LinkRenderer`，所有内部链接都通过它生成。
- 增加 link-style lint，检查不可点击链接、错误 vault root、缺失 target。

### 验收

- `AI Safety.md` 不再出现 `相关概念（548）`。
- `AI Agents.md` 不再出现上千个相关概念。
- 核心页面正文可在一次阅读中消化。
- `相关主题`、`相关概念`、`相关对象` 中已存在目标页的链接必须可点击。
- 在默认配置下，生成 Markdown 相对链接，而不是硬编码 `[[wiki/themes/...]]`。
- `broken_links.md` 能列出目标不存在、vault root 不匹配、链接样式不可用的问题。

## Phase 8: Relation State Layer

### 目标

关系不丢，但不再生成额外阅读目录。每个 `.md` 页面只展示与当前主题最相关、最值得读的关系；长尾关系只进入机器状态，供下次渲染、review 和 lint 使用。

### 输出

```text
mymind/wiki/_state/relation_edges.json
```

Edge 结构：

```json
{
  "source_id": "concept:ai-safety",
  "target_id": "concept:red-teaming",
  "relation": "has_subconcept",
  "weight": 0.84,
  "mentions": 32,
  "evidence_source_ids": [],
  "last_seen": "2026-06-15"
}
```

Relation 类型：

```text
has_parent_theme
has_subconcept
related_to
contrasts_with
often_uses
represented_by_entity
mentioned_in_source
merged_into
```

### 工程任务

- 新增 `taxonomy/relations.py`。
- 从 records 生成 relation candidates。
- 根据 taxonomy type 和 scoring 选择页面展示关系。
- 全量 edge 保存在 `_state`。
- 不生成 `mymind/wiki/graph/`。
- 不生成 `mymind/wiki/queries/`。

### 验收

- 页面短，但每页都有足够的关系上下文。
- 用户只需要读当前 `.md`，不需要跳到 `graph/` 或 `queries/`。
- review 可以看到被隐藏但仍存在的长尾关系。

## Phase 9: Review Workflow

### 目标

所有自动决策都可复核。

### Review 报告

```text
mymind/wiki/_state/review/new_candidates.md
mymind/wiki/_state/review/emerging_concepts.md
mymind/wiki/_state/review/suspected_merges.md
mymind/wiki/_state/review/out_of_scope.md
mymind/wiki/_state/review/theme_drift.md
mymind/wiki/_state/review/entity_anomalies.md
mymind/wiki/_state/review/source_duplicates.md
```

### 每条 review item 字段

```text
推荐动作
节点类型
canonical name
当前状态
建议状态
scope
score
原因
证据 sources
影响页面
自动置信度
```

### 工程任务

- 新增 `taxonomy/review.py`。
- 在每次 compile 后生成 review reports。
- 支持人工 decision 文件：

```text
mymind/wiki/_state/taxonomy_decisions.yaml
```

示例：

```yaml
accept:
  - concept:agentic-commerce
merge:
  concept:llm-as-judge: concept:llm-as-a-judge
reject:
  - concept:corporate-governance
promote_theme:
  - theme:ai-browsers
```

### 验收

- 人工可以通过 decision 文件影响下一次编译。
- 自动发现的新概念可见但不污染正式页。

## Phase 10: 迁移策略

### 原则

不直接删除大量页面。先生成计划，再迁移。

### 步骤

1. 扫描当前 wiki，生成 `taxonomy_registry.draft.json`。
2. 生成 duplicate and merge reports。
3. 生成 concept/entity/theme migration plan。
4. 先做 dry-run，不改页面。
5. 人工确认高影响 merge。
6. 写 alias 和 redirect 状态。
7. 重建正式页面。
8. 归档被移除页面。

### 归档路径

```text
mymind/wiki/_archive/pre-taxonomy-migration/YYYYMMDD/
```

### 迁移报告

```text
mymind/wiki/_state/review/migration_plan.md
mymind/wiki/_state/review/migration_result.md
```

### 验收

- 所有删除或合并都有记录。
- 被合并页面可以追溯 canonical target。
- 现有核心页面不会无记录消失。

## Phase 11: 测试体系

### Unit Tests

覆盖：

```text
normalize_concept_name
normalize_entity_name
classify_taxonomy_scope
score_concept_candidate
build_concept_lifecycle
is_concept_like_entity
normalize_theme_name
related edge selection
```

### Golden Tests

准备固定小语料：

```text
tests/fixtures/wiki_sources/ai_safety/
tests/fixtures/wiki_sources/ai_agents/
tests/fixtures/wiki_sources/noise/
```

验证输出：

```text
AI Safety -> published concept
Red Teaming -> published or emerging concept
Corporate Governance -> background_context
OpenAI -> entity
Claude Code -> published entity
New AI model/product -> emerging_entity before published_entity
AI Alignment -> concept, not entity
AI Browsers -> emerging theme if evidence strong
```

### Snapshot Tests

核心页面：

```text
AI Safety.md
AI Agents.md
RAG.md
Model Evaluation.md
OpenAI.md
Anthropic.md
```

断言：

- 相关概念数量不超过上限。
- 必须包含 `建议先读`。
- 不包含数百个长尾共现项。
- entity 页不展示 concept-like title。

### Regression Tests

覆盖历史问题：

- 大小写重复 entity。
- concept 和 entity 同名冲突。
- theme 和 concept 同名冲突。
- source 重复污染 source_count。
- 低价值泛化概念晋级。

## Phase 12: CLI 与运行模式

新增 CLI flags：

```bash
uv run python main.py --taxonomy-dry-run
uv run python main.py --taxonomy-review
uv run python main.py --apply-taxonomy-decisions
uv run python main.py --rebuild-registry
uv run python main.py --only-taxonomy
uv run python main.py --only-render
```

运行模式：

```text
daily compile:
  source -> taxonomy candidate update -> normal render

weekly review:
  generate review reports -> apply decisions -> rebuild published pages

monthly maintenance:
  merge aliases -> retire stale themes -> archive deprecated pages
```

## 实施顺序

### Milestone 1: 只读诊断层

交付：

- `taxonomy_registry.draft.json`
- `source_quality.json`
- `review/*.md`

不改变现有页面。

### Milestone 2: Source 去重与 scope gate

交付：

- duplicate source 不参与晋级。
- background_context 不生成 published concept。
- scope 出现在 lifecycle 文件中。

### Milestone 3: Entity 严格化

交付：

- concept-like entity 阻断。
- 大小写 entity merge。
- entity anomaly report。

### Milestone 4: Concept lifecycle 替换

交付：

- candidate/emerging/published 状态有效，发布晋升由 review decision 控制。
- 新概念进入 emerging，不被白名单排除。
- 低价值概念不再进入正式页。

### Milestone 5: Theme 分层系统

交付：

- theme_domain、stable_theme、emerging_theme 分离。
- theme drift report。
- 同名 theme/concept 冲突进入 review。

### Milestone 6: 页面渲染重构

交付：

- 相关关系 Top N。
- 精选关系、近期信号和阅读路径写入对应页面。
- 长尾关系只进入 relation state。
- 核心页面变短且可读。

### Milestone 7: Migration

交付：

- 合并重复概念和实体。
- 归档低质量页面。
- 重新生成正式 wiki。

### Milestone 8: 测试与 CI

交付：

- unit tests。
- golden tests。
- snapshot tests。
- regression tests。

## 验收指标

### 结构指标

- 正式 concept 数量下降，但 candidate/emerging 数量可见。
- published concept 维持足够覆盖面，不能被压缩成少量 curated seed。
- emerging concept 自动出现并进入 review。
- entity 中 concept-like 页面接近 0。
- published entity 维持足够生态覆盖面，observed entity 不直接生成页面。
- emerging entity 自动出现并进入 review。
- stable theme 数量保持在 30 到 50 个左右，并由 8 到 12 个 theme_domain 分组导航。
- emerging theme 自动出现并进入 review。

### 页面指标

- 核心 concept 页相关概念不超过 20 到 30。
- theme 页不再像全量索引。
- 每个正式页面有明确阅读路径。
- 全量共现关系不丢失，但不生成单独阅读目录；用户只读对应页面。

### 质量指标

- source duplicate 不参与 lifecycle 晋级。
- source 有明确 `source_role`、`source_scope`、`quality` 和 `source_weight`。
- source 的事件时间、发布时间、收录时间不混用。
- 新概念不会被硬白名单排除。
- 普通背景词不会自动变成正式 AI 概念。
- 高频背景实体不会仅因高频变成正式 AI entity。
- 高信号新趋势能在 7 到 14 天内进入 emerging。

### 可维护性指标

- 每次自动状态变化都有原因；发布晋升必须能追溯到 review decision 或 seed。
- 每次合并都有 canonical target。
- 每次拒绝都有 scope 或 genericness 原因。
- 人工 decision 文件可以稳定覆盖自动判断。

## 完成定义

这份 plan 完成后，工程实现必须同时满足下面的可验证条件。

### 代码交付

- `src/knowledge_wiki_compiler/taxonomy/` 存在并包含：
  - `registry.py`
  - `scope.py`
  - `scoring.py`
  - `lifecycle.py`
  - `canonical.py`
  - `relations.py`
  - `review.py`
- `source_quality.py`、`page_renderer.py`、`linting.py` 存在，且 `app.py` 只保留主编排和兼容 glue code。
- `config.py` 不再暴露长期使用的 `queries_dir`；迁移期需要读取旧 query 文件时，必须通过显式 migration command。
- `pyproject.toml` 增加测试依赖，至少支持 `uv run pytest`。
- `tests/` 存在，并覆盖 unit、golden、snapshot、regression 四类测试。

### 生成物交付

运行正式编译后必须生成：

```text
mymind/wiki/concepts/*.md
mymind/wiki/entities/*.md
mymind/wiki/themes/*.md
mymind/wiki/emerging/
mymind/wiki/families/
mymind/wiki/domains/
mymind/wiki/sources/index/
mymind/wiki/_state/taxonomy_registry.json
mymind/wiki/_state/source_registry.json
mymind/wiki/_state/source_quality.json
mymind/wiki/_state/source_duplicates.json
mymind/wiki/_state/relation_edges.json
mymind/wiki/_state/taxonomy_decisions.yaml
mymind/wiki/_state/review/*.md
```

最终正式结构中不得生成：

```text
mymind/wiki/graph/
mymind/wiki/queries/
```

旧 `queries/` 只能出现在 `_archive/pre-taxonomy-migration/YYYYMMDD/queries/`。

### 行为交付

- `candidate` 和 `emerging` 可以自动产生。
- `published_concept` 和 `published_entity` 默认需要 `taxonomy_decisions.yaml` 或 seed/bootstrap 证明。
- concept/entity 页面只展示精选关系、近期信号和阅读路径。
- 长尾关系只存在于 `_state/relation_edges.json`，不生成额外阅读目录。
- 内部链接默认是 Markdown relative link，且已存在目标必须可点击。
- source duplicate 不参与 source_count 晋级，只参与引用和 duplicate review。
- 旧历史材料不会因新收录时间误入 recent velocity。

### 验证命令

工程完成后至少运行：

```bash
cd knowledge-wiki-compiler
uv sync
uv run pytest
uv run python main.py --taxonomy-dry-run
uv run python main.py --taxonomy-review
uv run python main.py --apply-taxonomy-decisions
uv run python main.py --force
```

并执行结构检查：

```bash
test ! -d ../../../mymind/wiki/graph
test ! -d ../../../mymind/wiki/queries
test -f ../../../mymind/wiki/_state/relation_edges.json
test -f ../../../mymind/wiki/_state/taxonomy_registry.json
test -d ../../../mymind/wiki/_state/review
```

### 完成审计表

| 需求 | 证据 |
| --- | --- |
| AI scope gate 生效 | `taxonomy_registry.json` 中 background_context 不生成 published 页 |
| 新概念不会被固定 seed 排除 | `review/emerging_concepts.md` 和 `review/concept_candidates.md` 有新增项 |
| 发布由人审控制 | `taxonomy_decisions.yaml` 中能追溯 promote/reject/merge |
| entity 不承载概念 | regression test 覆盖 `AI Alignment -> concept, not entity` |
| theme 少而稳定但不封闭 | `theme_lifecycle.json` 中同时存在 stable_theme 和 emerging_theme |
| 用户只读对应页面 | 无 `graph/`、无 `queries/`，近期信号写入 concept/entity/theme 页 |
| 链接可点击 | `review/broken_links.md` 为空或只包含已解释的外部/缺失目标 |
| source 去重有效 | duplicate canonical URL 不增加 `unique_source_count` |

## 关键设计取舍

### 为什么不是硬 curated seed

硬 seed 会让知识库变旧。这里采用 seed 作为稳定骨架，而不是边界：

```text
seed = trusted core
candidate/emerging = open discovery
published = governed publishing
```

### 为什么不是只有 10 到 15 个 Stable Theme

10 到 15 个适合作为顶层 domain，不适合作为完整 stable theme 层。AI 知识库如果只有十几个 stable theme，会把模型、Agent、Infra、安全、产品、商业、研究工作流等不同问题压进少数大桶，页面会重新变成全量索引。

这里采用：

```text
theme_domain = 顶层导航域，少而稳
stable theme = 正式主题页，覆盖主要 AI 知识面
emerging theme = 趋势雷达，开放增长
```

domain 层控制导航复杂度，stable theme 层保证覆盖面，emerging theme 层保证新鲜度。

### 为什么不生成 graph 或 queries 目录

个人知识库的阅读入口应尽量集中。关系、近期信号和阅读路径应该回到对应的 concept/entity/theme 页面里，而不是让用户在 `graph/`、`queries/` 和正文页之间来回跳。

取舍是：

```text
当前页面 = 人读的精选关系
_state/relation_edges.json = 机器用的全量关系
```

这样既保留后续计算能力，又保证日常阅读只打开一个 `.md`。

### 为什么需要 review 文件

AI 知识库是个人知识系统，自动化应提出建议，而不是默默改写知识结构。Review 文件是人机协作边界。

### 为什么 Source 不按质量移动物理文件

Source 是证据层，链接稳定性比目录美观更重要。物理文件继续按来源类型和日期归档，质量、范围、重复组、权重通过 frontmatter、registry 和 index 表达。

这样可以避免：

- source 质量变化后频繁移动文件。
- 旧链接失效。
- 同一 source 同时属于多个视角时只能放进一个目录。
- Obsidian 链接和 relation state 被目录迁移破坏。

## 最终目录形态

推荐最终结构：

```text
mymind/wiki/
  concepts/
    AI Safety.md
    RAG.md
    Model Evaluation.md

  entities/
    OpenAI.md
    Anthropic.md
    Claude Code.md

  themes/
    AI Safety And Governance.md
    AI Agents.md
    Model Evaluation.md

  emerging/
    concepts/
    entities/
    themes/

  families/
    Agent Evaluation.md
    Context And Memory.md
    Model Alignment.md

  domains/
    AI Agents And Automation.md
    Models And Research.md
    AI Safety And Governance.md

  sources/
    articles/
      YYYYMMDD/
    posts/
      YYYYMMDD/
    index/
      by-date.md
      by-source-type.md
      by-quality.md
      by-scope.md
      primary-sources.md
      high-signal-sources.md
      failed-sources.md
      duplicate-sources.md

  index/
    ai-map.md
    concepts.md
    entities.md
    themes.md
    emerging.md
    review.md

  _state/
    taxonomy_registry.json
    concept_lifecycle.json
    entity_lifecycle.json
    theme_lifecycle.json
    source_registry.json
    source_quality.json
    source_duplicates.json
    relation_edges.json
    taxonomy_decisions.yaml
    review/

  _archive/
    pre-taxonomy-migration/
```

Source 的关键点：

- `sources/articles/YYYYMMDD/` 和 `sources/posts/YYYYMMDD/` 保持证据文件稳定。
- `sources/index/` 提供人工阅读入口。
- `_state/source_registry.json` 提供机器可读事实。
- `_state/source_quality.json` 提供质量、范围和权重。
- `_state/source_duplicates.json` 提供 canonical duplicate groups。
- 只有非 source 的知识层进入 `concepts/`、`entities/`、`themes/`、`emerging/`。

## 最终状态

重构完成后，`knowledge-wiki-compiler` 应该从：

```text
automatic wiki tag compiler
```

升级为：

```text
AI knowledge taxonomy compiler
```

它应同时满足：

- 有边界：不是 Wikipedia。
- 有新鲜度：不会被固定 taxonomy 锁死。
- 有治理：新概念先候选，再趋势，再正式。
- 有可读性：核心页是知识页，不是共现列表。
- 有证据链：每个节点和关系都能回到 source。
- 有人工控制点：通过 review 和 decisions 管理知识结构。
