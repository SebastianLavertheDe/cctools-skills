#!/usr/bin/env python3
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import threading

try:
    import fcntl as _fcntl
except ImportError:
    _fcntl = None
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

from runtime_paths import (
    RuntimePathError,
    optional_run_dir,
    require_skill_data_dir,
    require_mymind_root,
    resolve_mymind_path,
)

from ai_client import (  # noqa: E402
    build_legacy_provider_tuples,
    call_chat_completion,
    extract_json_text,
    load_env_file as load_shared_env_file,
)

WATCHLIST_TERMS = [
    "claude code",
    "cursor",
    "langchain",
    "codex",
    "anthropic",
    "openai",
    "glm",
    "minimax",
    "ai agent harness",
    "harness",
    "ai工程",
    "agent engineering",
    "context engineering",
]

PRIORITY_POST_AUTHORS = {
    "claudeai",
    "openai",
    "anthropicai",
    "geminiapp",
    "googleaistudio",
    "dotey",
    "ianneo_ai",
    "github_daily",
    "berryxia",
    "op7418",
    "xiaohu",
    "lxfater",
}

FIRST_PASS_CHUNK_SIZE = 40

# LLM calls are network IO and go through the package-local stateless AI client,
# so it is safe to run multiple chunks in parallel. Keep workers bounded to avoid hitting
# provider rate limits.
FIRST_PASS_CONCURRENCY = int(os.environ.get("DTS_FIRST_PASS_WORKERS", "5"))

WECHAT_SOURCE_MARKERS = (
    "mp.weixin.qq.com",
    "weixin.qq.com",
    "微信公众号",
    "微信公众平台",
    "微信公众",
)

OPEN_SOURCE_PROJECT_CATEGORY = "开源项目推荐"

# ---------------------------------------------------------------------------
# LLM result cache (DTS-2)
#
# Re-running the pipeline while tuning prompts or thresholds used to re-bill
# every LLM call. Wrap each top-level LLM-driven function in
# `with_llm_result_cache(...)` and it will skip the network round trip when the
# exact same input bundle (source content + tuning params + provider + stage
# label) is seen again. The cache lives at
#   {base_dir}/daily-topic/.cache/{date_str}.json
# and is keyed on sha256 of the inputs, so any change to thresholds, provider,
# or the underlying daily_summary/posts.json content forces a miss.
# ---------------------------------------------------------------------------

CACHE_DISABLED = os.environ.get("DTS_DISABLE_CACHE", "") in ("1", "true", "yes")


def _stable_hash(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _provider_fingerprint(
    provider_configs: List[Tuple[str, str, str, str]],
) -> str:
    # Only the (provider name, model) pair matters for cache identity — keys and
    # base urls are deliberately ignored so rotated credentials do not bust the
    # cache, while a switch to a different model still invalidates it.
    parts = [f"{name}::{model}" for name, _base, model, _key in provider_configs]
    return "|".join(parts)


# 三阶段（topic/reddit/post）并发写同一份 .cache/{date}.json，进程内 Lock 保护
# 读改写，避免后写覆盖前写而丢失某个 stage 的结果。
_CACHE_LOCK = threading.Lock()


def _load_cache_store(cache_dir: Path, date_str: str) -> Dict[str, Any]:
    cache_path = cache_dir / f"{date_str}.json"
    if not cache_path.exists():
        return {}
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache_store(cache_dir: Path, date_str: str, store: Dict[str, Any]) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{date_str}.json"
    tmp_path = cache_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(cache_path)


def _read_cache_entry(
    cache_dir: Path,
    date_str: str,
    cache_key: str,
) -> Any:
    if CACHE_DISABLED:
        return None
    store = _load_cache_store(cache_dir, date_str)
    entry = store.get(cache_key)
    if not isinstance(entry, dict):
        return None
    return entry.get("result")


def _write_cache_entry(
    cache_dir: Path,
    date_str: str,
    cache_key: str,
    stage_label: str,
    result: Any,
) -> None:
    if CACHE_DISABLED:
        return
    cache_dir.mkdir(parents=True, exist_ok=True)
    lock_path = cache_dir / f"{date_str}.lock"
    # 进程内 _CACHE_LOCK 串行化多线程；fcntl 跨进程互斥 .lock 文件，保证读改写原子——
    # 两个脚本进程同时跑也不会争抢 {date}.json.tmp 互相覆盖丢失某个 stage 的结果。
    with _CACHE_LOCK:
        with open(lock_path, "w", encoding="utf-8") as lock_file:
            locked = False
            if _fcntl is not None:
                try:
                    _fcntl.flock(lock_file.fileno(), _fcntl.LOCK_EX)
                    locked = True
                except OSError:
                    pass
            try:
                store = _load_cache_store(cache_dir, date_str)
                store[cache_key] = {"stage": stage_label, "result": result}
                _save_cache_store(cache_dir, date_str, store)
            except OSError:
                # Cache writes must never break a real run.
                pass
            finally:
                if locked:
                    try:
                        _fcntl.flock(lock_file.fileno(), _fcntl.LOCK_UN)
                    except OSError:
                        pass


@dataclass
class Topic:
    category_heading: str
    title: str
    score: int
    author: str
    summary: str
    keypoints: List[str]
    link_text: str
    link_url: str
    source_text: str
    source_url: str


@dataclass
class PostItem:
    tweet_id: str
    author_name: str
    author_screen_name: str
    verified: bool
    content: str
    created_at: str
    link: str
    likes: int
    reposts: int
    replies: int
    quotes: int
    bookmarks: int
    is_retweet: bool


def _topic_to_cache_dict(topic: Topic) -> Dict[str, Any]:
    return {
        "category_heading": topic.category_heading,
        "title": topic.title,
        "score": topic.score,
        "author": topic.author,
        "summary": topic.summary,
        "keypoints": list(topic.keypoints),
        "link_text": topic.link_text,
        "link_url": topic.link_url,
        "source_text": topic.source_text,
        "source_url": topic.source_url,
    }


def _topic_from_cache_dict(payload: Dict[str, Any]) -> Topic:
    return Topic(
        category_heading=str(payload.get("category_heading", "")),
        title=str(payload.get("title", "")),
        score=int(payload.get("score", 0) or 0),
        author=str(payload.get("author", "")),
        summary=str(payload.get("summary", "")),
        keypoints=[str(k) for k in payload.get("keypoints", [])],
        link_text=str(payload.get("link_text", "")),
        link_url=str(payload.get("link_url", "")),
        source_text=str(payload.get("source_text", "")),
        source_url=str(payload.get("source_url", "")),
    )


def extract_json_from_response(text: str) -> str:
    return extract_json_text(text)


def load_env_file(env_file: str) -> None:
    p = Path(env_file)
    if not p.exists():
        raise FileNotFoundError(f"Env file not found: {env_file}")
    load_shared_env_file(p)


def choose_ai_providers(provider: str) -> List[Tuple[str, str, str, str]]:
    return build_legacy_provider_tuples()


def parse_md_link(line: str) -> Tuple[str, str]:
    m = re.search(r"\[([^\]]+)\]\(([^)]+)\)", line)
    if not m:
        return "", ""
    return m.group(1).strip(), m.group(2).strip()


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def text_looks_like_open_source_project(text: str) -> bool:
    normalized = normalize_whitespace(text).lower()
    if not normalized:
        return False

    if re.search(r"\bgithub\s*[-:：]", normalized):
        return True
    if re.search(r"https?://(?:www\.)?github\.com/[\w.-]+/[\w.-]+", normalized):
        return True
    if re.search(r"\bgithub\.com/[\w.-]+/[\w.-]+", normalized):
        return True

    project_terms = (
        "开源项目",
        "github热门项目",
        "github 仓库",
        "github仓库",
        "开源仓库",
        "开源工具",
        "开源插件",
        "开源 sdk",
        "开源sdk",
        "开源 cli",
        "开源cli",
        "推荐开源",
    )
    if any(term in normalized for term in project_terms):
        return True

    if (
        ("github_daily" in normalized or "githubdaily" in normalized)
        and any(term in normalized for term in ("项目", "插件", "工具", "库", "框架", "sdk", "cli"))
    ):
        return True

    return False


def topic_display_category_heading(topic: Topic) -> str:
    haystack = " ".join(
        [
            topic.category_heading,
            topic.title,
            topic.summary,
            " ".join(topic.keypoints),
            topic.link_text,
            topic.link_url,
            topic.source_text,
            topic.source_url,
        ]
    )
    if OPEN_SOURCE_PROJECT_CATEGORY in topic.category_heading or text_looks_like_open_source_project(haystack):
        return f"## 📚 {OPEN_SOURCE_PROJECT_CATEGORY}"
    return topic.category_heading


def post_highlight_looks_like_open_source_project(item: Dict[str, object]) -> bool:
    text = " ".join(
        str(item.get(key, "") or "")
        for key in ("summary", "link", "author_name", "author_screen_name")
    )
    return text_looks_like_open_source_project(text)


def is_priority_post_author(post: PostItem) -> bool:
    screen_name = (post.author_screen_name or "").strip().lower()
    return screen_name in PRIORITY_POST_AUTHORS


def find_watchlist_hits(topic: Topic) -> List[str]:
    haystack = " ".join(
        [
            topic.title,
            topic.summary,
            " ".join(topic.keypoints),
            topic.link_text,
            topic.source_text,
        ]
    ).lower()
    hits: List[str] = []
    for term in WATCHLIST_TERMS:
        if term.lower() in haystack:
            hits.append(term)
    return hits


def is_wechat_source_topic(topic: Topic) -> bool:
    haystack = " ".join(
        [
            topic.title,
            topic.link_text,
            topic.link_url,
            topic.source_text,
            topic.source_url,
        ]
    )
    haystack_lower = haystack.lower()
    return any(marker.lower() in haystack_lower for marker in WECHAT_SOURCE_MARKERS)


def remove_wechat_source_topics(topics: List[Topic]) -> Tuple[List[Topic], int]:
    filtered = [topic for topic in topics if not is_wechat_source_topic(topic)]
    return filtered, len(topics) - len(filtered)


def build_topic_block(index: int, topic: Topic) -> str:
    watchlist_hits = find_watchlist_hits(topic)
    return "\n".join(
        [
            f"[{index}] 标题: {topic.title}",
            f"分类: {topic.category_heading}",
            f"评分: {topic.score}/100",
            f"摘要: {topic.summary[:180]}",
            f"显式关注词命中: {', '.join(watchlist_hits) if watchlist_hits else '无'}",
        ]
    )


def build_selection_prompts(
    topic_blocks: List[str],
    min_fit_score: int,
    min_ai_score: int,
    min_viral_score: int,
    min_breakout_score: int,
    stage_label: str,
    extra_instruction: str = "",
) -> Tuple[str, str]:
    system_prompt = (
        "你是资深中文内容运营编辑，专长从资讯池中筛选 AI 相关公众号选题。"
        "必须基于给定候选池进行判断，不可编造链接和源文件。"
        "只保留真正值得单独成文的 AI 主题。"
        "你必须同时评估三个内部维度："
        "fit_score=独立成文价值（是否能支撑一篇公众号内容）；"
        "ai_score=AI相关性（主题主体是否明确围绕AI）；"
        "viral_score=传播价值（是否有新意、冲突、实操价值、讨论空间或行业判断价值）。"
        "breakout_score=AI领域易于传播的爆款潜力（是否具备强标题感、情绪张力、圈层扩散性、转发欲或鲜明观点）。"
        "只有四个维度都达标的主题才能保留。"
        "对于边缘项、凑数项、轻量更新、信息密度不足的内容，要直接过滤。"
        "只保留与以下方向强相关的内容："
        "AI技巧、AI使用技巧、AI产品使用技巧、AI领域新产品、AI新模型发布、AI Agent相关、"
        "AI领域新闻、AI公司相关的新闻、Prompt使用技巧、AI开源项目、开源项目推荐、AI+行业相关新闻、AI+伦理、AI+安全。"
        "遇到 GitHub 仓库、开源工具、开源插件、SDK、CLI、框架、开发者库等推荐类主题，应作为“开源项目推荐”单独保留和展示。"
        "额外优先关注这些实体与主题：Claude Code、Cursor、LangChain、Codex、Anthropic、OpenAI、GLM、MiniMax、AI Agent Harness、AI工程、Agent Engineering、Context Engineering。"
        "高优保留规则（以下主题即使边缘也应保留，各维度评分自动+2）："
        "1）Claude Code、Anthropic、OpenAI 官方发布的文章（博客、工程博文、产品公告），来源为 claude.com/blog 或 anthropic.com/engineering 或 openai.com/index。"
        "2）AI创业方法论：AI-native创业策略、创始人视角、AI时代如何从0到1、AI产品商业模式。"
        "3）AI与人的关系：人机协作、AI对个人能力的影响、AI时代人的核心能力、人机共生。"
        "4）AI与人与组织的新关系：AI对团队结构的影响、AI时代的组织架构变革、AI重塑管理方式、人+AI混合团队。"
        "如果需要生成中文说明，去掉 AI 味：不要使用“不是...但是...”“应该...而非...”“在于...而非...”“不在于...而在于...”“不...而...”“不...而是...”“不...而在于...”“不是...而是...”“不只有...还有...”“之所以...是因为...”“既是...也是...”等模板化句式；不要写先否定再转折的句子，直接说观点。"
    )

    user_prompt = f"""
当前阶段：{stage_label}

请从以下候选主题中，筛选“属于 AI 相关范围，且适合发微信公众号”的主题。

优先级规则（从高到低）：
1) AI技巧 / AI使用技巧 / AI产品使用技巧
2) AI领域新产品 / 新功能发布 / 产品更新 / 新服务上线
3) AI新模型发布 / 能力评测 / 技术突破 / Benchmark
4) AI Agent相关 / Agent工程 / 工作流 / 多智能体 / 评测 / 上下文工程
5) AI领域新闻 / AI公司相关新闻 / 大厂动态 / 融资 / 收购 / 战略调整
6) Prompt使用技巧 / 提示词工程 / 模板 / 实战方法
7) 开源项目推荐 / AI开源项目 / GitHub热门项目 / SDK / 框架 / CLI / 基础设施
8) AI+行业相关新闻（如 AI+编程、AI+办公、AI+教育、AI+医疗、AI+金融等）
9) AI+伦理 / 版权 / 监管 / 隐私 / 公平性 / 治理
10) AI+安全 / 漏洞 / 防护 / 红队 / 越狱 / 数据与供应链安全

额外优先关注：
- Claude Code、Cursor、LangChain、Codex
- Anthropic、OpenAI、GLM、MiniMax
- AI Agent Harness、AI工程、Agent Engineering、Context Engineering

高优保留规则（以下主题即使边缘也应保留，各维度评分自动+2）：
- Claude Code、Anthropic、OpenAI 官方发布的文章（博客、工程博文、产品公告），来源为 claude.com/blog、anthropic.com/engineering、openai.com/index
- AI创业方法论：AI-native创业策略、创始人视角、AI时代如何从0到1、AI产品商业模式
- AI与人的关系：人机协作、AI对个人能力的影响、AI时代人的核心能力、人机共生
- AI与人与组织的新关系：AI对团队结构的影响、AI时代的组织架构变革、AI重塑管理方式、人+AI混合团队

筛选原则：
- 只要主题核心叙事落在以上任一方向，才有资格进入候选。
- 如果文章主体不是 AI，只是附带提到 AI、或者只是用 AI 做了一个次要点缀，不要入选。
- 必须同时具备传播价值。所谓传播价值，至少应满足以下一项：有明显新意或反常识；有冲突或争议；有明确实操价值；有行业趋势判断；能被独立展开成一篇公众号内容。
- 必须额外评估“AI领域易于传播的爆款潜力”。优先保留那些一看就容易在 AI 圈传播的话题：有强烈反差、明确站队、爆点标题空间、情绪张力、人物/大厂/产品/IP 加持，或者能引发“转发给同行讨论”的冲动。
- 如果只是普通资讯、轻量更新、参数/价格小改动、缺少方法论和讨论空间，即使属于 AI 也不要入选。
- 对“AI 工程 / Agent / Harness / Context Engineering / 开源项目推荐 / 重要模型发布 / 重要公司动态”给予更高权重。
- 如果你不确定某条是否达标，默认排除，不要放宽标准。
{extra_instruction}

输出必须是严格 JSON，格式如下：
{{
  "wechat": [1, 4, 8]
}}

约束：
- wechat 输出你认为真正达标的全部主题，不要凑数。
- 只返回 index 数组，不返回其他字段。
- 如需内部生成判断理由，避免“不是...但是...”“应该...而非...”“在于...而非...”“不在于...而在于...”“不...而...”“不...而是...”“不...而在于...”“更多是...而非...”“不是...而是...”“不只有...还有...”“之所以...是因为...”“既是...也是...”等模板句，不要写先否定再转折的句子，直接说判断依据。
- 你需要在内部按以下阈值筛选：
  fit_score >= {min_fit_score}
  ai_score >= {min_ai_score}
  viral_score >= {min_viral_score}
  breakout_score >= {min_breakout_score}
- index 必须来自候选编号，且不可重复。
- 只输出 JSON，不要输出任何额外文字。
- 必须输出完整闭合 JSON。若无法判断，也必须输出 `{{"wechat":[]}}`。

候选主题：
{chr(10).join(topic_blocks)}
"""
    return system_prompt, user_prompt


def request_selected_indices(
    provider_configs: List[Tuple[str, str, str, str]],
    topic_blocks: List[str],
    min_fit_score: int,
    min_ai_score: int,
    min_viral_score: int,
    min_breakout_score: int,
    stage_label: str,
    extra_instruction: str = "",
) -> Tuple[List[int], str, str]:
    system_prompt, user_prompt = build_selection_prompts(
        topic_blocks=topic_blocks,
        min_fit_score=min_fit_score,
        min_ai_score=min_ai_score,
        min_viral_score=min_viral_score,
        min_breakout_score=min_breakout_score,
        stage_label=stage_label,
        extra_instruction=extra_instruction,
    )

    parsed = None
    raw = ""
    provider_name = ""
    model = ""
    errors: List[str] = []
    for provider_name, base_url, model, api_key in provider_configs:
        try:
            raw = call_chat_completion(
                base_url, api_key, model, system_prompt, user_prompt
            )
            json_text = extract_json_from_response(raw)
            if not json_text:
                raise RuntimeError("empty JSON extraction")
            parsed = json.loads(json_text)
            if not isinstance(parsed.get("wechat"), list):
                raise RuntimeError("'wechat' key missing or not a list")
            break
        except (RuntimeError, json.JSONDecodeError, ValueError) as exc:
            errors.append(f"{provider_name}/{model}: {exc}")
            parsed = None
            continue

    if parsed is None:
        raise RuntimeError("All AI providers failed: " + " | ".join(errors))

    items = parsed.get("wechat", [])
    if not isinstance(items, list):
        raise RuntimeError("AI output 'wechat' must be a list.")

    selected_indices: List[int] = []
    used = set()
    for item in items:
        if not isinstance(item, int):
            continue
        if item in used:
            continue
        used.add(item)
        selected_indices.append(int(item))

    return selected_indices, provider_name, model


def build_reddit_selection_prompts(
    reddit_blocks: List[str],
    max_posts: int,
    stage_label: str,
    extra_instruction: str = "",
) -> Tuple[str, str]:
    system_prompt = (
        "你是资深中文科技编辑，负责从 Reddit 社区摘要里筛选高信号 AI 社区动态。"
        "Reddit 条目通常只有一句话摘要，因此不要用“能否单独支撑一篇公众号文章”作为硬门槛。"
        "你要筛的是社区传播信号：AI 相关、讨论价值高、能补充新闻/文章视角、适合放进日报的社区观察。"
        "优先保留这些方向：AI 产品/模型/功能发布，Agent / AI 工程 / 开发者工作流，"
        "大厂动态 / 融资 / 战略信号，AI 技巧，Prompt 技巧，容易引发讨论和传播的行业观点。"
        "过滤纯灌水、梗图、低信息量提问、泛泛焦虑、与 AI 关系弱、或只适合闲聊的帖子。"
        "输出中文要去掉 AI 味：不要使用“不是...但是...”“应该...而非...”“在于...而非...”"
        "“不在于...而在于...”“不...而...”“不...而是...”“更多是...而非...”"
        "“不是...而是...”“不只有...还有...”“之所以...是因为...”“既是...也是...”等模板句。"
    )

    user_prompt = f"""
请阅读以下 Reddit 候选帖子，筛选适合进入 `Reddit 社区信号` 的条目。

阶段：{stage_label}

筛选标准：
- AI 相关性明确。
- 可传播度高：有新产品、新模型、新功能、真实痛点、强争议、实操技巧、行业判断或社区共鸣。
- 优先方向：AI 产品/模型/功能发布；Agent / AI 工程 / 开发者工作流；大厂动态 / 融资 / 战略信号；AI技巧；Prompt技巧；容易引发讨论的行业观点。
- 不要求单条 Reddit 能独立写成一篇文章，但它必须能作为“社区正在关注什么”的有效信号。
- 不要凑数。最多保留 {max_posts if max_posts > 0 else "不限"} 条。
{extra_instruction}

输出必须是严格 JSON，格式如下：
{{
  "reddit": [
    {{"index": 3, "reason": "一句话说明为什么值得保留"}}
  ]
}}

约束：
- index 必须来自候选编号，且不可重复。
- reason 只用于内部判断，不会写入最终文件。
- 只输出 JSON，不要输出任何额外文字。

候选帖子：
{chr(10).join(reddit_blocks)}
"""
    return system_prompt, user_prompt


def request_reddit_indices(
    provider_configs: List[Tuple[str, str, str, str]],
    reddit_blocks: List[str],
    max_posts: int,
    stage_label: str,
    extra_instruction: str = "",
) -> Tuple[List[int], str, str]:
    system_prompt, user_prompt = build_reddit_selection_prompts(
        reddit_blocks=reddit_blocks,
        max_posts=max_posts,
        stage_label=stage_label,
        extra_instruction=extra_instruction,
    )

    parsed = None
    raw = ""
    provider_name = ""
    model = ""
    errors: List[str] = []
    for provider_name, base_url, model, api_key in provider_configs:
        try:
            raw = call_chat_completion(
                base_url, api_key, model, system_prompt, user_prompt
            )
            json_text = extract_json_from_response(raw)
            if not json_text:
                raise RuntimeError("empty JSON extraction")
            parsed = json.loads(json_text)
            break
        except (RuntimeError, json.JSONDecodeError, ValueError) as exc:
            errors.append(f"{provider_name}/{model}: {exc}")
            parsed = None
            continue

    if parsed is None:
        raise RuntimeError(
            "All AI providers failed for Reddit topic selection: "
            + " | ".join(errors)
        )

    items = parsed.get("reddit", [])
    if not isinstance(items, list):
        raise RuntimeError("AI output 'reddit' must be a list.")

    selected_indices: List[int] = []
    used = set()
    for item in items:
        if isinstance(item, dict):
            item = item.get("index")
        if not isinstance(item, int) or item in used:
            continue
        used.add(item)
        selected_indices.append(int(item))

    return selected_indices, provider_name, model


def extract_topics(content: str) -> List[Topic]:
    lines = content.splitlines()
    topics: List[Topic] = []
    i = 0
    current_category_heading = ""
    skip_current_section = False
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("## "):
            skip_current_section = line.startswith("## 🧵") or line.startswith("## 💬")
            current_category_heading = line
            i += 1
            continue
        if skip_current_section or not line.startswith("### "):
            i += 1
            continue

        title = line[4:].strip()
        score = 0
        author = ""
        summary_lines: List[str] = []
        keypoints: List[str] = []
        link_text, link_url = "", ""
        source_text, source_url = "", ""

        i += 1
        mode = ""
        while i < len(lines):
            cur = lines[i].rstrip()
            cur_strip = cur.strip()

            if cur_strip.startswith("### "):
                break
            if cur_strip.startswith("---"):
                i += 1
                break
            if cur_strip.startswith("## "):
                break

            if cur_strip.startswith("**评分**"):
                m = re.search(r"(\d+)\s*/\s*100", cur_strip)
                if m:
                    score = int(m.group(1))
            elif cur_strip.startswith("**作者**"):
                mode = ""
                author = re.sub(r"^\*\*作者\*\*:\s*", "", cur_strip).strip()
            elif cur_strip == "**摘要**:":
                mode = "summary"
            elif cur_strip == "**关键要点**:":
                mode = "keypoints"
            elif cur_strip.startswith("**链接**"):
                mode = ""
                link_text, link_url = parse_md_link(cur_strip)
            elif cur_strip.startswith("**源文件**"):
                mode = ""
                source_text, source_url = parse_md_link(cur_strip)
            elif mode == "summary":
                if cur_strip:
                    summary_lines.append(cur_strip)
            elif mode == "keypoints" and cur_strip.startswith("- "):
                keypoints.append(cur_strip[2:].strip())

            i += 1

        topics.append(
            Topic(
                category_heading=current_category_heading or "## 📚 未分类",
                title=title,
                score=score,
                author=author,
                summary=" ".join(summary_lines).strip(),
                keypoints=keypoints,
                link_text=link_text,
                link_url=link_url,
                source_text=source_text,
                source_url=source_url,
            )
        )
    return topics


def extract_reddit_summary_section(content: str) -> List[str]:
    """Copy the compact Reddit section from daily-summary output."""
    lines = content.splitlines()
    start = -1
    for idx, line in enumerate(lines):
        if line.strip().startswith("## 💬 Reddit"):
            start = idx
            break

    if start < 0:
        return []

    end = len(lines)
    for idx in range(start + 1, len(lines)):
        stripped = lines[idx].strip()
        if stripped.startswith("## ") and not stripped.startswith("### "):
            end = idx
            break

    section = lines[start:end]

    while section and not section[-1].strip():
        section.pop()
    if section and section[-1].strip() == "---":
        section.pop()
    while section and not section[-1].strip():
        section.pop()

    return section + [""] if section else []


def extract_reddit_summary_items(content: str) -> Tuple[str, Dict[str, Dict[str, str]]]:
    """Parse compact Reddit summaries keyed by normalized post URL."""
    section = extract_reddit_summary_section(content)
    if not section:
        return "", {}

    source = ""
    current_subreddit = ""
    items: Dict[str, Dict[str, str]] = {}

    for line in section:
        stripped = line.strip()
        if stripped.startswith("**来源**:"):
            source = stripped.replace("**来源**:", "", 1).strip()
            continue
        if stripped.startswith("### r/"):
            current_subreddit = stripped.replace("### r/", "", 1).strip()
            continue
        if not stripped.startswith("- "):
            continue
        body = stripped[2:].strip()
        link_match = re.search(r"\s+\[原帖\]\(([^)]+)\)\s*$", body)
        if not link_match:
            continue
        summary = normalize_whitespace(body[: link_match.start()])
        link = normalize_url(link_match.group(1))
        if not link:
            continue
        items[link] = {
            "subreddit": current_subreddit or infer_subreddit_from_url(link) or "unknown",
            "summary": summary,
            "link": link,
        }

    return source, items


def parse_posts_json(path: Path) -> List[PostItem]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    posts: List[PostItem] = []
    if not isinstance(raw, list):
        return posts

    for item in raw:
        if not isinstance(item, dict):
            continue
        author = item.get("author", {}) or {}
        metrics = item.get("metrics", {}) or {}
        content = normalize_whitespace(item.get("content", ""))
        if not content:
            continue

        posts.append(
            PostItem(
                tweet_id=str(item.get("tweet_id", "")).strip(),
                author_name=str(author.get("name", "")).strip(),
                author_screen_name=str(author.get("screen_name", "")).strip(),
                verified=bool(author.get("verified", False)),
                content=content,
                created_at=str(item.get("created_at", "")).strip(),
                link=str(item.get("link", "")).strip(),
                likes=int(metrics.get("likes", 0) or 0),
                reposts=int(metrics.get("reposts", 0) or 0),
                replies=int(metrics.get("replies", 0) or 0),
                quotes=int(metrics.get("quotes", 0) or 0),
                bookmarks=int(metrics.get("bookmarks", 0) or 0),
                is_retweet=bool(item.get("is_retweet", False)),
            )
        )
    return posts


def normalize_url(url: str) -> str:
    cleaned = (url or "").strip()
    cleaned = cleaned.split("#", 1)[0].split("?", 1)[0]
    return cleaned.rstrip("/")


def infer_subreddit_from_url(url: str) -> str:
    match = re.search(r"reddit\.com/r/([^/]+)/", url or "", re.IGNORECASE)
    return match.group(1) if match else ""


def is_reddit_topic(topic: Topic) -> bool:
    return "reddit.com/r/" in (topic.link_url or "").lower()


def strip_reddit_topic_title(title: str) -> str:
    text = normalize_whitespace(title)
    text = re.sub(r"^[⭐📖📄]\s*", "", text)
    text = re.sub(r"^\[r/[^\]]+\]\s*", "", text)
    return text


def build_reddit_topic_fallback_summary(topic: Topic) -> str:
    for candidate in [strip_reddit_topic_title(topic.title), topic.summary]:
        text = normalize_whitespace(candidate)
        if text:
            return text[:120].rstrip("，。；; ") + ("…" if len(text) > 120 else "")
    return "Reddit 热帖"


def build_reddit_block(index: int, item: Dict[str, str]) -> str:
    return "\n".join(
        [
            f"[{index}] subreddit: r/{item.get('subreddit') or 'unknown'}",
            f"摘要: {normalize_whitespace(item.get('summary', ''))[:220]}",
            f"链接: {item.get('link', '')}",
        ]
    )


def select_reddit_items_with_ai(
    compact_reddit_items: Dict[str, Dict[str, str]],
    provider_configs: List[Tuple[str, str, str, str]],
    max_posts: int,
    *,
    cache_dir: Path | None = None,
    date_str: str = "",
) -> Tuple[List[Dict[str, str]], str, str]:
    if cache_dir is not None and date_str:
        reddit_fingerprint = json.dumps(compact_reddit_items, ensure_ascii=False, sort_keys=True)
        cache_key = _stable_hash(
            "reddit_select|"
            f"provider={_provider_fingerprint(provider_configs)}|"
            f"max_posts={max_posts}|"
            f"items={_stable_hash(reddit_fingerprint)}"
        )
        cached = _read_cache_entry(cache_dir, date_str, cache_key)
        if cached is not None and isinstance(cached, list):
            return cached, "", ""

    candidates = [
        {
            "subreddit": normalize_whitespace(str(item.get("subreddit", ""))),
            "summary": normalize_whitespace(str(item.get("summary", ""))),
            "link": normalize_url(str(item.get("link", ""))),
        }
        for item in compact_reddit_items.values()
        if normalize_whitespace(str(item.get("summary", "")))
    ]
    if not candidates:
        return [], "", ""

    first_pass_candidates: List[Dict[str, str]] = []
    used_links = set()
    used_provider = ""
    used_model = ""

    reddit_chunks: List[Tuple[int, List[Dict[str, str]]]] = [
        (start, candidates[start : start + FIRST_PASS_CHUNK_SIZE])
        for start in range(0, len(candidates), FIRST_PASS_CHUNK_SIZE)
    ]

    def _run_reddit_chunk(
        chunk_start: int, chunk_items: List[Dict[str, str]]
    ) -> Tuple[int, List[Dict[str, str]], str, str]:
        reddit_blocks = [
            build_reddit_block(i, item) for i, item in enumerate(chunk_items, start=1)
        ]
        selected_indices, used_provider, used_model = request_reddit_indices(
            provider_configs=provider_configs,
            reddit_blocks=reddit_blocks,
            max_posts=max_posts,
            stage_label=f"第一轮 Reddit 社区信号初筛（候选 {chunk_start + 1}-{chunk_start + len(chunk_items)}）",
            extra_instruction="这是第一轮初筛。请保留可传播度高、信息增量强、能代表社区真实关注点的 AI 相关帖子。",
        )
        picked: List[Dict[str, str]] = []
        for idx in selected_indices:
            if idx < 1 or idx > len(chunk_items):
                continue
            picked.append(chunk_items[idx - 1])
        return chunk_start, picked, used_provider, used_model

    chunk_results: List[Tuple[int, List[Dict[str, str]], str, str]] = []
    if len(reddit_chunks) > 1 and FIRST_PASS_CONCURRENCY > 1:
        with ThreadPoolExecutor(max_workers=FIRST_PASS_CONCURRENCY) as pool:
            futures = [
                pool.submit(_run_reddit_chunk, start, chunk)
                for start, chunk in reddit_chunks
            ]
            for fut in futures:
                chunk_results.append(fut.result())
    else:
        for start, chunk in reddit_chunks:
            chunk_results.append(_run_reddit_chunk(start, chunk))

    chunk_results.sort(key=lambda item: item[0])
    for chunk_start, picked_items, prov, mdl in chunk_results:
        if prov:
            used_provider = prov
            used_model = mdl
        for item in picked_items:
            link = item.get("link", "")
            if link in used_links:
                continue
            used_links.add(link)
            first_pass_candidates.append(item)

    if not first_pass_candidates:
        return [], used_provider, used_model

    final_blocks = [
        build_reddit_block(i, item)
        for i, item in enumerate(first_pass_candidates, start=1)
    ]
    final_indices, used_provider, used_model = request_reddit_indices(
        provider_configs=provider_configs,
        reddit_blocks=final_blocks,
        max_posts=max_posts,
        stage_label="第二轮 Reddit 社区信号复筛（全局复核）",
        extra_instruction=(
            "这是第二轮全局复核。请删除重复、低信息量、只适合闲聊的帖子，"
            "保留最适合传播的 AI 产品/模型/功能发布、Agent 工程、开发者工作流、"
            "大厂动态、AI技巧、Prompt技巧和行业观点。"
        ),
    )

    out: List[Dict[str, str]] = []
    used_final = set()
    for idx in final_indices:
        if idx < 1 or idx > len(first_pass_candidates) or idx in used_final:
            continue
        used_final.add(idx)
        out.append(first_pass_candidates[idx - 1])

    if max_posts > 0:
        out = out[:max_posts]

    if cache_dir is not None and date_str:
        _write_cache_entry(
            cache_dir,
            date_str,
            cache_key,
            "select_reddit_items_with_ai",
            out,
        )

    return out, used_provider, used_model


def post_signal_score(post: PostItem) -> float:
    verified_bonus = 40 if post.verified else 0
    watchlist_bonus = (
        18 if any(term in post.content.lower() for term in WATCHLIST_TERMS) else 0
    )
    priority_author_bonus = 120 if is_priority_post_author(post) else 0
    retweet_penalty = 60 if post.is_retweet else 0
    return (
        post.likes
        + post.reposts * 4
        + post.quotes * 3
        + post.replies * 2
        + post.bookmarks * 2
        + verified_bonus
        + watchlist_bonus
        + priority_author_bonus
        - retweet_penalty
    )


def select_post_candidates(posts: List[PostItem], limit: int) -> List[PostItem]:
    originals = [post for post in posts if not post.is_retweet]
    pool = originals or posts
    ranked = sorted(
        pool,
        key=lambda post: (post_signal_score(post), post.created_at, post.tweet_id),
        reverse=True,
    )
    if limit > 0:
        return ranked[:limit]
    return ranked


def build_post_block(index: int, post: PostItem) -> str:
    return "\n".join(
        [
            f"[{index}] 作者: {post.author_name} (@{post.author_screen_name}){' verified' if post.verified else ''}",
            f"重点作者: {'是' if is_priority_post_author(post) else '否'}",
            f"互动: likes={post.likes}, reposts={post.reposts}, replies={post.replies}, quotes={post.quotes}, bookmarks={post.bookmarks}",
            f"时间: {post.created_at or 'unknown'}",
            f"内容: {post.content[:280]}",
        ]
    )


def build_post_summary_prompts(post_blocks: List[str]) -> Tuple[str, str]:
    system_prompt = (
        "你是资深中文科技编辑，负责总结当天 X/Twitter 时间线。"
        "必须严格基于候选帖子内容判断，不可编造作者、产品、链接或结论。"
        "请优先提炼 AI、开发者工具、产品发布、模型更新、Agent 工程、行业讨论 相关的高信号动态。"
        "GitHub 仓库、开源工具、开源插件、SDK、CLI、框架、开发者库等推荐类帖子要作为开源项目推荐保留。"
        "忽略纯灌水、单纯情绪宣泄、无信息增量的转述。"
        "对重点作者可以适度优先，但绝不能因为作者身份牺牲内容质量。"
        "输出中文要去掉 AI 味：不要使用“不是...但是...”“应该...而非...”“在于...而非...”“不在于...而在于...”“不...而...”“不...而是...”“不...而在于...”“不是...而是...”“不只有...还有...”“之所以...是因为...”“既是...也是...”等模板化句式；不要写先否定再转折的句子，直接说产品、动作、结论和影响。"
    )

    user_prompt = f"""
请阅读以下候选帖子，输出一份“当天 Post 总结”。

要求：
1. `overview`：用 2-3 句话总结今天时间线的整体氛围与主线，适合写进日报。
2. `themes`：提炼 3-5 条今日主线，每条不超过 18 个字。
3. `highlights`：挑出你认为真正值得关注的帖子，给出对应 `index` 和一句中文总结。不设固定上限，但不要凑数。
4. highlight 优先级：
   - AI 产品/模型/功能发布
   - 开源项目推荐 / GitHub 热门项目 / 开源工具
   - Agent / AI 工程 / 开发者工作流
   - 大厂动态 / 融资 / 战略信号
   - 容易引发讨论的行业观点
5. 对候选中标记为“重点作者: 是”的帖子可以适度优先，因为这些作者通常更稳定地产出高信号内容。
6. 但不要把“重点作者”当成白名单。非重点作者如果帖子质量更高、信息增量更强，依然应该入选。
7. 如果某条只是转发、情绪吐槽、版权抱怨但没有新增信息，尽量不要入选。
8. 写法去掉 AI 味：不要使用“不是...但是...”“应该...而非...”“在于...而非...”“不在于...而在于...”“不...而...”“不...而是...”“不...而在于...”“更多是...而非...”“不是...而是...”“不只有...还有...”“之所以...是因为...”“既是...也是...”等模板句；不要写先否定再转折的句子，直接说观点，用具体产品、动作、结论和影响表达。

输出必须是严格 JSON，格式如下：
{{
  "overview": "......",
  "themes": ["......", "......"],
  "highlights": [
    {{"index": 3, "summary": "......"}},
    {{"index": 8, "summary": "......"}}
  ]
}}

约束：
- 只能引用给定 index。
- `highlights` 不可重复。
- 只输出 JSON，不要输出其他说明。

候选帖子：
{chr(10).join(post_blocks)}
"""
    return system_prompt, user_prompt


def request_post_summary(
    provider_configs: List[Tuple[str, str, str, str]],
    post_blocks: List[str],
) -> Tuple[Dict[str, object], str, str]:
    system_prompt, user_prompt = build_post_summary_prompts(post_blocks)

    parsed = None
    raw = ""
    provider_name = ""
    model = ""
    errors: List[str] = []
    for provider_name, base_url, model, api_key in provider_configs:
        try:
            raw = call_chat_completion(
                base_url, api_key, model, system_prompt, user_prompt
            )
            json_text = extract_json_from_response(raw)
            if not json_text:
                raise RuntimeError("empty JSON extraction")
            parsed = json.loads(json_text)
            break
        except (RuntimeError, json.JSONDecodeError, ValueError) as exc:
            errors.append(f"{provider_name}/{model}: {exc}")
            parsed = None
            continue

    if parsed is None:
        raise RuntimeError(
            "All AI providers failed for post summary: " + " | ".join(errors)
        )

    return parsed, provider_name, model


def select_topics_with_ai(
    topics: List[Topic],
    top_wechat: int,
    min_fit_score: int,
    min_ai_score: int,
    min_viral_score: int,
    min_breakout_score: int,
    provider_configs: List[Tuple[str, str, str, str]],
    *,
    cache_dir: Path | None = None,
    date_str: str = "",
) -> Tuple[Dict[str, List[Tuple[Topic, List[str]]]], str, str]:
    if cache_dir is not None and date_str:
        topic_fingerprint = "\n".join(
            f"{topic.title}\n{topic.summary}\n{topic.link_url}"
            for topic in topics
        )
        cache_key = _stable_hash(
            "topic_select|"
            f"provider={_provider_fingerprint(provider_configs)}|"
            f"top_wechat={top_wechat}|"
            f"min_fit={min_fit_score}|min_ai={min_ai_score}|"
            f"min_viral={min_viral_score}|min_breakout={min_breakout_score}|"
            f"topics={_stable_hash(topic_fingerprint)}"
        )
        cached = _read_cache_entry(cache_dir, date_str, cache_key)
        if cached is not None and isinstance(cached, dict):
            wechat_pairs: List[Tuple[Topic, List[str]]] = []
            for raw in cached.get("wechat", []):
                if isinstance(raw, dict):
                    wechat_pairs.append((_topic_from_cache_dict(raw), []))
            return {"wechat": wechat_pairs}, "", ""

    first_pass_candidates: List[Topic] = []
    used_topic_ids = set()
    used_provider = ""
    used_model = ""

    topic_chunks: List[Tuple[int, List[Topic]]] = [
        (start, topics[start : start + FIRST_PASS_CHUNK_SIZE])
        for start in range(0, len(topics), FIRST_PASS_CHUNK_SIZE)
    ]

    def _run_topic_chunk(
        chunk_start: int, chunk_topics: List[Topic]
    ) -> Tuple[int, List[Topic], str, str]:
        topic_blocks = [
            build_topic_block(i, topic) for i, topic in enumerate(chunk_topics, start=1)
        ]
        selected_indices, used_provider, used_model = request_selected_indices(
            provider_configs=provider_configs,
            topic_blocks=topic_blocks,
            min_fit_score=min_fit_score,
            min_ai_score=min_ai_score,
            min_viral_score=min_viral_score,
            min_breakout_score=min_breakout_score,
            stage_label=f"第一轮初筛（候选 {chunk_start + 1}-{chunk_start + len(chunk_topics)}）",
            extra_instruction="这是第一轮初筛。请宁缺毋滥，只保留明显达标且具备AI圈传播爆点的主题。",
        )
        picked: List[Topic] = []
        for idx in selected_indices:
            if idx < 1 or idx > len(chunk_topics):
                continue
            picked.append(chunk_topics[idx - 1])
        return chunk_start, picked, used_provider, used_model

    chunk_results: List[Tuple[int, List[Topic], str, str]] = []
    if len(topic_chunks) > 1 and FIRST_PASS_CONCURRENCY > 1:
        with ThreadPoolExecutor(max_workers=FIRST_PASS_CONCURRENCY) as pool:
            futures = [
                pool.submit(_run_topic_chunk, start, chunk)
                for start, chunk in topic_chunks
            ]
            for fut in futures:
                chunk_results.append(fut.result())
    else:
        for start, chunk in topic_chunks:
            chunk_results.append(_run_topic_chunk(start, chunk))

    # Restore chunk order so logs and provider attribution stay deterministic.
    chunk_results.sort(key=lambda item: item[0])
    for chunk_start, picked_topics, prov, mdl in chunk_results:
        if prov:
            used_provider = prov
            used_model = mdl
        for topic in picked_topics:
            if id(topic) in used_topic_ids:
                continue
            used_topic_ids.add(id(topic))
            first_pass_candidates.append(topic)

    if not first_pass_candidates:
        return {"wechat": []}, used_provider, used_model

    final_topic_blocks = [
        build_topic_block(i, topic)
        for i, topic in enumerate(first_pass_candidates, start=1)
    ]
    final_indices, used_provider, used_model = request_selected_indices(
        provider_configs=provider_configs,
        topic_blocks=final_topic_blocks,
        min_fit_score=min_fit_score,
        min_ai_score=min_ai_score,
        min_viral_score=min_viral_score,
        min_breakout_score=min_breakout_score,
        stage_label="第二轮复筛（全局复核）",
        extra_instruction="这是第二轮全局复核。请删除重复事件、边缘项、低传播价值项，只保留真正同时满足 AI相关性、传播价值、独立成文价值、以及AI圈爆款潜力的主题。",
    )

    out: List[Tuple[Topic, List[str]]] = []
    used_indices = set()
    for idx in final_indices:
        if idx < 1 or idx > len(first_pass_candidates) or idx in used_indices:
            continue
        used_indices.add(idx)
        out.append((first_pass_candidates[idx - 1], []))

    source_order = {id(topic): idx for idx, topic in enumerate(topics)}
    out.sort(key=lambda pair: source_order[id(pair[0])])

    if top_wechat > 0:
        out = out[:top_wechat]

    if cache_dir is not None and date_str:
        _write_cache_entry(
            cache_dir,
            date_str,
            cache_key,
            "select_topics_with_ai",
            {"wechat": [_topic_to_cache_dict(topic) for topic, _ in out]},
        )

    return {"wechat": out}, used_provider, used_model


def summarize_posts_with_ai(
    posts: List[PostItem],
    provider_configs: List[Tuple[str, str, str, str]],
    candidate_limit: int,
    *,
    cache_dir: Path | None = None,
    date_str: str = "",
) -> Tuple[Dict[str, object], str, str]:
    candidates = select_post_candidates(posts, limit=candidate_limit)
    if not candidates:
        return {"overview": "", "themes": [], "highlights": []}, "", ""

    if cache_dir is not None and date_str:
        post_fingerprint = json.dumps(
            [
                {
                    "tweet_id": post.tweet_id,
                    "content": post.content,
                    "created_at": post.created_at,
                    "link": post.link,
                }
                for post in posts
            ],
            ensure_ascii=False,
            sort_keys=True,
        )
        cache_key = _stable_hash(
            "post_summary|"
            f"provider={_provider_fingerprint(provider_configs)}|"
            f"candidate_limit={candidate_limit}|"
            f"posts={_stable_hash(post_fingerprint)}"
        )
        cached = _read_cache_entry(cache_dir, date_str, cache_key)
        if cached is not None and isinstance(cached, dict):
            return cached, "", ""

    post_blocks = [
        build_post_block(i, post) for i, post in enumerate(candidates, start=1)
    ]
    parsed, provider_name, model = request_post_summary(
        provider_configs=provider_configs,
        post_blocks=post_blocks,
    )

    overview = normalize_whitespace(str(parsed.get("overview", "")))
    raw_themes = parsed.get("themes", [])
    themes = []
    if isinstance(raw_themes, list):
        for theme in raw_themes:
            text = normalize_whitespace(str(theme))
            if text and text not in themes:
                themes.append(text)

    raw_highlights = parsed.get("highlights", [])
    highlights: List[Dict[str, str]] = []
    used = set()
    if isinstance(raw_highlights, list):
        for item in raw_highlights:
            if not isinstance(item, dict):
                continue
            idx = item.get("index")
            if (
                not isinstance(idx, int)
                or idx < 1
                or idx > len(candidates)
                or idx in used
            ):
                continue
            used.add(idx)
            summary = normalize_whitespace(str(item.get("summary", "")))
            if not summary:
                continue
            post = candidates[idx - 1]
            highlights.append(
                {
                    "author_name": post.author_name,
                    "author_screen_name": post.author_screen_name,
                    "link": post.link,
                    "summary": summary,
                }
            )

    result = {
        "overview": overview,
        "themes": themes,
        "highlights": highlights,
        "source_count": len(posts),
        "candidate_count": len(candidates),
    }
    if cache_dir is not None and date_str:
        _write_cache_entry(
            cache_dir,
            date_str,
            cache_key,
            "summarize_posts_with_ai",
            result,
        )
    return result, provider_name, model


def build_output_md(
    date_str: str,
    source_path: Path,
    selections: Dict[str, List[Tuple[Topic, List[str]]]],
    post_summary: Dict[str, object],
    post_source_path: Path,
    reddit_summary_section: List[str],
) -> str:
    today = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"# 📌 {date_str} 每日 AI 选题",
        "",
        f"**生成时间**: {today}",
        f"**来源汇总**: {source_path.as_posix()}",
        "**筛选范围**: AI技巧、AI使用技巧、AI产品使用技巧、AI领域新产品、AI新模型发布、AI Agent相关、AI领域新闻、AI公司相关的新闻、Prompt使用技巧、AI开源项目、开源项目推荐、AI+行业相关新闻、AI+伦理、AI+安全、AI领域易于传播的爆款",
        "",
        "---",
        "",
    ]
    lines.extend(render_post_summary_section(post_summary, post_source_path))
    lines.append("---")
    lines.append("")
    if reddit_summary_section:
        lines.extend(reddit_summary_section)
        lines.append("---")
        lines.append("")
    lines.extend(render_platform_section(selections["wechat"]))
    lines.append("")
    return "\n".join(lines)


def render_reddit_topic_section(
    reddit_items: List[Dict[str, str]],
    source_display: str,
) -> List[str]:
    if not reddit_items:
        return []

    grouped: Dict[str, List[Dict[str, str]]] = {}
    seen_links = set()
    for item in reddit_items:
        normalized_link = normalize_url(item.get("link", ""))
        if normalized_link in seen_links:
            continue
        seen_links.add(normalized_link)

        subreddit = (
            item.get("subreddit")
            or infer_subreddit_from_url(normalized_link)
            or "unknown"
        )
        grouped.setdefault(subreddit, []).append(
            {
                "summary": item.get("summary", ""),
                "link": normalized_link,
            }
        )

    if not grouped:
        return []

    out: List[str] = ["## 💬 Reddit 社区信号", ""]
    if source_display:
        out.append(f"**来源**: {source_display}")
    total_posts = sum(len(items) for items in grouped.values())
    out.append(f"**统计**: {len(grouped)} 个板块，{total_posts} 条入选帖子")
    out.append("")

    for sub_name in sorted(grouped.keys()):
        out.append(f"### r/{sub_name}")
        out.append("")
        for item in grouped[sub_name]:
            summary = normalize_whitespace(item.get("summary", ""))
            link = normalize_whitespace(item.get("link", ""))
            if link:
                out.append(f"- {summary} [原帖]({link})")
            else:
                out.append(f"- {summary}")
        out.append("")

    return out


def render_post_summary_section(
    post_summary: Dict[str, object], post_source_path: Path
) -> List[str]:
    out: List[str] = ["## 🧵 当天 Post 总结", ""]
    if not post_summary:
        out.extend(["未生成当天 Post 总结。", ""])
        return out

    out.append(f"**来源**: {post_source_path.as_posix()}")
    source_count = post_summary.get("source_count")
    candidate_count = post_summary.get("candidate_count")
    if source_count or candidate_count:
        out.append(
            f"**统计**: 原始帖子 {source_count or 0} 条，参与总结 {candidate_count or 0} 条"
        )
    out.append("")

    overview = normalize_whitespace(str(post_summary.get("overview", "")))
    if overview:
        out.append("**概览**:")
        out.append(overview)
        out.append("")

    themes = post_summary.get("themes", [])
    if isinstance(themes, list) and themes:
        out.append("**今日主线**:")
        for theme in themes:
            text = normalize_whitespace(str(theme))
            if text:
                out.append(f"- {text}")
        out.append("")

    highlights = post_summary.get("highlights", [])
    if isinstance(highlights, list) and highlights:
        open_source_highlights = [
            item
            for item in highlights
            if isinstance(item, dict) and post_highlight_looks_like_open_source_project(item)
        ]
        other_highlights = [
            item
            for item in highlights
            if isinstance(item, dict) and not post_highlight_looks_like_open_source_project(item)
        ]

        def render_highlights(section_title: str, items: List[Dict[str, object]]) -> None:
            if not items:
                return
            out.append(section_title)
            for item in items:
                author_name = (
                    normalize_whitespace(str(item.get("author_name", ""))) or "未知作者"
                )
                author_screen_name = normalize_whitespace(
                    str(item.get("author_screen_name", ""))
                )
                handle = f" (@{author_screen_name})" if author_screen_name else ""
                summary = normalize_whitespace(str(item.get("summary", "")))
                link = normalize_whitespace(str(item.get("link", "")))
                if not summary:
                    continue
                if link:
                    out.append(f"- **{author_name}{handle}**: {summary} [原帖]({link})")
                else:
                    out.append(f"- **{author_name}{handle}**: {summary}")
            out.append("")

        render_highlights(f"**{OPEN_SOURCE_PROJECT_CATEGORY}**:", open_source_highlights)
        render_highlights("**重点帖子**:", other_highlights)

    if out[-1] != "":
        out.append("")
    return out


def render_platform_section(items: List[Tuple[Topic, List[str]]]) -> List[str]:
    out: List[str] = []
    if not items:
        return ["未筛选到符合条件的 AI 主题。"]

    grouped: Dict[str, List[Tuple[Topic, List[str]]]] = {}
    for topic, reasons in items:
        grouped.setdefault(topic_display_category_heading(topic), []).append((topic, reasons))

    for category_heading, category_items in grouped.items():
        if out:
            out.append("---")
            out.append("")
        out.append(category_heading)
        out.append("")

        for topic, _reasons in category_items:
            out.append(f"### {topic.title}")
            out.append("")
            out.append(f"**评分**: {topic.score}/100")
            out.append("")
            if topic.author:
                out.append(f"**作者**: {topic.author}")
                out.append("")
            out.append("**摘要**:")
            out.append(topic.summary)
            out.append("")
            out.append("**关键要点**:")
            for keypoint in topic.keypoints:
                out.append(f"- {keypoint}")
            out.append("")
            out.append(f"**链接**: [{topic.link_text}]({topic.link_url})")
            out.append("")
            out.append(f"**源文件**: [{topic.source_text}]({topic.source_url})")
            out.append("")
    return out


def resolve_date(date_arg: str) -> str:
    if date_arg:
        if not re.match(r"^\d{8}$", date_arg):
            raise ValueError("--date must be YYYYMMDD")
        return date_arg
    return dt.datetime.now().strftime("%Y%m%d")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Filter AI-related daily topics from daily summary."
    )
    parser.add_argument(
        "--date", default="", help="Date in YYYYMMDD, default is today."
    )
    parser.add_argument(
        "--top-wechat",
        type=int,
        default=0,
        help="Max selected topics for WeChat. 0 means no cap.",
    )
    parser.add_argument(
        "--min-fit-score", type=int, default=7, help="Minimum WeChat fit score (0-10)."
    )
    parser.add_argument(
        "--min-ai-score", type=int, default=6, help="Minimum AI relevance score (0-10)."
    )
    parser.add_argument(
        "--min-viral-score",
        type=int,
        default=6,
        help="Minimum viral potential score (0-10).",
    )
    parser.add_argument(
        "--min-breakout-score",
        type=int,
        default=6,
        help="Minimum breakout potential score in AI circles (0-10).",
    )
    parser.add_argument(
        "--provider",
        choices=["auto", "claude", "doubao", "gemini", "openai", "deepseek"],
        default="auto",
        help="AI provider. Default: auto.",
    )
    parser.add_argument(
        "--env-file",
        default="",
        help="Optional .env file to load API keys before running.",
    )
    parser.add_argument(
        "--base-dir",
        default="mymind",
        help="Logical mymind base; resolved below the bound mymind root.",
    )
    parser.add_argument("--mymind-root", default="", help="Explicit mymind root; defaults to CCTOOLS_MYMIND_ROOT.")
    parser.add_argument("--skill-data-dir", default="", help="App-private Skill data directory.")
    parser.add_argument("--run-dir", default="", help="App-private Run working directory.")
    parser.add_argument(
        "--no-post-summary",
        action="store_true",
        help="Skip summarizing mymind/post/YYYYMMDD/posts.json.",
    )
    parser.add_argument(
        "--post-candidate-limit",
        type=int,
        default=0,
        help="How many candidate posts to send into the post summary stage. 0 means no cap.",
    )
    parser.add_argument(
        "--reddit-max-posts",
        type=int,
        default=12,
        help="Max selected Reddit community-signal posts. 0 means no cap.",
    )
    args = parser.parse_args()

    if args.env_file:
        load_env_file(args.env_file)

    try:
        mymind_root = require_mymind_root(args.mymind_root)
        skill_data_dir = require_skill_data_dir(args.skill_data_dir)
        optional_run_dir(args.run_dir)
        base_dir = resolve_mymind_path(args.base_dir, mymind_root, "--base-dir")
    except RuntimePathError as exc:
        parser.error(str(exc))

    date_str = resolve_date(args.date)
    source_path = base_dir / "daily-summary" / f"{date_str}_daily_summary.md"
    if not source_path.exists():
        raise FileNotFoundError(f"Daily summary not found: {source_path}")

    content = source_path.read_text(encoding="utf-8")
    reddit_source_display, compact_reddit_items = extract_reddit_summary_items(content)
    topics = extract_topics(content)
    if not topics:
        raise RuntimeError("No topic entries parsed from daily summary.")
    topics, removed_wechat_count = remove_wechat_source_topics(topics)

    provider_configs = choose_ai_providers(args.provider)
    post_source_path = base_dir / "post" / date_str / "posts.json"
    # LLM cache is app-private state, never content under mymind or the package.
    cache_dir = skill_data_dir / "daily-topic-cache"

    # The three top-level stages have no data dependency between them (the only
    # post-stage touch-up is the is_reddit_topic string filter on topic links,
    # which does not depend on the Reddit stage's output). Each stage calls the
    # LLM calls use the stateless package-local AI client, so it is safe
    # to run them in parallel and join the results afterwards.
    topic_task: Callable[[], Tuple[Dict[str, List[Tuple[Topic, List[str]]]], str, str]] = (
        lambda: select_topics_with_ai(
            topics=topics,
            top_wechat=args.top_wechat,
            min_fit_score=args.min_fit_score,
            min_ai_score=args.min_ai_score,
            min_viral_score=args.min_viral_score,
            min_breakout_score=args.min_breakout_score,
            provider_configs=provider_configs,
            cache_dir=cache_dir,
            date_str=date_str,
        )
    )
    reddit_task: Callable[
        [], Tuple[List[Dict[str, str]], str, str]
    ] = lambda: select_reddit_items_with_ai(
        compact_reddit_items=compact_reddit_items,
        provider_configs=provider_configs,
        max_posts=args.reddit_max_posts,
        cache_dir=cache_dir,
        date_str=date_str,
    )

    def _post_task() -> Tuple[Dict[str, object], str, str, str]:
        if args.no_post_summary or not post_source_path.exists():
            return {"overview": "", "themes": [], "highlights": []}, "", "", ""
        try:
            posts = parse_posts_json(post_source_path)
            summary, prov, mdl = summarize_posts_with_ai(
                posts=posts,
                provider_configs=provider_configs,
                candidate_limit=args.post_candidate_limit,
                cache_dir=cache_dir,
                date_str=date_str,
            )
            return summary, prov, mdl, ""
        except Exception as exc:  # noqa: BLE001 - mirror previous behavior
            return (
                {
                    "overview": "",
                    "themes": [],
                    "highlights": [],
                    "source_count": 0,
                    "candidate_count": 0,
                    "error": str(exc),
                },
                "",
                "",
                str(exc),
            )

    with ThreadPoolExecutor(max_workers=3) as pool:
        topic_future = pool.submit(topic_task)
        reddit_future = pool.submit(reddit_task)
        post_future = pool.submit(_post_task)

        selections, used_provider, used_model = topic_future.result()
        reddit_items, reddit_provider, reddit_model = reddit_future.result()
        post_summary, post_provider, post_model, _post_err = post_future.result()

    selections["wechat"] = [
        item for item in selections["wechat"] if not is_reddit_topic(item[0])
    ]
    reddit_summary_section = render_reddit_topic_section(
        reddit_items=reddit_items,
        source_display=reddit_source_display,
    )

    output_dir = base_dir / "daily-topic"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{date_str}_daily_topic.md"
    output_md = build_output_md(
        date_str=date_str,
        source_path=source_path,
        selections=selections,
        post_summary=post_summary,
        post_source_path=post_source_path,
        reddit_summary_section=reddit_summary_section,
    )
    output_path.write_text(output_md, encoding="utf-8")

    print(f"Generated: {output_path}")
    print(f"Parsed topics: {len(topics) + removed_wechat_count}")
    print(f"Filtered WeChat source topics: {removed_wechat_count}")
    print(f"Selected: WeChat={len(selections['wechat'])}")
    print(f"Selected Reddit posts: {len(reddit_items)}")
    if post_summary:
        print(f"Post summary highlights: {len(post_summary.get('highlights', []))}")
    if reddit_summary_section:
        print("Reddit summary: filtered with community-signal rules")
        print(f"Reddit AI provider: {reddit_provider}")
        print(f"Reddit AI model: {reddit_model}")
    print(f"Topic AI provider: {used_provider}")
    print(f"Topic AI model: {used_model}")
    if post_provider or post_model:
        print(f"Post AI provider: {post_provider}")
        print(f"Post AI model: {post_model}")


if __name__ == "__main__":
    main()
