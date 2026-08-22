from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from .config import AppConfig, load_config
from .content_ai import KnowledgeWikiWriter
from .entity_ai import LLMEntityExtractor
from .runtime_paths import RuntimePathError, optional_run_dir, require_skill_data_dir, require_content_root


SOURCE_NOTE_WRITE_LOCK = threading.Lock()
GROUP_AI_CACHE_VERSION = "v1"

STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "over", "your",
    "their", "about", "while", "still", "using", "when", "after", "have", "will",
    "just", "more", "than", "they", "them", "what", "which", "been", "also",
    "through", "some", "many", "much", "does", "were", "where", "why", "how",
    "all", "can", "you", "its", "our", "out", "are", "not", "use", "used",
    "new", "too", "via", "but", "has", "had", "was", "day", "week", "today",
    "yesterday", "tomorrow", "like", "there", "being", "very", "only", "make",
    "made", "good", "best", "first", "last", "one", "two", "three", "most",
    "among", "across", "under", "should",
}

# Concepts are reusable ideas/methods; entities are named subjects; themes are broad navigation buckets.
GENERIC_CONCEPT_KEYS = {
    "agent",
    "agents",
    "agentic",
    "workflow",
    "workflows",
    "automation",
    "prompt",
    "prompts",
    "context",
    "contexts",
    "eval",
    "evals",
    "benchmark",
    "benchmarks",
    "retrieval",
    "rag",
    "reason",
    "reasoning",
    "robot",
    "robots",
    "robotics",
    "memory",
    "inference",
    "pricing",
    "subscription",
    "wiki",
}

CONCEPT_CANONICAL_MAP = {
    "agent": "AI Agents",
    "agents": "AI Agents",
    "agentic": "AI Agents",
    "multi-agent": "AI Agents",
    "coding agent": "AI Agents",
    "coding agents": "AI Agents",
    "workflow": "Workflow Automation",
    "workflows": "Workflow Automation",
    "automation": "Workflow Automation",
    "orchestrate": "Workflow Automation",
    "research": "Research Workflows",
    "researcher": "Research Workflows",
    "researchers": "Research Workflows",
    "researching": "Research Workflows",
    "prompt": "Prompt Engineering",
    "prompts": "Prompt Engineering",
    "prompting": "Prompt Engineering",
    "context": "Context Engineering",
    "contexts": "Context Engineering",
    "eval": "Evaluation",
    "evals": "Evaluation",
    "evaluate": "Evaluation",
    "evaluator": "Evaluation",
    "benchmark": "Evaluation",
    "benchmarks": "Evaluation",
    "search": "RAG",
    "searches": "RAG",
    "searching": "RAG",
    "retrieval": "RAG",
    "reason": "Reasoning",
    "reasons": "Reasoning",
    "reasoning": "Reasoning",
    "memory": "Memory Systems",
    "pricing": "Model Pricing",
    "subscription": "Model Pricing",
    "inference": "Inference Economics",
    "robot": "Robotics",
    "robots": "Robotics",
    "wiki": "Knowledge Bases",
    "ai generated content": "AI-Generated Content",
    "ai-generated content": "AI-Generated Content",
    "ai hallucinations": "AI Hallucination",
    "agent workflow": "Workflow Automation",
    "agent workflows": "Workflow Automation",
    "agentic workflow": "Workflow Automation",
    "agentic workflows": "Workflow Automation",
    "attention mechanisms": "Attention Mechanism",
    "chain-of-thought": "Chain Of Thought",
    "chain of thought": "Chain Of Thought",
    "cognitive biases": "Cognitive Bias",
    "context windows": "Context Window",
    "dual-use technology": "Dual Use Technology",
    "dual use technology": "Dual Use Technology",
    "evaluation frameworks": "Evaluation Framework",
    "founder-market fit": "Founder Market Fit",
    "founder market fit": "Founder Market Fit",
    "knowledge graphs": "Knowledge Graph",
    "llm-as-a-judge": "LLM-As-A-Judge",
    "llm-as-judge": "LLM-As-A-Judge",
    "llm as a judge": "LLM-As-A-Judge",
    "mixture-of-experts": "Mixture Of Experts",
    "mixture of experts": "Mixture Of Experts",
    "product-market fit": "Product Market Fit",
    "product market fit": "Product Market Fit",
    "public-private partnerships": "Public-Private Partnership",
    "retrieval augmented generation": "RAG",
    "retrieval-augmented generation": "RAG",
    "sandboxed environments": "Sandbox Environments",
    "system prompts": "System Prompt",
    "test-driven development": "Test Driven Development",
    "test driven development": "Test Driven Development",
    "vision-language models": "Vision Language Models",
    "vision language models": "Vision Language Models",
    "voice interfaces": "Voice Interface",
}

CONCEPT_DROP_SET = {
    "agentschap",
    "reasonable",
    "unreasonable",
}

CONCEPT_RULES: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = [
    ("AI Agents", ("agent", "agents", "agentic", "智能体"), ("Agent Systems",)),
    ("Harness Engineering", ("harness", "harness engineering", "主循环", "控制面", "上下文治理", "恢复路径"), ("AI Coding Tools", "Developer Workflow")),
    ("Context Engineering", ("context engineering", "上下文工程", "context window", "提示缓存", "prompt caching"), ("Knowledge Operations",)),
    ("Prompt Engineering", ("prompt", "prompts", "提示词", "system prompt"), ("Knowledge Operations",)),
    ("RAG", ("rag", "retrieval", "检索增强"), ("Knowledge Operations",)),
    ("Model Pricing", ("pricing", "subscription", "按需付费", "api key", "退款", "token 成本"), ("AI Business",)),
    ("Open Source Strategy", ("open source", "开源", "closed harness", "封闭"), ("AI Business", "Open Source Ecosystems")),
    ("Inference Economics", ("capacity", "算力", "负载", "成本", "throughput", "gpu"), ("AI Business",)),
    ("Workflow Automation", ("workflow", "automation", "pipeline", "编排", "orchestrate"), ("Developer Workflow",)),
    ("Knowledge Bases", ("knowledge base", "wiki", "obsidian", "知识库", "markdown wiki"), ("Knowledge Operations",)),
    ("Research Workflows", ("research", "paper", "dataset", "corpus", "研究"), ("Knowledge Operations",)),
    ("Memory Systems", ("memory", "long-term memory", "记忆", "长期记忆"), ("Knowledge Operations",)),
    ("Reasoning", ("reasoning", "reason", "推理"), ("Knowledge Operations",)),
    ("Evaluation", ("eval", "evals", "benchmark", "评测"), ("Knowledge Operations",)),
    ("Robotics", ("robot", "robots", "robotics", "具身"), ("Creative AI",)),
    ("AIGC", ("aigc",), ("Creative AI",)),
    ("Image Generation", ("image generation", "image prompt", "文生图", "图像"), ("Creative AI",)),
    ("Slides And Visualizations", ("marp", "slide", "slides", "matplotlib", "visualization"), ("Knowledge Operations",)),
]

ENTITY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("Anthropic", ("anthropic",)),
    ("Claude", ("claude",)),
    ("Claude Code", ("claude code",)),
    ("Claude Cowork", ("claude cowork",)),
    ("OpenClaw", ("openclaw",)),
    ("OpenAI", ("openai",)),
    ("Codex", ("codex",)),
    ("Cursor", ("cursor",)),
    ("GitHub", ("github",)),
    ("Google", ("google",)),
    ("Gemini", ("gemini",)),
    ("Qwen", ("qwen", "通义千问", "千问")),
    ("DeepSeek", ("deepseek",)),
    ("Zhipu", ("智谱", "zhipu", "glm")),
    ("Doubao", ("doubao", "豆包")),
    ("NVIDIA", ("nvidia",)),
    ("Obsidian", ("obsidian",)),
    ("Marp", ("marp",)),
    ("X", ("x.com", "twitter", "tweet", "tweets")),
    ("WeChat", ("wechat", "微信")),
]

ENTITY_CANONICAL_MAP = {
    "claude": "Claude",
    "claude code": "Claude Code",
    "chatgpt": "ChatGPT",
    "gpt4o": "GPT-4o",
    "gpt-4o": "GPT-4o",
    "gemini": "Gemini",
    "nvidia": "NVIDIA",
    "openai": "OpenAI",
    "openclaw": "OpenClaw",
    "github": "GitHub",
    "xai": "xAI",
    "youtube": "YouTube",
    "vscode": "VSCode",
    "x": "X",
}

ENTITY_TYPE_OVERRIDES = {
    "anthropic": "organization",
    "openai": "organization",
    "google": "organization",
    "nvidia": "organization",
    "xai": "organization",
    "servicenow": "organization",
    "cursor": "product",
    "claude": "product",
    "claude code": "product",
    "chatgpt": "product",
    "gemini": "product",
    "gpt-4o": "product",
    "openclaw": "product",
    "atlas": "product",
    "chrome": "browser",
    "x": "platform",
    "model context protocol": "protocol",
}

DOMAIN_ENTITY_RE = re.compile(r"^[a-z0-9-]+\.(?:com|net|org|io|ai|dev|app|sh|me|co)$", re.IGNORECASE)
ENTITY_HIGH_SALIENCE = 4
CONCEPT_STATUS_PUBLISHED = "published"
CONCEPT_STATUS_EMERGING = "emerging"
CONCEPT_STATUS_CANDIDATE = "candidate"
CONCEPT_STATUS_DEPRECATED = "deprecated"
CONCEPT_STATUS_REJECTED = "rejected"
CONCEPT_STATUS_MERGED = "merged"
PUBLISHED_CONCEPT_STATUSES = {CONCEPT_STATUS_PUBLISHED}
CONCEPT_PAGE_STATUSES = {CONCEPT_STATUS_PUBLISHED}
THEME_PAGE_STATUSES = {CONCEPT_STATUS_PUBLISHED, CONCEPT_STATUS_EMERGING}
PUBLISHED_ENTITY_STATUSES = {CONCEPT_STATUS_PUBLISHED}
AI_RELATED_KEYWORDS = {
    "ai",
    "aigc",
    "agent",
    "agentic",
    "alignment",
    "anthropic",
    "automation",
    "chatgpt",
    "claude",
    "codex",
    "context",
    "copilot",
    "cursor",
    "deepseek",
    "diffusion",
    "embedding",
    "eval",
    "gemini",
    "gpt",
    "gpu",
    "inference",
    "llm",
    "memory",
    "model",
    "multimodal",
    "nvidia",
    "openai",
    "prompt",
    "qwen",
    "rag",
    "reasoning",
    "retrieval",
    "robot",
    "token",
    "transformer",
    "vibe",
}
AI_ENTITY_KEYS = {
    "anthropic",
    "amazonbedrock",
    "appleintelligence",
    "chatgpt",
    "claude",
    "claudecode",
    "codex",
    "copilot",
    "cursor",
    "deepseek",
    "elevenlabs",
    "gemini",
    "githubcopilot",
    "googledeepmind",
    "gpt4o",
    "huggingface",
    "langchain",
    "langgraph",
    "langsmith",
    "llamaindex",
    "lovable",
    "mcp",
    "mistralai",
    "modelcontextprotocol",
    "nvidia",
    "openai",
    "openclaw",
    "perplexity",
    "pytorch",
    "qwen",
    "replit",
    "sora",
    "stabilityai",
    "vllm",
    "xai",
}
BACKGROUND_ENTITY_KEYS = {
    "android",
    "apache20",
    "arxiv",
    "c",
    "c++",
    "china",
    "docker",
    "europeanunion",
    "firefox",
    "git",
    "github",
    "hackernews",
    "html",
    "instagram",
    "java",
    "javascript",
    "linux",
    "notion",
    "python",
    "reddit",
    "rust",
    "slack",
    "spotify",
    "sqlite",
    "substack",
    "whitehouse",
    "wikipedia",
    "windows",
    "x",
    "youtube",
    "berniesanders",
    "billgates",
    "techcrunch",
    "unitedstates",
    "usa",
    "wired",
    "axios",
}
ENTITY_CONCEPT_LIKE_KEYS = {
    "ai",
    "aiagent",
    "aiagents",
    "aipsychosis",
    "artificialintelligence",
    "agenticai",
    "agenticengineering",
    "codingagent",
    "contextcompression",
    "contextengineering",
    "contextwindow",
    "constitutionalai",
    "diffusionmodel",
    "diffusionmodels",
    "diffusionlargelanguagemodels",
    "discretediffusionmodel",
    "largelanguagemodel",
    "largelanguagemodels",
}

THEME_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("AI Coding Tools", ("claude code", "codex", "cursor", "openclaw", "copilot", "harness")),
    ("AI Business", ("subscription", "pricing", "api", "token", "profit", "cost", "商业", "付费")),
    ("Knowledge Operations", ("knowledge base", "wiki", "obsidian", "markdown", "summary", "summarize", "检索", "context")),
    ("Developer Workflow", ("workflow", "automation", "pipeline", "agent", "coding", "开发流程")),
    ("Open Source Ecosystems", ("open source", "开源", "github", "community")),
    ("Social Signals", ("x.com", "tweet", "retweet", "timeline", "post", "repost")),
    ("Prompt Engineering", ("prompt engineering", "system prompt", "few-shot", "chain-of-thought", "cot", "prompt optimization", "prompt design")),
    ("Creative AI", ("image", "visual", "video", "photo")),
]

CONTENT_TYPES = {"fact", "opinion", "signal", "tactic", "thesis"}
STATUS_VALUES = {"inbox", "processed", "evergreen_candidate", "evergreen", "archived"}
LEVEL_VALUES = {"high", "medium", "low"}
ACTION_VALUES = {"write", "test", "track", "ignore", "share", "build", "none"}

GROUP_DISPLAY_NAME_MAP: dict[str, dict[str, str]] = {
    "concept": {
        "AI Agents": "AI 智能体",
        "Harness Engineering": "控制面工程",
        "Context Engineering": "上下文工程",
        "Prompt Engineering": "提示词工程",
        "RAG": "检索增强生成",
        "Model Pricing": "模型定价",
        "Open Source Strategy": "开源策略",
        "Inference Economics": "推理经济学",
        "Workflow Automation": "工作流自动化",
        "Knowledge Bases": "知识库",
        "Research Workflows": "研究工作流",
        "Memory Systems": "记忆系统",
        "Reasoning": "推理",
        "Evaluation": "评测",
        "Robotics": "机器人",
        "AIGC": "AIGC",
        "Image Generation": "图像生成",
        "Slides And Visualizations": "演示与可视化",
    },
    "theme": {
        "AI Coding Tools": "AI 编程工具",
        "AI Business": "AI 商业",
        "Knowledge Operations": "知识运营",
        "Developer Workflow": "开发者工作流",
        "Open Source Ecosystems": "开源生态",
        "Social Signals": "社交信号",
        "Creative AI": "创意 AI",
        "Agent Systems": "智能体系统",
        "AI Agents": "AI 智能体",
        "Workflow Automation": "工作流自动化",
        "Inference Economics": "推理经济学",
        "Context Engineering": "上下文工程",
        "Open Source Strategy": "开源策略",
        "Reasoning": "推理",
        "Knowledge Bases": "知识库",
        "RAG": "检索增强生成",
        "Research Workflows": "研究工作流",
        "Model Pricing": "模型定价",
        "Memory Systems": "记忆系统",
        "AI Infrastructure": "AI 基础设施",
        "AI Hardware": "AI 硬件",
        "AI Safety": "AI 安全",
        "AI Governance": "AI 治理",
        "AI Regulation": "AI 监管",
        "AI Security": "AI 安全攻防",
        "AI Ethics": "AI 伦理",
        "AI Research": "AI 研究",
        "Model Development": "模型研发",
        "Model Evaluation": "模型评测",
        "Data And Training": "数据与训练",
        "Multimodal AI": "多模态 AI",
        "Generative AI": "生成式 AI",
        "AI Products": "AI 产品",
        "Enterprise AI": "企业 AI",
        "Consumer AI": "消费者 AI",
        "AI Education": "AI 教育",
        "Future Of Work": "未来工作",
        "AI Ecosystem": "AI 生态",
        "Venture Capital": "风险投资",
        "Robotics": "机器人",
        "AI Engineering": "AI 工程",
        "Prompt Engineering": "提示词工程",
        "Computer Architecture": "计算机体系结构",
        "Web Development": "Web 开发",
        "Distributed Systems": "分布式系统",
        "Software Architecture": "软件架构",
        "Operating Systems": "操作系统",
    },
    "entity": {},
}

ALL_CONCEPTS: list[str] = list(GROUP_DISPLAY_NAME_MAP["concept"].keys())
ALL_THEMES: list[str] = list(GROUP_DISPLAY_NAME_MAP["theme"].keys())
APPROVED_CONCEPTS = set(ALL_CONCEPTS)


def theme_reserved_name_keys(
    theme_index: dict[str, list[dict[str, Any]]] | None = None,
) -> set[str]:
    """Canonical theme names plus any theme tags seen in the corpus."""
    keys = {taxonomy_name_key(name) for name in ALL_THEMES}
    if theme_index:
        keys |= {taxonomy_name_key(name) for name in theme_index}
    return keys


def concept_blocked_by_theme(name: str, theme_index: dict[str, list[dict[str, Any]]], config: AppConfig) -> bool:
    if not config.theme_wins_overlap:
        return False
    return taxonomy_name_key(name) in theme_reserved_name_keys(theme_index)

THEME_CANONICAL_MAP = {
    "ai assisted coding": "AI Coding Tools",
    "ai developer workflow": "Developer Workflow",
    "developer workflows": "Developer Workflow",
    "developer tools": "Developer Workflow",
    "software engineering": "Developer Workflow",
    "ai coding": "AI Coding Tools",
    "coding agents": "AI Coding Tools",
    "ai agents": "AI Agents",
    "agentic ai": "AI Agents",
    "agentic workflows": "AI Agents",
    "multi-agent": "AI Agents",
    "multi agent": "AI Agents",
    "agent orchestration": "AI Agents",
    "ai economics": "AI Business",
    "api economics": "AI Business",
    "market competition": "AI Business",
    "business models": "AI Business",
    "inference economics": "Inference Economics",
    "inference cost": "Inference Economics",
    "token economics": "Inference Economics",
    "model pricing": "Model Pricing",
    "api pricing": "Model Pricing",
    "token pricing": "Model Pricing",
    "product management": "AI Products",
    "product strategy": "AI Products",
    "consumer applications": "Consumer AI",
    "consumer ai applications": "Consumer AI",
    "enterprise adoption": "Enterprise AI",
    "enterprise strategy": "Enterprise AI",
    "enterprise security": "AI Security",
    "enterprise ai security": "AI Security",
    "ai policy": "AI Governance",
    "ai policy governance": "AI Governance",
    "ai safety alignment": "AI Safety",
    "alignment": "AI Safety",
    "model safety": "AI Safety",
    "ai model optimization": "AI Infrastructure",
    "model optimization": "AI Infrastructure",
    "performance optimization": "AI Infrastructure",
    "system performance": "AI Infrastructure",
    "systems performance": "AI Infrastructure",
    "gpu infrastructure": "AI Infrastructure",
    "inference infrastructure": "AI Infrastructure",
    "ai research automation": "AI Research",
    "research automation": "Research Workflows",
    "research workflows": "Research Workflows",
    "ai model evaluation": "Model Evaluation",
    "evaluation": "Model Evaluation",
    "benchmarks": "Model Evaluation",
    "model training": "Model Development",
    "training data": "Data And Training",
    "synthetic data": "Data And Training",
    "ai ecosystem": "AI Ecosystem",
    "ai ecosystems": "AI Ecosystem",
    "developer ecosystem": "AI Ecosystem",
    "developer ecosystems": "AI Ecosystem",
    "tech ecosystem": "AI Ecosystem",
    "tech ecosystems": "AI Ecosystem",
    "open source ecosystem": "Open Source Ecosystems",
    "open source strategy": "Open Source Strategy",
    "content creation": "Creative AI",
    "content curation": "Creative AI",
    "image generation": "Creative AI",
    "video generation": "Creative AI",
    "human computer interaction": "AI Products",
    "human-computer interaction": "AI Products",
    "education": "AI Education",
    "ai learning": "AI Education",
    "workflow automation": "Workflow Automation",
    "automation pipeline": "Workflow Automation",
    "context engineering": "Context Engineering",
    "prompt engineering": "Prompt Engineering",
    "reasoning": "Reasoning",
    "chain of thought": "Reasoning",
    "knowledge base": "Knowledge Bases",
    "knowledge management": "Knowledge Bases",
    "rag": "RAG",
    "retrieval augmented": "RAG",
    "memory systems": "Memory Systems",
    "long-term memory": "Memory Systems",
    "workforce": "Future Of Work",
    "future work": "Future Of Work",
    "startup funding": "Venture Capital",
    "venture funding": "Venture Capital",
    "robotics": "Robotics",
    "embodied ai": "Robotics",
    "ai engineering": "AI Engineering",
    "ai engineer": "AI Engineering",
    "llm engineering": "AI Engineering",
    "ml engineering": "AI Engineering",
    "ai production engineering": "AI Engineering",
    # --- 地缘/架构/生产力:暂归入最接近的现有主题(新独立主题待候选积累后另议) ---
    "geopolitics of ai": "AI Governance",
    "geopolitics of tech": "AI Governance",
    "geo politics": "AI Governance",
    "geo-politics": "AI Governance",
    "political economy": "AI Governance",
    "model architectures": "Model Development",
    "architecture": "Model Development",
    "productivity tool": "Consumer AI",
    # --- 第二梯队:合并进现有主题 ---
    "ai business strategy": "AI Business",
    "ai business models": "AI Business",
    "business strategy": "AI Business",
    "market dynamics": "AI Business",
    "cybersecurity": "AI Security",
    "cyber security": "AI Security",
    "data privacy": "AI Security",
    "digital privacy": "AI Security",
    "digital rights": "AI Security",
    "defense technology": "AI Security",
    "national security": "AI Security",
    "information security": "AI Security",
    "startup strategy": "Venture Capital",
    "startup ecosystem": "Venture Capital",
    "entrepreneurship": "Venture Capital",
    "developer experience": "AI Coding Tools",
    "developer productivity": "AI Coding Tools",
    "systems programming": "AI Coding Tools",
    "computer vision": "Multimodal AI",
    "tech policy": "AI Governance",
    "tech regulation": "AI Governance",
    "corporate governance": "AI Governance",
    "platform governance": "AI Governance",
    "public sector ai": "AI Governance",
    "cloud infrastructure": "AI Infrastructure",
    "corporate strategy": "Enterprise AI",
    "risk management": "AI Safety",
    "trust and safety": "AI Safety",
    "open source models": "Open Source Ecosystems",
    "open source ai": "Open Source Ecosystems",
    "ai evaluation": "Model Evaluation",
    "semiconductor industry": "AI Hardware",
    "hardware engineering": "AI Hardware",
    "data engineering": "Data And Training",
    "deep learning theory": "AI Education",
    "machine learning theory": "AI Education",
    # --- 人工合并决策(2026-07-17 review B 档):候选 theme 归入最接近的现有 theme ---
    "system architecture": "Software Architecture",   # 与软件架构合并
    "ai developer tools": "AI Coding Tools",
    "ai product strategy": "AI Products",
    "ai in science": "AI Research",
    "ai for science": "AI Research",
    "medical ai": "AI Research",                       # 量小,暂归 AI Research
    "model efficiency": "AI Infrastructure",
    "llm training": "Data And Training",
    "machine learning": "Data And Training",
}

CONCEPT_RELEVANCE_HINTS: dict[str, tuple[str, ...]] = {
    "AI Agents": ("agent", "agents", "agentic", "智能体", "代理", "multi-agent", "tool use"),
    "Harness Engineering": ("harness", "控制面", "主循环", "恢复路径", "上下文治理"),
    "Context Engineering": ("context engineering", "上下文工程", "context window", "prompt caching", "上下文"),
    "Prompt Engineering": ("prompt", "prompts", "提示词", "system prompt"),
    "RAG": ("rag", "retrieval-augmented generation", "检索增强生成", "retrieval", "semantic search", "vector database", "embedding", "向量数据库", "检索"),
    "Model Pricing": ("pricing", "subscription", "套餐", "token 成本", "model pricing", "付费"),
    "Open Source Strategy": ("open source", "开源", "community", "封闭", "closed source"),
    "Inference Economics": ("inference", "throughput", "latency", "capacity", "token 成本", "推理成本", "算力"),
    "Workflow Automation": ("workflow", "automation", "pipeline", "orchestrate", "工作流", "自动化", "编排"),
    "Knowledge Bases": ("knowledge base", "wiki", "obsidian", "knowledge", "知识库", "markdown wiki"),
    "Research Workflows": ("research", "benchmark", "paper", "dataset", "研究", "评测流程"),
    "Memory Systems": ("memory", "long-term memory", "记忆", "长期记忆", "memory systems"),
    "Reasoning": ("reasoning", "推理", "thinking", "test-time", "reason"),
    "Evaluation": ("eval", "evaluation", "benchmark", "评测", "评分", "terminal bench"),
    "Robotics": ("robot", "robotics", "humanoid", "具身", "机器人"),
    "Image Generation": ("image generation", "文生图", "图像生成", "diffusion", "image prompt"),
}

THEME_RELEVANCE_HINTS: dict[str, tuple[str, ...]] = {
    "AI Coding Tools": ("claude code", "cursor", "copilot", "coding", "hooks", "开发者工具", "agentic coding"),
    "AI Business": ("pricing", "subscription", "商业", "收入", "利润", "成本", "enterprise"),
    "Knowledge Operations": ("knowledge", "wiki", "obsidian", "summary", "memory", "context", "知识库", "检索"),
    "Developer Workflow": ("workflow", "automation", "pipeline", "hooks", "开发流程", "工作流", "terminal"),
    "Open Source Ecosystems": ("open source", "开源", "community", "github", "生态"),
    "Social Signals": ("x.com", "tweet", "social", "timeline", "社交", "热议"),
    "Creative AI": ("image", "video", "photo", "design", "creative", "视觉"),
    "Agent Systems": ("agent", "agentic", "tool use", "planner", "执行", "智能体"),
}

SECTION_HEADING_ALIASES: dict[str, tuple[str, ...]] = {
    "summary": ("Summary", "总结"),
    "key_signals": ("Key Signals", "关键信号"),
    "why_it_matters": ("Why It Matters", "为什么值得看"),
    "my_take": ("My Take", "我的判断"),
    "action": ("Action", "行动"),
    "related_concepts": ("Related Concepts", "相关概念"),
    "related_entities": ("Related Entities", "相关对象"),
    "related_themes": ("Related Themes", "相关主题"),
    "open_questions": ("Open Questions", "开放问题"),
    "source_metadata": ("Source Metadata", "来源元数据"),
}


class RawEntry:
    def __init__(
        self,
        *,
        source_id: str,
        source_kind: str,
        source_path: str,
        title: str,
        date: str,
        canonical_url: str,
        author: str,
        content_hash: str,
        payload: dict[str, Any],
    ) -> None:
        self.source_id = source_id
        self.source_kind = source_kind
        self.source_path = source_path
        self.title = title
        self.date = date
        self.canonical_url = canonical_url
        self.author = author
        self.content_hash = content_hash
        self.payload = payload


def _yesterday() -> str:
    from datetime import date, timedelta
    return (date.today() - timedelta(days=1)).strftime("%Y%m%d")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compile raw mymind sources into a Markdown wiki.")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--content-root", default="", help="Explicit content root; defaults to OPENMIND_ROOT (legacy alias CCTOOLS_MYMIND_ROOT).")
    parser.add_argument("--skill-data-dir", default="", help="App-private Skill data directory.")
    parser.add_argument("--run-dir", default="", help="App-private Run working directory.")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--only", choices=["all", "articles", "posts"], default="all")
    parser.add_argument("--date", default=_yesterday(), help="Only compile raw entries whose source directory date matches YYYYMMDD. Defaults to yesterday.")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=2, help="Number of source entries to compile concurrently.")
    parser.add_argument("--aggregate-every", type=int, default=1000, help="Rebuild aggregate wiki pages after this many compiled source entries.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--taxonomy-dry-run", action="store_true", help="Scan and aggregate taxonomy state without compiling changed raw sources.")
    parser.add_argument("--taxonomy-review", action="store_true", help="Generate taxonomy review reports from current registry.")
    parser.add_argument("--apply-taxonomy-decisions", action="store_true", help="Apply taxonomy_decisions.yaml during aggregate rendering.")
    parser.add_argument("--rebuild-registry", action="store_true", help="Rebuild source registry from existing source notes before aggregation.")
    parser.add_argument("--only-taxonomy", action="store_true", help="Only rebuild aggregate taxonomy pages and state from existing source notes.")
    parser.add_argument("--only-render", action="store_true", help="Only rerender aggregate pages from existing state.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        root = require_content_root(args.content_root)
        data_dir = require_skill_data_dir(args.skill_data_dir)
        optional_run_dir(args.run_dir)
        config_path = Path(args.config).expanduser()
        if not config_path.is_absolute():
            skill_home = Path(os.environ.get("CCTOOLS_SKILL_HOME", Path(__file__).resolve().parents[2]))
            config_path = skill_home / config_path
        config = load_config(config_path.resolve(), root, data_dir)
    except RuntimePathError as exc:
        raise SystemExit(str(exc)) from exc
    stats = compile_wiki(
        config,
        force=args.force,
        only=args.only,
        date=args.date,
        limit=args.limit,
        workers=args.workers,
        aggregate_every=args.aggregate_every,
        dry_run=args.dry_run or args.taxonomy_dry_run,
        taxonomy_review=args.taxonomy_review,
        apply_taxonomy_decisions=args.apply_taxonomy_decisions,
        rebuild_registry=args.rebuild_registry,
        only_taxonomy=args.only_taxonomy or args.taxonomy_dry_run,
        only_render=args.only_render,
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


def compile_wiki(
    config: AppConfig,
    *,
    force: bool = False,
    only: str = "all",
    date: str | None = None,
    limit: int | None = None,
    workers: int = 2,
    aggregate_every: int = 1000,  # 与 parse_args 默认一致（曾经签名 20 与 CLI 1000 不一致）
    dry_run: bool = False,
    taxonomy_review: bool = False,
    apply_taxonomy_decisions: bool = False,
    rebuild_registry: bool = False,
    only_taxonomy: bool = False,
    only_render: bool = False,
) -> dict[str, Any]:
    print("[start] ensuring wiki directories", flush=True)
    ensure_dirs(config)
    if dry_run:
        print("[start] dry-run: skipping source note filename normalization", flush=True)
    elif config.generate_source_notes:
        print("[start] normalizing existing source note filenames", flush=True)
        rename_existing_source_notes(config)
    elif not dry_run:
        removed = prune_mirrored_article_source_notes(config)
        if removed:
            print(f"[start] removed {removed} mirrored article source notes", flush=True)
        cleanup_stats = cleanup_stale_entity_artifacts(config)
        legacy_stats = cleanup_legacy_wiki_dirs(config)
        merged_stats = {**cleanup_stats, **legacy_stats}
        if any(merged_stats.values()):
            print(f"[start] cleaned stale wiki artifacts: {merged_stats}", flush=True)
    print("[start] initializing AI providers", flush=True)
    entity_extractor = LLMEntityExtractor(config) if config.generate_entities else None
    text_writer = KnowledgeWikiWriter(config)
    compiler_build = compiler_build_id(config)

    manifest_path = config.state_dir / "manifest.json"
    registry_path = config.state_dir / "registry.json"
    group_ai_cache_path = config.state_dir / "group_ai_cache.json"
    print("[start] loading state files", flush=True)
    previous_manifest = load_json(manifest_path, default={})
    previous_registry = load_json(registry_path, default={})
    group_ai_cache = load_json(group_ai_cache_path, default={})
    if not isinstance(group_ai_cache, dict):
        group_ai_cache = {}
    if rebuild_registry:
        previous_registry = collect_existing_source_records(config)
        previous_manifest = {
            source_id: {
                "source_id": source_id,
                "source_kind": record.get("source_kind", ""),
                "source_path": record.get("source_path", ""),
                "title": record.get("title", ""),
                "date": record.get("date", ""),
                "content_hash": sha1_text(json.dumps(record, ensure_ascii=False, sort_keys=True)),
                "compiler_build": compiler_build,
            }
            for source_id, record in previous_registry.items()
        }
    if not previous_registry:
        previous_registry = collect_existing_source_records(config)
    else:
        previous_registry = migrate_registry_legacy_paths(previous_registry)
        previous_registry = migrate_registry_link_paths(previous_registry, config)
    print(f"[start] normalizing registry ({len(previous_registry)} records)", flush=True)
    registry_before_normalization = previous_registry
    if dry_run:
        print("[start] dry-run: skipping registry normalization writes", flush=True)
    else:
        previous_registry = normalize_existing_registry(previous_registry, config, compiler_build)
    registry_changed_by_normalization = previous_registry != registry_before_normalization
    prune_missing_note_records(previous_registry, previous_manifest, config)

    if only_taxonomy or only_render or taxonomy_review:
        active_records = [record for record in previous_registry.values() if record_note_exists(record, config)]
        if dry_run:
            return {
                "raw_entries": 0,
                "changed_entries": 0,
                "removed_entries": 0,
                "compiled_entries": 0,
                "source_records": len(active_records),
                "wiki_root": str(config.wiki_dir),
                "mode": "taxonomy_dry_run",
            }
        archive_legacy_queries(config)
        active_group_cache_keys = generate_aggregate_pages(
            active_records,
            config,
            text_writer,
            group_ai_cache=group_ai_cache,
            compiler_build=compiler_build,
            allow_group_ai=False,
        )
        prune_group_ai_cache(group_ai_cache, active_group_cache_keys)
        rewrite_source_note_links(config)
        write_json(manifest_path, previous_manifest)
        write_json(registry_path, {k: slim_record(v) for k, v in previous_registry.items()})
        write_json(group_ai_cache_path, group_ai_cache)
        return {
            "raw_entries": 0,
            "changed_entries": 0,
            "removed_entries": 0,
            "compiled_entries": 0,
            "source_records": len(active_records),
            "source_notes": count_markdown_files(config.sources_dir),
            "concept_pages": count_markdown_files(config.concepts_dir),
            "entity_pages": count_markdown_files(config.entities_dir),
            "theme_pages": count_markdown_files(config.themes_dir),
            "wiki_root": str(config.wiki_dir),
            "mode": "taxonomy",
        }

    print(f"[start] scanning raw sources only={only} date={date or ''}", flush=True)
    raw_entries = scan_raw(config, only=only, date=date)
    normalized_date_filter = normalize_date(date or "")
    if limit is not None:
        raw_entries = raw_entries[:limit]
    print(f"[start] scanned {len(raw_entries)} raw entries", flush=True)

    current_manifest = {entry.source_id: manifest_record(entry, compiler_build=compiler_build) for entry in raw_entries}
    current_ids = set(current_manifest)
    previous_ids = set(previous_manifest)

    changed: list[RawEntry] = []
    for entry in raw_entries:
        prev = previous_manifest.get(entry.source_id)
        registry_item = previous_registry.get(entry.source_id, {})
        note_path_value = str(registry_item.get("note_path", "") or "").strip()
        note_exists = bool(note_path_value) and (config.repo_root / note_path_value).exists()
        if (
            force
            or not prev
            or prev.get("content_hash") != entry.content_hash
            or not note_exists
        ):
            changed.append(entry)

    removed_ids = sorted(previous_ids - current_ids) if limit is None else []
    stale_group_ids: list[str] = []
    if limit is None:
        for sid in sorted(previous_registry.keys()):
            if "#group_" not in sid:
                continue
            base_source_id = sid.split("#group_")[0]
            if base_source_id not in current_ids:
                stale_group_ids.append(sid)
    if date:
        removed_ids = [
            source_id
            for source_id in removed_ids
            if source_bucket_for_manifest(previous_manifest.get(source_id, {})) == normalized_date_filter
        ]
    if only != "all":
        prefix = "article:" if only == "articles" else "post:"
        removed_ids = [item for item in removed_ids if item.startswith(prefix)]

    if dry_run:
        return {
            "raw_entries": len(raw_entries),
            "changed_entries": len(changed),
            "removed_entries": len(removed_ids),
            "wiki_root": str(config.wiki_dir),
            "mode": only,
            "date": date or "",
        }

    archive_legacy_queries(config)

    pending_affected_entities: set[str] = set()
    pending_affected_themes: set[str] = set()
    pending_affected_concepts: set[str] = set()
    pending_rebuild_all_entities = registry_changed_by_normalization
    pending_rebuild_all_themes = registry_changed_by_normalization
    pending_rebuild_all_concepts = registry_changed_by_normalization
    for removed_id in removed_ids:
        record = previous_registry.pop(removed_id, None)
        if record:
            pending_affected_entities.update(
                str(name).strip()
                for name in (record.get("entities", []) or [])
                if str(name).strip()
            )
            pending_affected_themes.update(
                str(name).strip()
                for name in (record.get("themes", []) or [])
                if str(name).strip()
            )
            pending_affected_concepts.update(
                str(name).strip()
                for name in (record.get("concepts", []) or [])
                if str(name).strip()
            )
            delete_wiki_source_note(record, config)
        previous_manifest.pop(removed_id, None)

    for stale_id in stale_group_ids:
        record = previous_registry.pop(stale_id, None)
        if record:
            pending_affected_entities.update(
                str(name).strip()
                for name in (record.get("entities", []) or [])
                if str(name).strip()
            )
            pending_affected_themes.update(
                str(name).strip()
                for name in (record.get("themes", []) or [])
                if str(name).strip()
            )
            pending_affected_concepts.update(
                str(name).strip()
                for name in (record.get("concepts", []) or [])
                if str(name).strip()
            )
            delete_wiki_source_note(record, config)

    def persist_state() -> None:
        write_json(manifest_path, previous_manifest)
        slimmed = {k: slim_record(v) for k, v in previous_registry.items()}
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        if not write_text_with_retry(registry_path, json.dumps(slimmed, ensure_ascii=False, separators=(",", ":"))):
            raise OSError(f"failed to write {registry_path}")
        write_json(group_ai_cache_path, group_ai_cache)

    def should_aggregate() -> bool:
        interval = max(1, int(aggregate_every or 1))
        return compiled_count == total_changed or compiled_count % interval == 0

    def aggregate_and_persist(label: str) -> None:
        nonlocal pending_affected_entities, pending_affected_themes, pending_affected_concepts, pending_rebuild_all_entities, pending_rebuild_all_themes, pending_rebuild_all_concepts
        with SOURCE_NOTE_WRITE_LOCK:
            active_records = [
                record
                for record in previous_registry.values()
                if record_note_exists(record, config)
            ]
            affected_entities = None if pending_rebuild_all_entities else set(pending_affected_entities)
            affected_themes = None if pending_rebuild_all_themes else set(pending_affected_themes)
            affected_concepts = None if pending_rebuild_all_concepts else set(pending_affected_concepts)
            affected_entities_label = "all" if affected_entities is None else str(len(affected_entities))
            affected_themes_label = "all" if affected_themes is None else str(len(affected_themes))
            affected_concepts_label = "all" if affected_concepts is None else str(len(affected_concepts))
            print(
                f"[aggregate] {label}: rebuilding wiki pages from {len(active_records)} source records"
                f" (affected_entities={affected_entities_label}, affected_themes={affected_themes_label}, affected_concepts={affected_concepts_label})",
                flush=True,
            )
            active_group_cache_keys = generate_aggregate_pages(
                active_records,
                config,
                text_writer,
                group_ai_cache=group_ai_cache,
                compiler_build=compiler_build,
                affected_entities=affected_entities,
                affected_themes=affected_themes,
                affected_concepts=affected_concepts,
            )
            prune_group_ai_cache(group_ai_cache, active_group_cache_keys)
            rewrite_source_note_links(config)
            persist_state()
            pending_affected_entities.clear()
            pending_affected_themes.clear()
            pending_affected_concepts.clear()
            pending_rebuild_all_entities = False
            pending_rebuild_all_themes = False
            pending_rebuild_all_concepts = False

    compiled_count = 0
    total_changed = len(changed)
    worker_count = max(1, int(workers or 1))
    if worker_count == 1 or total_changed <= 1:
        for index, entry in enumerate(changed, start=1):
            print(f"[compile] {index}/{total_changed} {entry.source_id}", flush=True)
            try:
                compiled_records = compile_entry(entry, config, entity_extractor, text_writer)
            except Exception as exc:
                raise RuntimeError(f"Failed compiling {entry.source_id} ({entry.title})") from exc

            old_group_ids = {sid for sid in previous_registry if sid == entry.source_id or sid.startswith(f"{entry.source_id}#group_")}
            for old_gid in old_group_ids:
                old_record = previous_registry.pop(old_gid, None)
                if old_record:
                    pending_affected_entities.update(
                        str(name).strip()
                        for name in (old_record.get("entities", []) or [])
                        if str(name).strip()
                    )
                    pending_affected_themes.update(
                        str(name).strip()
                        for name in (old_record.get("themes", []) or [])
                        if str(name).strip()
                    )
                    pending_affected_concepts.update(
                        str(name).strip()
                        for name in (old_record.get("concepts", []) or [])
                        if str(name).strip()
                    )
                    if old_gid not in {r["source_id"] for r in compiled_records}:
                        delete_wiki_source_note(old_record, config)

            for compiled_record in compiled_records:
                rid = compiled_record["source_id"]
                previous_registry[rid] = compiled_record
                pending_affected_entities.update(
                    str(name).strip()
                    for name in (compiled_record.get("entities", []) or [])
                    if str(name).strip()
                )
                pending_affected_themes.update(
                    str(name).strip()
                    for name in (compiled_record.get("themes", []) or [])
                    if str(name).strip()
                )
                pending_affected_concepts.update(
                    str(name).strip()
                    for name in (compiled_record.get("concepts", []) or [])
                    if str(name).strip()
                )

            previous_manifest[entry.source_id] = current_manifest[entry.source_id]
            compiled_count += 1
            persist_state()
            if should_aggregate():
                aggregate_and_persist(f"after {compiled_count}/{total_changed}")
    else:
        print(f"[compile] running with workers={worker_count}", flush=True)
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {}
            for index, entry in enumerate(changed, start=1):
                print(f"[compile:start] {index}/{total_changed} {entry.source_id}", flush=True)
                future = executor.submit(compile_entry, entry, config, entity_extractor, text_writer)
                futures[future] = (index, entry)

            for future in as_completed(futures):
                index, entry = futures[future]
                try:
                    compiled_records = future.result()
                except Exception as exc:
                    for pending in futures:
                        if pending is not future:
                            pending.cancel()
                    raise RuntimeError(f"Failed compiling {entry.source_id} ({entry.title})") from exc

                old_group_ids = {sid for sid in previous_registry if sid == entry.source_id or sid.startswith(f"{entry.source_id}#group_")}
                for old_gid in old_group_ids:
                    old_record = previous_registry.pop(old_gid, None)
                    if old_record:
                        pending_affected_entities.update(
                            str(name).strip()
                            for name in (old_record.get("entities", []) or [])
                            if str(name).strip()
                        )
                        pending_affected_themes.update(
                            str(name).strip()
                            for name in (old_record.get("themes", []) or [])
                            if str(name).strip()
                        )
                        pending_affected_concepts.update(
                            str(name).strip()
                            for name in (old_record.get("concepts", []) or [])
                            if str(name).strip()
                        )
                        if old_gid not in {r["source_id"] for r in compiled_records}:
                            delete_wiki_source_note(old_record, config)

                for compiled_record in compiled_records:
                    rid = compiled_record["source_id"]
                    previous_registry[rid] = compiled_record
                    pending_affected_entities.update(
                        str(name).strip()
                        for name in (compiled_record.get("entities", []) or [])
                        if str(name).strip()
                    )
                    pending_affected_themes.update(
                        str(name).strip()
                        for name in (compiled_record.get("themes", []) or [])
                        if str(name).strip()
                    )
                    pending_affected_concepts.update(
                        str(name).strip()
                        for name in (compiled_record.get("concepts", []) or [])
                        if str(name).strip()
                    )

                previous_manifest[entry.source_id] = current_manifest[entry.source_id]
                compiled_count += 1
                print(f"[compile:done] {compiled_count}/{total_changed} source_index={index} {entry.source_id}", flush=True)
                persist_state()
                if should_aggregate():
                    aggregate_and_persist(f"after {compiled_count}/{total_changed}")

    if compiled_count == 0:
        if removed_ids or registry_changed_by_normalization:
            aggregate_and_persist("final")
        else:
            print("[aggregate] final: skipped (no source changes)", flush=True)
            persist_state()

    return {
        "raw_entries": len(raw_entries),
        "changed_entries": len(changed),
        "removed_entries": len(removed_ids),
        "compiled_entries": compiled_count,
        "source_notes": count_markdown_files(config.sources_dir),
        "concept_pages": count_markdown_files(config.concepts_dir),
        "entity_pages": count_markdown_files(config.entities_dir),
        "theme_pages": count_markdown_files(config.themes_dir),
        "wiki_root": str(config.wiki_dir),
        "date": date or "",
    }


def ensure_dirs(config: AppConfig) -> None:
    required_dirs = [
        config.wiki_dir,
        config.state_dir,
        config.sources_dir,
        config.concepts_dir,
        config.themes_dir,
        config.index_dir,
        config.sources_dir / "articles",
        config.sources_dir / "posts",
        config.sources_dir / "index",
    ]
    if config.generate_entities:
        required_dirs.append(config.entities_dir)
    for path in required_dirs:
        path.mkdir(parents=True, exist_ok=True)


def archive_legacy_queries(config: AppConfig) -> None:
    if config.generate_queries:
        return
    legacy_dir = config.legacy_queries_dir
    if not legacy_dir.exists() or not legacy_dir.is_dir():
        return
    if not any(legacy_dir.iterdir()):
        legacy_dir.rmdir()
        return
    archive_base = config.wiki_dir / "_archive" / "pre-taxonomy-migration" / today_date()
    target = archive_base / "queries"
    if target.exists():
        target = archive_base / f"queries-{datetime.now().strftime('%H%M%S')}"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(legacy_dir), str(target))


def markdown_link_target(target: Path, source_dir: Path) -> str:
    rel = os.path.relpath(target, source_dir).replace(os.sep, "/")
    return format_markdown_path(rel)


def format_markdown_path(value: str) -> str:
    text = str(value or "")
    if any(char.isspace() or char in "()[]#%" for char in text):
        return f"<{text}>"
    return text


_DATE_DIRECTORY_RE = re.compile(r"\d{8}\Z")


def _is_date_directory(name: str) -> bool:
    """True when ``name`` is a pure YYYYMMDD date-bucket directory."""
    return _DATE_DIRECTORY_RE.match(name) is not None


def _iter_article_md_paths(article_dir: Path, date_filter: str) -> list[Path]:
    """Candidate ``.md`` paths under ``article_dir``.

    When ``date_filter`` is set, every other YYYYMMDD bucket directory is
    skipped so its files are never read + hashed only to be discarded. Files
    inside non-date directories (edge cases whose bucket is derived from
    front-matter date) are still yielded and filtered later by
    ``source_bucket_for_entry``, preserving existing behavior.
    """
    paths: list[Path] = []
    if not article_dir.exists():
        return paths
    if not date_filter:
        for path in article_dir.rglob("*.md"):
            if not path.name.startswith("."):
                paths.append(path)
        return paths
    for child in article_dir.iterdir():
        if child.is_dir():
            if _is_date_directory(child.name) and child.name != date_filter:
                continue
            for path in child.rglob("*.md"):
                if not path.name.startswith("."):
                    paths.append(path)
        elif child.is_file() and child.suffix == ".md" and not child.name.startswith("."):
            paths.append(child)
    return paths


def _iter_post_date_dirs(post_dir: Path, date_filter: str) -> list[Path]:
    """YYYYMMDD subdirectories of ``post_dir``. With ``date_filter`` set, other
    date buckets are skipped; non-date directories are kept for edge cases."""
    dirs: list[Path] = []
    if not post_dir.exists():
        return dirs
    for child in post_dir.iterdir():
        if not child.is_dir() or child.name.startswith("."):
            continue
        if date_filter and _is_date_directory(child.name) and child.name != date_filter:
            continue
        dirs.append(child)
    return dirs


def scan_raw(config: AppConfig, *, only: str, date: str | None = None) -> list[RawEntry]:
    entries: list[RawEntry] = []
    normalized_date_filter = normalize_date(date or "")
    if only in {"all", "articles"}:
        for path in sorted(_iter_article_md_paths(config.article_dir, normalized_date_filter)):
            entry = scan_article(path, config)
            if normalized_date_filter and source_bucket_for_entry(entry) != normalized_date_filter:
                continue
            entries.append(entry)
        entries = dedupe_article_entries(entries)
    if only in {"all", "posts"} and config.post_dir.exists():
        for directory in sorted(_iter_post_date_dirs(config.post_dir, normalized_date_filter)):
            json_path = directory / config.post_json_filename
            if json_path.exists():
                entry = scan_post_batch(json_path, config)
                if normalized_date_filter and source_bucket_for_entry(entry) != normalized_date_filter:
                    continue
                entries.append(entry)
    entries.sort(key=lambda item: (item.date, item.source_kind, item.source_path))
    return entries


def dedupe_article_entries(entries: list[RawEntry]) -> list[RawEntry]:
    if not entries:
        return entries

    canonical_groups: dict[str, list[RawEntry]] = defaultdict(list)
    unresolved: list[RawEntry] = []
    title_author_to_url_key: dict[tuple[str, str], str] = {}
    title_to_url_key: dict[str, str] = {}

    for entry in entries:
        if entry.source_kind != "article":
            unresolved.append(entry)
            continue

        url_key = normalized_canonical_url_key(entry.canonical_url)
        title_key = normalized_article_title_key(entry.title)
        author_key = normalized_article_author_key(entry.author)

        if url_key:
            canonical_groups[url_key].append(entry)
            if title_key:
                title_author_to_url_key[(title_key, author_key)] = url_key
                title_to_url_key[title_key] = url_key
            continue

        unresolved.append(entry)

    for entry in unresolved:
        if entry.source_kind != "article":
            continue
        title_key = normalized_article_title_key(entry.title)
        author_key = normalized_article_author_key(entry.author)
        fallback_key = title_author_to_url_key.get((title_key, author_key)) or title_to_url_key.get(title_key)
        if fallback_key:
            canonical_groups[fallback_key].append(entry)

    invalid_groups: dict[str, list[RawEntry]] = defaultdict(list)
    passthrough: list[RawEntry] = []
    for entry in unresolved:
        if entry.source_kind != "article":
            passthrough.append(entry)
            continue
        title_key = normalized_article_title_key(entry.title)
        if not title_key:
            passthrough.append(entry)
            continue
        if title_key in title_to_url_key:
            continue
        invalid_groups[title_key].append(entry)

    deduped: list[RawEntry] = list(passthrough)
    duplicate_count = 0

    for grouped_entries in canonical_groups.values():
        winner, duplicates = select_preferred_article_entry(grouped_entries)
        deduped.append(winner)
        duplicate_count += len(duplicates)

    for grouped_entries in invalid_groups.values():
        winner, duplicates = select_preferred_article_entry(grouped_entries)
        deduped.append(winner)
        duplicate_count += len(duplicates)

    if duplicate_count:
        print(f"[scan] deduped {duplicate_count} duplicate article entries", flush=True)

    deduped.sort(key=lambda item: (item.date, item.source_kind, item.source_path))
    return deduped


def select_preferred_article_entry(entries: list[RawEntry]) -> tuple[RawEntry, list[RawEntry]]:
    ranked = sorted(
        entries,
        key=lambda entry: (
            -article_entry_rank(entry)[0],
            -article_entry_rank(entry)[1],
            -article_entry_rank(entry)[2],
            entry.source_path,
        ),
    )
    winner = ranked[0]
    duplicates = ranked[1:]
    return winner, duplicates


def article_entry_rank(entry: RawEntry) -> tuple[int, int, int]:
    text = str((entry.payload or {}).get("text", "") or "")
    canonical_url = str(entry.canonical_url or "").strip()
    return (
        1 if canonical_url and not is_placeholder_canonical_url(canonical_url) else 0,
        len(text),
        1 if str(entry.author or "").strip() else 0,
    )


def normalized_canonical_url_key(url: str) -> str:
    value = str(url or "").strip()
    if not value or is_placeholder_canonical_url(value):
        return ""

    parsed = urlparse(value)
    if not parsed.scheme or not parsed.netloc:
        return ""

    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return f"{host}{path}"


def is_placeholder_canonical_url(url: str) -> bool:
    value = str(url or "").strip()
    if not value:
        return True

    parsed = urlparse(value)
    path = (parsed.path or "").rstrip("/").lower()
    if path in {"/404", "/error", "/not-found"}:
        return True
    return False


def normalized_article_title_key(title: str) -> str:
    return sanitize_title_filename(title).lower()


def normalized_article_author_key(author: str) -> str:
    return re.sub(r"\s+", " ", str(author or "").strip().lower())




def source_bucket_for_entry(entry: RawEntry) -> str:
    match = re.search(r"/(\d{8})(?:/|$)", entry.source_path)
    if match:
        return match.group(1)
    return date_bucket_for(entry.date)


def source_bucket_for_manifest(record: dict[str, Any]) -> str:
    match = re.search(r"/(\d{8})(?:/|$)", str(record.get("source_path", "") or ""))
    if match:
        return match.group(1)
    return date_bucket_for(str(record.get("date", "") or ""))


def scan_article(path: Path, config: AppConfig) -> RawEntry:
    text = path.read_text(encoding="utf-8", errors="ignore")
    title = extract_heading(text) or path.stem
    metadata = parse_article_metadata(text)
    rel_path = path.relative_to(config.repo_root).as_posix()
    return RawEntry(
        source_id=f"article:{rel_path}",
        source_kind="article",
        source_path=rel_path,
        title=title,
        date=normalize_date(metadata.get("发布时间") or metadata.get("保存时间") or path.parent.name),
        canonical_url=metadata.get("链接", ""),
        author=metadata.get("作者", ""),
        content_hash=sha1_text(text),
        payload={"text": text, "metadata": metadata},
    )


def scan_post_batch(path: Path, config: AppConfig) -> RawEntry:
    text = path.read_text(encoding="utf-8", errors="ignore")
    rel_path = path.relative_to(config.repo_root).as_posix()
    date = normalize_date(path.parent.name)
    title = f"X Timeline {date}" if date else path.parent.name
    return RawEntry(
        source_id=f"post:{rel_path}",
        source_kind="post_batch",
        source_path=rel_path,
        title=title,
        date=date,
        canonical_url="",
        author="",
        content_hash=sha1_text(text),
        payload={"text": text},
    )


def manifest_record(entry: RawEntry, *, compiler_build: str) -> dict[str, Any]:
    return {
        "source_kind": entry.source_kind,
        "source_path": entry.source_path,
        "title": entry.title,
        "date": entry.date,
        "canonical_url": entry.canonical_url,
        "author": entry.author,
        "content_hash": entry.content_hash,
        "compiler_build": compiler_build,
    }


def compile_entry(
    entry: RawEntry,
    config: AppConfig,
    entity_extractor: LLMEntityExtractor,
    text_writer: KnowledgeWikiWriter,
) -> list[dict[str, Any]]:
    if entry.source_kind == "article":
        return [compile_article_entry(entry, config, entity_extractor, text_writer)]
    return compile_post_entry(entry, config, entity_extractor, text_writer)


def compile_article_entry(
    entry: RawEntry,
    config: AppConfig,
    entity_extractor: LLMEntityExtractor,
    text_writer: KnowledgeWikiWriter,
) -> dict[str, Any]:
    text = str(entry.payload.get("text", ""))
    metadata = dict(entry.payload.get("metadata", {}))
    body = extract_article_body(text)
    clean_body = clean_text(body)
    paragraphs = split_paragraphs(clean_body)
    if config.generate_entities:
        entity_details, entity_provider = entity_extractor.extract_entities(
            source_kind="article",
            title=entry.title,
            date=entry.date,
            author=entry.author,
            canonical_url=entry.canonical_url,
            content=clean_body,
        )
        entity_names = [item["name"] for item in entity_details] or extract_entities_rules(f"{entry.title}\n{clean_body}")
        if not entity_details:
            entity_details = [
                {
                    "name": name,
                    "type": "rule_fallback",
                    "aliases": [],
                    "confidence": 0.0,
                    "salience": 2,
                    "evidence": "Matched by local rule fallback.",
                }
                for name in entity_names
            ]
    else:
        entity_details = []
        entity_provider = ""
        entity_names = []
    ai_source = text_writer.generate_source_copy(
        source_kind=entry.source_kind,
        title=entry.title,
        author=entry.author,
        canonical_url=entry.canonical_url,
        content=clean_body,
        available_concepts=ALL_CONCEPTS,
        available_themes=ALL_THEMES,
        entities=entity_names,
    )
    raw_concept_tags = list((ai_source or {}).get("concepts_tags") or [])
    concept_names = normalize_concept_list(raw_concept_tags or extract_concepts(f"{entry.title}\n{clean_body}"), aliases=get_concept_aliases(config))
    raw_theme_tags = list((ai_source or {}).get("themes_tags") or [])
    theme_candidates = extract_theme_candidates(raw_theme_tags)
    theme_names = normalize_theme_list(raw_theme_tags)
    if not theme_names:
        theme_names = normalize_theme_list(extract_themes(f"{entry.title}\n{clean_body}", concepts=concept_names))
    summary = list((ai_source or {}).get("summary") or summarize_paragraphs(paragraphs, max_items=4))
    key_signals = list((ai_source or {}).get("key_signals") or build_key_signals(paragraphs, max_items=4))
    open_questions = list((ai_source or {}).get("open_questions") or build_open_questions(entry.title, concept_names, entity_names))
    knowledge_fields = build_source_knowledge_fields(
        source_kind=entry.source_kind,
        title=entry.title,
        canonical_url=entry.canonical_url,
        concepts=concept_names,
        entities=entity_names,
        themes=theme_names,
        summary=summary,
        key_signals=key_signals,
        text=clean_body,
        config=config,
    )
    if (ai_source or {}).get("why_it_matters"):
        knowledge_fields["why_it_matters"] = str(ai_source["why_it_matters"]).strip()
    if (ai_source or {}).get("content_type"):
        knowledge_fields["content_type"] = ai_source["content_type"]
    if (ai_source or {}).get("actionability"):
        knowledge_fields["actionability"] = ai_source["actionability"]
    if (ai_source or {}).get("next_action"):
        knowledge_fields["next_action"] = ai_source["next_action"]

    with SOURCE_NOTE_WRITE_LOCK:
        note_path_rel = entry.source_path
        if config.generate_source_notes:
            note_path = allocate_source_note_path(
                base_dir=config.sources_dir / "articles" / date_bucket_for(entry.date),
                title=entry.title,
                source_id=entry.source_id,
            )

            frontmatter = {
                "type": "source",
                "source_kind": entry.source_kind,
                "source_id": entry.source_id,
                "source_path": entry.source_path,
                "title": entry.title,
                "display_title": str((ai_source or {}).get("display_title") or "").strip(),
                "date": entry.date,
                "canonical_url": entry.canonical_url,
                "author": entry.author,
                **knowledge_fields,
                "concepts": concept_names,
                "entities": entity_names,
                "entity_details": entity_details,
                "entity_provider": entity_provider,
                "themes": theme_names,
                "theme_candidates": theme_candidates,
                "compiled_at": now_iso(),
            }
            body_text = render_source_note_body(
                title=entry.title,
                display_title=str((ai_source or {}).get("display_title") or "").strip(),
                summary=summary,
                key_signals=key_signals,
                knowledge_fields=knowledge_fields,
                note_path=note_path,
                concept_names=concept_names,
                entity_names=entity_names,
                theme_names=theme_names,
                open_questions=open_questions,
                metadata_lines=[f"- **{key}**: {value}" for key, value in metadata.items() if value],
                config=config,
            )
            write_markdown(note_path, frontmatter, body_text)
            note_path_rel = note_path.relative_to(config.repo_root).as_posix()

    return {
        "source_id": entry.source_id,
        "source_kind": entry.source_kind,
        "source_path": entry.source_path,
        "note_path": note_path_rel,
        "title": entry.title,
        "display_title": str((ai_source or {}).get("display_title") or "").strip(),
        "date": entry.date,
        "canonical_url": entry.canonical_url,
        "author": entry.author,
        **knowledge_fields,
        "concepts": concept_names,
        "entities": entity_names,
        "entity_details": entity_details,
        "entity_provider": entity_provider,
        "themes": theme_names,
        "theme_candidates": theme_candidates,
        "summary": summary,
        "key_signals": key_signals,
        "compiled_at": now_iso(),
    }


def compile_post_entry(
    entry: RawEntry,
    config: AppConfig,
    entity_extractor: LLMEntityExtractor,
    text_writer: KnowledgeWikiWriter,
) -> list[dict[str, Any]]:
    tweets = load_post_items(entry.payload["text"])
    clusters = cluster_post_items(
        tweets,
        min_cluster=config.post_group_min_size,
        max_groups=config.post_max_groups,
    )
    author_counter = Counter(tweet["author"] for tweet in tweets if tweet["author"])
    top_authors = [name for name, _count in author_counter.most_common(6)]
    domains = extract_domains(tweets)

    records: list[dict[str, Any]] = []
    for group_index, (label, group_tweets) in enumerate(clusters):
        group_source_id = f"{entry.source_id}#group_{group_index}"
        group_title = f"X Timeline {entry.date} - {label}" if label not in ("misc", "timeline") else f"X Timeline {entry.date} - Misc"
        record = _compile_post_group(
            group_source_id=group_source_id,
            group_title=group_title,
            group_tweets=group_tweets,
            entry=entry,
            config=config,
            entity_extractor=entity_extractor,
            text_writer=text_writer,
            top_authors=top_authors,
            domains=domains,
        )
        records.append(record)
    return records


def _compile_post_group(
    *,
    group_source_id: str,
    group_title: str,
    group_tweets: list[dict[str, Any]],
    entry: RawEntry,
    config: AppConfig,
    entity_extractor: LLMEntityExtractor,
    text_writer: KnowledgeWikiWriter,
    top_authors: list[str],
    domains: list[str],
) -> dict[str, Any]:
    post_count = len(group_tweets)
    top_tweets = sorted(
        group_tweets,
        key=lambda item: (item["score"], item["reposts"], item["likes"]),
        reverse=True,
    )[:config.post_tweets_per_group]
    combined_text = "\n".join(tweet["content"] for tweet in top_tweets if tweet["content"])
    if config.generate_entities:
        entity_details, entity_provider = entity_extractor.extract_entities(
            source_kind="post_batch",
            title=group_title,
            date=entry.date,
            author="",
            canonical_url="",
            content=combined_text,
            top_authors=top_authors[:10],
            top_domains=domains[:10],
        )
        entity_names = [item["name"] for item in entity_details]
        if not entity_names:
            entity_names = extract_entities_rules(combined_text)
            entity_names = dedupe_preserve(entity_names)[:8]
            entity_details = [
                {
                    "name": name,
                    "type": "rule_fallback",
                    "aliases": [],
                    "confidence": 0.0,
                    "salience": 2,
                    "evidence": "Matched by local rule fallback.",
                }
                for name in entity_names
            ]
    else:
        entity_details = []
        entity_provider = ""
        entity_names = []
    ai_source = text_writer.generate_source_copy(
        source_kind="post_batch",
        title=group_title,
        author="",
        canonical_url="",
        content=combined_text,
        available_concepts=ALL_CONCEPTS,
        available_themes=ALL_THEMES,
        entities=entity_names,
    )
    raw_concept_tags = list((ai_source or {}).get("concepts_tags") or [])
    concept_names = normalize_concept_list(raw_concept_tags or extract_concepts(combined_text), aliases=get_concept_aliases(config))
    raw_theme_tags = list((ai_source or {}).get("themes_tags") or [])
    theme_candidates = extract_theme_candidates(raw_theme_tags)
    theme_names = normalize_theme_list(raw_theme_tags)
    if not theme_names:
        theme_names = normalize_theme_list(extract_themes(combined_text, concepts=concept_names))
    if "Social Signals" not in theme_names:
        theme_names.append("Social Signals")
    summary = list((ai_source or {}).get("summary") or [
        f"X 时间线话题分组：{group_title}，共 {post_count} 条帖子。",
        f"活跃作者最频繁的是 {cn_join(top_authors[:3]) or '未识别作者'}。",
    ])
    key_signals = list((ai_source or {}).get("key_signals") or [])
    if not key_signals:
        for tweet in top_tweets:
            snippet = truncate_line(tweet["content"], 180)
            metrics = []
            if tweet["likes"]:
                metrics.append(f"{tweet['likes']} likes")
            if tweet["reposts"]:
                metrics.append(f"{tweet['reposts']} reposts")
            if tweet["quotes"]:
                metrics.append(f"{tweet['quotes']} quotes")
            metric_suffix = f" ({', '.join(metrics)})" if metrics else ""
            key_signals.append(f"@{tweet['screen_name']}: {snippet}{metric_suffix}")
    knowledge_fields = build_source_knowledge_fields(
        source_kind="post_batch",
        title=group_title,
        canonical_url="",
        concepts=concept_names,
        entities=entity_names,
        themes=theme_names,
        summary=summary,
        key_signals=key_signals,
        text=combined_text,
        config=config,
    )
    if (ai_source or {}).get("why_it_matters"):
        knowledge_fields["why_it_matters"] = str(ai_source["why_it_matters"]).strip()
    if (ai_source or {}).get("content_type"):
        knowledge_fields["content_type"] = ai_source["content_type"]
    if (ai_source or {}).get("actionability"):
        knowledge_fields["actionability"] = ai_source["actionability"]
    if (ai_source or {}).get("next_action"):
        knowledge_fields["next_action"] = ai_source["next_action"]

    with SOURCE_NOTE_WRITE_LOCK:
        note_path_rel = entry.source_path
        if config.generate_post_source_notes:
            note_path = allocate_source_note_path(
                base_dir=config.sources_dir / "posts",
                title=group_title,
                source_id=group_source_id,
            )

            frontmatter = {
                "type": "source",
                "source_kind": "post_batch",
                "source_id": group_source_id,
                "source_path": entry.source_path,
                "title": group_title,
                "display_title": str((ai_source or {}).get("display_title") or "").strip(),
                "date": entry.date,
                "canonical_url": "",
                "author": "",
                **knowledge_fields,
                "concepts": concept_names,
                "entities": entity_names,
                "entity_details": entity_details,
                "entity_provider": entity_provider,
                "themes": theme_names,
                "theme_candidates": theme_candidates,
                "compiled_at": now_iso(),
            }
            body_text = render_source_note_body(
                title=group_title,
                display_title=str((ai_source or {}).get("display_title") or "").strip(),
                summary=summary,
                key_signals=key_signals,
                knowledge_fields=knowledge_fields,
                note_path=note_path,
                concept_names=concept_names,
                entity_names=entity_names,
                theme_names=theme_names,
                open_questions=list((ai_source or {}).get("open_questions") or build_open_questions(group_title, concept_names, entity_names)),
                metadata_lines=[
                    f"- **Post Count**: {post_count}",
                    f"- **Source File**: {entry.source_path}",
                    f"- **Top Domains**: {comma_list(domains[:10]) or 'None'}",
                ],
                extra_sections=[
                    "## Top Authors",
                    "",
                    *[f"- {name}: {count} posts" for name, count in Counter(t["author"] for t in group_tweets if t["author"]).most_common(10)],
                ],
                config=config,
            )
            write_markdown(note_path, frontmatter, body_text)
            note_path_rel = note_path.relative_to(config.repo_root).as_posix()

    return {
        "source_id": group_source_id,
        "source_kind": "post_batch",
        "source_path": entry.source_path,
        "note_path": note_path_rel,
        "title": group_title,
        "display_title": str((ai_source or {}).get("display_title") or "").strip(),
        "date": entry.date,
        "canonical_url": "",
        "author": "",
        **knowledge_fields,
        "concepts": concept_names,
        "entities": entity_names,
        "entity_details": entity_details,
        "entity_provider": entity_provider,
        "themes": theme_names,
        "theme_candidates": theme_candidates,
        "summary": summary,
        "key_signals": key_signals,
        "compiled_at": now_iso(),
    }


def generate_aggregate_pages(
    records: list[dict[str, Any]],
    config: AppConfig,
    text_writer: KnowledgeWikiWriter,
    *,
    group_ai_cache: dict[str, Any],
    compiler_build: str,
    affected_entities: set[str] | None = None,
    affected_themes: set[str] | None = None,
    affected_concepts: set[str] | None = None,
    allow_group_ai: bool = True,
) -> set[str]:
    print("[aggregate] building concept/entity/theme indexes", flush=True)
    concept_index = defaultdict(list)
    entity_index = defaultdict(list)
    theme_index = defaultdict(list)

    taxonomy_decisions = load_taxonomy_decisions(config)
    # Manually promoted themes are not in the seeded theme list, so their tags
    # live in each record's theme_candidates instead of themes. Map candidate
    # name keys back to the canonical promoted name so they join the index.
    promoted_theme_map = {
        taxonomy_name_key(format_taxonomy_name(str(item or ""))): format_taxonomy_name(str(item or ""))
        for item in list(taxonomy_decisions.get("promote_theme", []) or [])
        if format_taxonomy_name(str(item or ""))
    }
    for record in records:
        for name in record.get("concepts", []):
            concept_index[name].append(record)
        for name in record.get("entities", []):
            entity_index[name].append(record)
        record_theme_keys = set()
        for name in record.get("themes", []):
            theme_index[name].append(record)
            record_theme_keys.add(taxonomy_name_key(name))
        if promoted_theme_map:
            for name in record.get("theme_candidates", []):
                key = taxonomy_name_key(name)
                canonical = promoted_theme_map.get(key)
                if canonical and key not in record_theme_keys:
                    theme_index[canonical].append(record)
                    record_theme_keys.add(key)
    concept_lifecycle = build_concept_lifecycle(concept_index, config, taxonomy_decisions, theme_index)
    entity_lifecycle = build_entity_lifecycle(entity_index, config, taxonomy_decisions) if config.generate_entities else {}
    theme_lifecycle = build_theme_lifecycle(theme_index, config, taxonomy_decisions)
    active_concept_index = filter_concept_index_for_published_pages(concept_index, concept_lifecycle)
    active_entity_index = (
        filter_entity_index_for_published_pages(entity_index, entity_lifecycle)
        if config.generate_entities
        else {}
    )
    active_theme_index = filter_theme_index_for_pages(theme_index, theme_lifecycle)
    active_group_cache_keys = eligible_group_cache_keys("concept", active_concept_index, config.concept_min_sources) | eligible_group_cache_keys(
        "theme", active_theme_index, config.theme_min_sources
    )
    if config.generate_entities:
        active_group_cache_keys |= eligible_group_cache_keys("entity", active_entity_index, config.entity_min_sources)
    write_concept_lifecycle_state(concept_lifecycle, config)
    if config.generate_entities:
        write_entity_lifecycle_state(entity_lifecycle, config)
    write_group_pages(
        group_type="concept",
        index=active_concept_index,
        output_dir=config.concepts_dir,
        min_sources=config.concept_published_min_sources,
        config=config,
        text_writer=text_writer,
        group_ai_cache=group_ai_cache,
        compiler_build=compiler_build,
        only_names=affected_concepts,
        allow_ai=allow_group_ai and not config.simple_group_pages,
        lifecycle=concept_lifecycle,
    )
    if config.generate_entities:
        write_group_pages(
            group_type="entity",
            index=active_entity_index,
            output_dir=config.entities_dir,
            min_sources=config.entity_min_sources,
            config=config,
            text_writer=text_writer,
            group_ai_cache=group_ai_cache,
            compiler_build=compiler_build,
            only_names=affected_entities,
            allow_ai=allow_group_ai and not config.simple_group_pages,
            lifecycle=entity_lifecycle,
        )
    elif affected_entities is None:
        for old_file in config.entities_dir.glob("*.md"):
            old_file.unlink()
    write_group_pages(
        group_type="theme",
        index=active_theme_index,
        output_dir=config.themes_dir,
        min_sources=config.theme_min_sources,
        config=config,
        text_writer=text_writer,
        group_ai_cache=group_ai_cache,
        compiler_build=compiler_build,
        only_names=affected_themes,
        allow_ai=allow_group_ai and not config.simple_group_pages,
        lifecycle=theme_lifecycle,
    )
    if config.generate_queries:
        write_queries(records, active_concept_index, active_entity_index, active_theme_index, config)
    write_relation_edges_state(active_concept_index, active_entity_index, active_theme_index, config)
    write_source_state(records, config)
    write_theme_lifecycle_state(theme_lifecycle, config)
    write_taxonomy_registry_state(
        concept_lifecycle=concept_lifecycle,
        entity_lifecycle=entity_lifecycle,
        theme_lifecycle=theme_lifecycle,
        config=config,
    )
    write_indexes(
        records,
        active_concept_index,
        active_entity_index,
        active_theme_index,
        config,
        text_writer,
        allow_ai=allow_group_ai and not config.simple_group_pages,
    )
    write_lint_report(records, concept_index, entity_index, theme_index, config)
    write_taxonomy_candidate_report(records, concept_index, entity_index, theme_index, concept_lifecycle, entity_lifecycle, config)
    return active_group_cache_keys


def node_id(group_type: str, name: str) -> str:
    return f"{group_type}:{taxonomy_name_key(name).replace(' ', '-')}"


def write_relation_edges_state(
    concept_index: dict[str, list[dict[str, Any]]],
    entity_index: dict[str, list[dict[str, Any]]],
    theme_index: dict[str, list[dict[str, Any]]],
    config: AppConfig,
) -> None:
    edges: dict[tuple[str, str, str], dict[str, Any]] = {}

    def add_edge(source_type: str, source_name: str, target_type: str, target_name: str, relation: str, item: dict[str, Any]) -> None:
        source = node_id(source_type, source_name)
        target = node_id(target_type, target_name)
        if source == target:
            return
        key = (source, target, relation)
        payload = edges.setdefault(
            key,
            {
                "source_id": source,
                "source_name": source_name,
                "target_id": target,
                "target_name": target_name,
                "relation": relation,
                "weight": 0,
                "mentions": 0,
                "last_seen": "",
            },
        )
        payload["weight"] = max(int(payload.get("weight", 0) or 0), int(item.get("score", 0) or 0))
        payload["mentions"] = max(int(payload.get("mentions", 0) or 0), int(item.get("mentions", 0) or 0))

    for name, records in concept_index.items():
        ordered = sort_records_for_group("concept", name, dedupe_records(records))
        for item in related_term_items(name, ordered, field="concepts", group_type="concept")[: config.concept_related_limit]:
            add_edge("concept", name, "concept", str(item.get("name", "")), "related_to", item)
        if config.generate_entities:
            for item in related_term_items(name, ordered, field="entities", group_type="concept")[: config.entity_related_limit]:
                add_edge("concept", name, "entity", str(item.get("name", "")), "represented_by_entity", item)
        for item in related_term_items(name, ordered, field="themes", group_type="concept")[: config.theme_related_limit]:
            add_edge("concept", name, "theme", str(item.get("name", "")), "has_parent_theme", item)

    if config.generate_entities:
        for name, records in entity_index.items():
            ordered = sort_records_for_group("entity", name, dedupe_records(records))
            for item in related_term_items(name, ordered, field="concepts", group_type="entity")[: config.concept_related_limit]:
                add_edge("entity", name, "concept", str(item.get("name", "")), "related_to", item)
            for item in related_term_items(name, ordered, field="themes", group_type="entity")[: config.theme_related_limit]:
                add_edge("entity", name, "theme", str(item.get("name", "")), "has_parent_theme", item)

    for name, records in theme_index.items():
        ordered = sort_records_for_group("theme", name, dedupe_records(records))
        for item in related_term_items(name, ordered, field="concepts", group_type="theme")[: config.theme_related_limit]:
            add_edge("theme", name, "concept", str(item.get("name", "")), "has_subconcept", item)
        if config.generate_entities:
            for item in related_term_items(name, ordered, field="entities", group_type="theme")[: config.entity_related_limit]:
                add_edge("theme", name, "entity", str(item.get("name", "")), "represented_by_entity", item)

    payload = {
        "generated_at": now_iso(),
        "edges": sorted(edges.values(), key=lambda item: (-int(item.get("mentions", 0) or 0), item["source_id"], item["target_id"])),
    }
    write_json(config.state_dir / "relation_edges.json", payload)


def source_quality_for(record: dict[str, Any]) -> dict[str, Any]:
    kind = str(record.get("source_kind", "") or "").strip()
    content_type = str(record.get("content_type", "") or "").strip()
    ai_scope = "core_ai_source" if record_has_ai_signal(record) else "background_source"
    if kind in {"article"} and content_type in {"fact", "thesis", "tactic"}:
        role = "analysis_source"
        quality = "high_signal" if ai_scope == "core_ai_source" else "normal"
        weight = 1.0
    elif kind in {"post_batch", "post"}:
        role = "signal_source"
        quality = "normal" if ai_scope == "core_ai_source" else "low_signal"
        weight = 0.5
    else:
        role = "reference_source"
        quality = "normal"
        weight = 0.6
    return {
        "source_role": role,
        "source_scope": ai_scope,
        "quality": quality,
        "source_weight": weight if ai_scope != "background_source" else min(weight, 0.2),
    }


def source_duplicate_key(record: dict[str, Any]) -> str:
    canonical = normalized_canonical_url_key(str(record.get("canonical_url", "") or ""))
    if canonical and not is_placeholder_canonical_url(canonical):
        return f"url:{canonical}"
    title = normalized_article_title_key(str(record.get("title", "") or ""))
    date_value = normalize_iso_date(str(record.get("date", "") or ""))
    return f"title:{title}:{date_value}" if title else str(record.get("source_id", ""))


def write_source_state(records: list[dict[str, Any]], config: AppConfig) -> None:
    registry: dict[str, dict[str, Any]] = {}
    quality: dict[str, dict[str, Any]] = {}
    duplicate_groups: dict[str, list[str]] = defaultdict(list)
    for record in records:
        source_id = str(record.get("source_id", "") or "").strip()
        if not source_id:
            continue
        duplicate_key = source_duplicate_key(record)
        duplicate_groups[duplicate_key].append(source_id)
        quality_payload = source_quality_for(record)
        source_payload = {
            "source_id": source_id,
            "source_kind": record.get("source_kind", ""),
            "title": record.get("title", ""),
            "display_title": record.get("display_title", ""),
            "note_path": record.get("note_path", ""),
            "canonical_url": record.get("canonical_url", ""),
            "date": record.get("date", ""),
            "compiled_at": record.get("compiled_at", ""),
            "concepts": record.get("concepts", []),
            "themes": record.get("themes", []),
            "canonical_group_id": duplicate_key,
            **quality_payload,
        }
        if config.generate_entities:
            source_payload["entities"] = record.get("entities", [])
        registry[source_id] = source_payload
        quality[source_id] = {
            "canonical_group_id": duplicate_key,
            **quality_payload,
        }

    duplicates = {
        key: {
            "canonical_group_id": key,
            "representative": source_ids[0],
            "source_ids": source_ids,
            "duplicate_count": len(source_ids),
        }
        for key, source_ids in duplicate_groups.items()
        if len(source_ids) > 1
    }
    write_json(config.state_dir / "source_registry.json", {"generated_at": now_iso(), "sources": registry})
    write_json(config.state_dir / "source_quality.json", {"generated_at": now_iso(), "sources": quality})
    write_json(config.state_dir / "source_duplicates.json", {"generated_at": now_iso(), "groups": duplicates})
    review_dir = config.state_dir / "review"
    review_dir.mkdir(parents=True, exist_ok=True)
    lines = ["# Source Duplicates", ""]
    if duplicates:
        for group_id, payload in sorted(duplicates.items(), key=lambda item: (-int(item[1]["duplicate_count"]), item[0]))[:200]:
            lines.extend(
                [
                    f"## {group_id}",
                    "",
                    f"- Representative: `{payload['representative']}`",
                    f"- Count: {payload['duplicate_count']}",
                    "",
                    *[f"- `{source_id}`" for source_id in payload["source_ids"]],
                    "",
                ]
            )
    else:
        lines.append("- 暂无重复 source group。")
    write_plain_markdown(review_dir / "source_duplicates.md", "\n".join(lines))


def build_theme_lifecycle(
    theme_index: dict[str, list[dict[str, Any]]],
    config: AppConfig,
    taxonomy_decisions: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    lifecycle: dict[str, dict[str, Any]] = {}
    for name, records in theme_index.items():
        deduped = dedupe_records(records)
        count = len(deduped)
        date_count = len(record_dates(deduped))
        decision, decision_target = taxonomy_decision_for(name, "theme", taxonomy_decisions)
        if decision == "reject":
            layer = "rejected_theme"
            status = CONCEPT_STATUS_REJECTED
            reason = "manual_reject"
        elif decision == "merge":
            layer = "merged_theme"
            status = CONCEPT_STATUS_MERGED
            reason = "manual_merge"
        elif decision == "promote":
            layer = "stable_theme"
            status = CONCEPT_STATUS_PUBLISHED
            reason = "manual_promote"
        elif name in GROUP_DISPLAY_NAME_MAP.get("theme", {}):
            layer = "stable_theme"
            status = CONCEPT_STATUS_PUBLISHED
            reason = "seeded_stable_theme"
        elif count >= config.theme_published_min_sources and date_count >= config.theme_published_min_dates:
            layer = "stable_theme"
            status = CONCEPT_STATUS_PUBLISHED
            reason = "repeated_theme_promoted"
        elif count >= config.theme_min_sources and date_count >= config.theme_emerging_min_dates:
            layer = "emerging_theme"
            status = CONCEPT_STATUS_EMERGING
            reason = "repeated_theme_candidate"
        else:
            layer = "candidate_theme"
            status = CONCEPT_STATUS_CANDIDATE
            reason = "needs_more_evidence"
        lifecycle[name] = {
            "status": status,
            "layer": layer,
            "source_count": count,
            "date_count": date_count,
            "dates": sorted(record_dates(deduped), reverse=True)[:8],
            "reason": reason,
        }
        if decision_target:
            lifecycle[name]["canonical"] = decision_target
    return dict(sorted(lifecycle.items()))


def filter_theme_index_for_pages(
    theme_index: dict[str, list[dict[str, Any]]],
    lifecycle: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    return {
        name: records
        for name, records in theme_index.items()
        if str(lifecycle.get(name, {}).get("status", CONCEPT_STATUS_CANDIDATE)) in THEME_PAGE_STATUSES
    }


def write_theme_lifecycle_state(lifecycle: dict[str, dict[str, Any]], config: AppConfig) -> None:
    payload = {
        "generated_at": now_iso(),
        "statuses": lifecycle,
        "policy": {
            "published": "Stable seeded themes or manually promoted themes that generate pages.",
            "emerging": "Repeated themes that generate pages but still need review.",
            "candidate": "Low-evidence themes kept for review, not rendered as pages.",
            "merged": "Themes merged into a canonical theme.",
            "rejected": "Out-of-scope or manually rejected themes.",
        },
    }
    write_json(config.state_dir / "theme_lifecycle.json", payload)


def write_taxonomy_registry_state(
    *,
    concept_lifecycle: dict[str, dict[str, Any]],
    entity_lifecycle: dict[str, dict[str, Any]],
    theme_lifecycle: dict[str, dict[str, Any]],
    config: AppConfig,
) -> None:
    nodes: dict[str, dict[str, Any]] = {}
    for name, payload in concept_lifecycle.items():
        nodes[node_id("concept", name)] = {
            "id": node_id("concept", name),
            "type": "concept",
            "canonical_name": name,
            **payload,
        }
    for name, payload in entity_lifecycle.items():
        nodes[node_id("entity", name)] = {
            "id": node_id("entity", name),
            "type": "entity",
            "canonical_name": name,
            **payload,
        }
    for name, payload in theme_lifecycle.items():
        nodes[node_id("theme", name)] = {
            "id": node_id("theme", name),
            "type": "theme",
            "canonical_name": name,
            **payload,
        }
    write_json(config.state_dir / "taxonomy_registry.json", {"generated_at": now_iso(), "nodes": nodes})


def load_taxonomy_decisions(config: AppConfig) -> dict[str, Any]:
    path = config.state_dir / "taxonomy_decisions.yaml"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(
                [
                    "# Manual taxonomy decisions.",
                    "# Use keys like concept:context-engineering, entity:openai, theme:ai-safety.",
                    "accept: []",
                    "merge: {}",
                    "reject: []",
                    "promote_theme: []",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return {"accept": [], "merge": {}, "reject": [], "promote_theme": []}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {"accept": [], "merge": {}, "reject": [], "promote_theme": []}
    return data if isinstance(data, dict) else {}


def decision_key(group_type: str, name: str) -> str:
    return f"{group_type}:{taxonomy_name_key(name).replace(' ', '-')}"


def decision_matches(value: Any, group_type: str, name: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    candidates = {
        text,
        taxonomy_name_key(text),
        text.casefold(),
    }
    target_keys = {
        name,
        taxonomy_name_key(name),
        decision_key(group_type, name),
        group_slug(name, group_type),
    }
    return bool({item.casefold() for item in candidates} & {item.casefold() for item in target_keys})


def taxonomy_decision_for(name: str, group_type: str, decisions: dict[str, Any]) -> tuple[str, str]:
    for item in list(decisions.get("reject", []) or []):
        if decision_matches(item, group_type, name):
            return "reject", ""
    merge_map = decisions.get("merge", {}) or {}
    if isinstance(merge_map, dict):
        for source, target in merge_map.items():
            if decision_matches(source, group_type, name):
                return "merge", str(target or "").strip()
    promoted = list(decisions.get("accept", []) or []) + list(decisions.get("promote", []) or [])
    if group_type == "theme":
        promoted += list(decisions.get("promote_theme", []) or [])
    for item in promoted:
        if decision_matches(item, group_type, name):
            return "promote", ""
    return "", ""


def existing_page_name_keys(directory: Path) -> set[str]:
    return {taxonomy_name_key(path.stem) for path in directory.glob("*.md") if path.is_file()}


def build_concept_lifecycle(
    concept_index: dict[str, list[dict[str, Any]]],
    config: AppConfig,
    taxonomy_decisions: dict[str, Any],
    theme_index: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, dict[str, Any]]:
    existing_published = existing_page_name_keys(config.concepts_dir)
    lifecycle: dict[str, dict[str, Any]] = {}
    theme_index = theme_index or {}
    for name, records in concept_index.items():
        deduped = dedupe_records(records)
        count = len(deduped)
        dates = sorted(record_dates(deduped), reverse=True)
        date_count = len(dates)
        ai_related = is_ai_related_concept(name, deduped)
        decision, decision_target = taxonomy_decision_for(name, "concept", taxonomy_decisions)
        if concept_blocked_by_theme(name, theme_index, config):
            status = CONCEPT_STATUS_REJECTED
            reason = "served_as_theme"
        elif decision == "reject":
            status = CONCEPT_STATUS_REJECTED
            reason = "manual_reject"
        elif decision == "merge":
            status = CONCEPT_STATUS_MERGED
            reason = "manual_merge"
        elif decision == "promote":
            status = CONCEPT_STATUS_PUBLISHED
            reason = "manual_promote"
        elif name in APPROVED_CONCEPTS and not concept_blocked_by_theme(name, theme_index, config):
            status = CONCEPT_STATUS_PUBLISHED
            reason = "seeded_published_concept"
        elif taxonomy_name_key(name) in existing_published and ai_related and not concept_blocked_by_theme(name, theme_index, config):
            status = CONCEPT_STATUS_PUBLISHED
            reason = "existing_page_bootstrap"
        elif (
            ai_related
            and count >= config.concept_published_min_sources
            and date_count >= config.concept_published_min_dates
        ):
            status = CONCEPT_STATUS_PUBLISHED
            reason = "ai_related_repeated_promoted"
        elif ai_related and count >= config.concept_emerging_min_sources and date_count >= config.concept_emerging_min_dates:
            status = CONCEPT_STATUS_EMERGING
            reason = "ai_related_repeated_needs_review"
        else:
            status = CONCEPT_STATUS_CANDIDATE
            reason = "needs_more_evidence"
        lifecycle[name] = {
            "status": status,
            "layer": f"{status}_concept" if status in {CONCEPT_STATUS_PUBLISHED, CONCEPT_STATUS_EMERGING, CONCEPT_STATUS_CANDIDATE} else status,
            "source_count": count,
            "date_count": len(dates),
            "dates": dates[:8],
            "ai_related": ai_related,
            "reason": reason,
        }
        if decision_target:
            lifecycle[name]["canonical"] = decision_target

    for alias_key, canonical in sorted(CONCEPT_CANONICAL_MAP.items()):
        alias = format_taxonomy_name(alias_key)
        canonical_name = normalize_concept_name(canonical) or canonical
        if taxonomy_name_key(alias) == taxonomy_name_key(canonical_name):
            continue
        lifecycle.setdefault(
            alias,
            {
                "status": CONCEPT_STATUS_DEPRECATED,
                "layer": CONCEPT_STATUS_DEPRECATED,
                "canonical": canonical_name,
                "source_count": 0,
                "date_count": 0,
                "dates": [],
                "ai_related": is_ai_related_concept(canonical_name, []),
                "reason": "canonical_alias",
            },
        )
    return dict(sorted(lifecycle.items()))


def filter_concept_index_for_published_pages(
    concept_index: dict[str, list[dict[str, Any]]],
    lifecycle: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    return {
        name: records
        for name, records in concept_index.items()
        if str(lifecycle.get(name, {}).get("status", CONCEPT_STATUS_CANDIDATE)) in CONCEPT_PAGE_STATUSES
    }


def build_entity_lifecycle(
    entity_index: dict[str, list[dict[str, Any]]],
    config: AppConfig,
    taxonomy_decisions: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    existing_published = existing_page_name_keys(config.entities_dir)
    existing_concepts = existing_page_name_keys(config.concepts_dir)
    lifecycle: dict[str, dict[str, Any]] = {}
    for name, records in entity_index.items():
        deduped = dedupe_records(records)
        count = len(deduped)
        date_count = len(record_dates(deduped))
        ai_related = is_ai_related_entity(name, deduped)
        entity_type = common_entity_type(name, deduped)
        publishable_entity = is_publishable_entity_name(name, entity_type, deduped)
        strong_ai_entity = is_strong_ai_entity_name(name, entity_type, deduped)
        decision, decision_target = taxonomy_decision_for(name, "entity", taxonomy_decisions)
        if taxonomy_name_key(name) in existing_concepts:
            status = CONCEPT_STATUS_REJECTED
            reason = "concept_page_duplicate"
        elif is_concept_like_entity(name):
            status = CONCEPT_STATUS_REJECTED
            reason = "concept_like_entity"
        elif entity_name_key(name) in BACKGROUND_ENTITY_KEYS and decision != "promote":
            status = "background"
            reason = "background_entity"
        elif decision == "reject":
            status = CONCEPT_STATUS_REJECTED
            reason = "manual_reject"
        elif decision == "merge":
            status = CONCEPT_STATUS_MERGED
            reason = "manual_merge"
        elif decision == "promote":
            status = CONCEPT_STATUS_PUBLISHED
            reason = "manual_promote"
        elif taxonomy_name_key(name) in existing_published and count >= config.entity_min_sources and strong_ai_entity:
            status = CONCEPT_STATUS_PUBLISHED
            reason = "existing_ai_page_bootstrap"
        elif ai_related and publishable_entity and count >= config.entity_min_sources and (date_count >= 2 or count >= 5):
            status = CONCEPT_STATUS_EMERGING
            reason = "ai_related_repeated_needs_review"
        elif count >= 3:
            status = "observed"
            reason = "observed_relation_state_only"
        else:
            status = CONCEPT_STATUS_CANDIDATE
            reason = "needs_more_evidence"
        lifecycle[name] = {
            "status": status,
            "layer": "published_entity" if status == CONCEPT_STATUS_PUBLISHED else f"{status}_entity",
            "source_count": count,
            "date_count": date_count,
            "dates": sorted(record_dates(deduped), reverse=True)[:8],
            "ai_related": ai_related,
            "entity_type": entity_type,
            "strong_ai_entity": strong_ai_entity,
            "reason": reason,
        }
        if decision_target:
            lifecycle[name]["canonical"] = decision_target
    return dict(sorted(lifecycle.items()))


def is_strong_ai_entity_name(name: str, entity_type: str, records: list[dict[str, Any]]) -> bool:
    key = entity_name_key(name)
    text_key = taxonomy_name_key(name)
    if key in BACKGROUND_ENTITY_KEYS:
        return False
    if key in AI_ENTITY_KEYS or has_ai_related_keyword(text_key):
        return True
    normalized_type = str(entity_type or "").strip().lower()
    if normalized_type in {"model", "benchmark", "dataset"}:
        return any(record_has_ai_signal(record) for record in records)
    return False


def is_publishable_entity_name(name: str, entity_type: str, records: list[dict[str, Any]]) -> bool:
    key = entity_name_key(name)
    text_key = taxonomy_name_key(name)
    if key in BACKGROUND_ENTITY_KEYS:
        return False
    if key in AI_ENTITY_KEYS or has_ai_related_keyword(text_key):
        return True
    normalized_type = str(entity_type or "").strip().lower()
    if normalized_type in {"product", "model", "framework", "dataset", "benchmark", "paper", "protocol", "project", "standard", "browser"}:
        return True
    if normalized_type in {"organization", "company"}:
        return len(dedupe_records(records)) >= 5 and any(record_has_ai_signal(record) for record in records)
    if normalized_type == "person":
        return len(dedupe_records(records)) >= 8 and any(record_has_ai_signal(record) for record in records)
    return False


def filter_entity_index_for_published_pages(
    entity_index: dict[str, list[dict[str, Any]]],
    lifecycle: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    return {
        name: records
        for name, records in entity_index.items()
        if str(lifecycle.get(name, {}).get("status", CONCEPT_STATUS_CANDIDATE)) in PUBLISHED_ENTITY_STATUSES
    }


def write_concept_lifecycle_state(lifecycle: dict[str, dict[str, Any]], config: AppConfig) -> None:
    payload = {
        "generated_at": now_iso(),
        "statuses": lifecycle,
        "policy": {
            "published": "Seeded, existing bootstrap, or manually promoted concepts that generate pages.",
            "emerging": "AI-related repeated concepts kept in review until promoted.",
            "candidate": "New or low-evidence concepts kept for review, not published pages.",
            "deprecated": "Aliases that should resolve to a canonical concept.",
            "rejected": "Out-of-scope or manually rejected concepts.",
        },
    }
    write_json(config.state_dir / "concept_lifecycle.json", payload)


def write_entity_lifecycle_state(lifecycle: dict[str, dict[str, Any]], config: AppConfig) -> None:
    payload = {
        "generated_at": now_iso(),
        "statuses": lifecycle,
        "policy": {
            "published": "Manually promoted entities plus strong AI-scope existing entities that generate pages.",
            "emerging": "AI-related repeated entities kept in review until promoted.",
            "observed": "Observed named objects kept only in relation state.",
            "candidate": "Low-evidence entities kept for review, not published pages.",
            "rejected": "Concept-like or manually rejected entities.",
        },
    }
    write_json(config.state_dir / "entity_lifecycle.json", payload)


def record_dates(records: list[dict[str, Any]]) -> set[str]:
    dates: set[str] = set()
    for record in records:
        value = normalize_iso_date(str(record.get("date", "") or record.get("compiled_at", "") or ""))
        if value:
            dates.add(value)
    return dates


def is_ai_related_concept(name: str, records: list[dict[str, Any]] | None = None) -> bool:
    key = taxonomy_name_key(name)
    if has_ai_related_keyword(key):
        return True
    if name in APPROVED_CONCEPTS:
        return True
    return False


def is_ai_related_entity(name: str, records: list[dict[str, Any]] | None = None) -> bool:
    key = entity_name_key(name)
    text_key = taxonomy_name_key(name)
    if key in AI_ENTITY_KEYS:
        return True
    if has_ai_related_keyword(text_key):
        return True
    if records and any(record_has_ai_signal(record) for record in records):
        return True
    return False


def is_concept_like_entity(name: str) -> bool:
    key = entity_name_key(name)
    if key in ENTITY_CONCEPT_LIKE_KEYS:
        return True
    normalized_concept = normalize_concept_name(name)
    if normalized_concept and taxonomy_name_key(normalized_concept) == taxonomy_name_key(name):
        concept_key = taxonomy_name_key(name)
        if concept_key in {taxonomy_name_key(item) for item in APPROVED_CONCEPTS}:
            return True
    text_key = taxonomy_name_key(name)
    concept_markers = (
        "alignment",
        "automation",
        "benchmark",
        "benchmarking",
        "chain of thought",
        "context engineering",
        "diffusion",
        "evaluation",
        "fine tuning",
        "hallucination",
        "inference",
        "learning",
        "memory",
        "optimization",
        "prompt engineering",
        "quantization",
        "reasoning",
        "retrieval",
        "safety",
        "training",
    )
    if any(marker in text_key for marker in concept_markers):
        allowed_entity_prefixes = ("chatgpt", "claude", "gemini", "gpt", "llama", "qwen", "deepseek", "swe bench")
        if not text_key.startswith(allowed_entity_prefixes):
            return True
    return False


def has_ai_related_keyword(normalized_text: str) -> bool:
    tokens = set(str(normalized_text or "").split())
    for keyword in AI_RELATED_KEYWORDS:
        key = taxonomy_name_key(keyword)
        if not key:
            continue
        key_tokens = key.split()
        if len(key_tokens) == 1:
            if key in tokens:
                return True
            continue
        if all(token in tokens for token in key_tokens):
            return True
    return False


def record_has_ai_signal(record: dict[str, Any]) -> bool:
    fragments = [
        str(record.get("title", "") or ""),
        str(record.get("display_title", "") or ""),
        " ".join(str(item) for item in record.get("concepts", []) or []),
        " ".join(str(item) for item in record.get("themes", []) or []),
        " ".join(str(item) for item in record.get("summary", []) or []),
    ]
    key = taxonomy_name_key(" ".join(fragments))
    return has_ai_related_keyword(key)


def normalize_existing_registry(
    registry: dict[str, Any], config: AppConfig, compiler_build: str | None = None
) -> dict[str, Any]:
    updated: dict[str, Any] = {}
    for source_id, record in registry.items():
        if not isinstance(record, dict):
            continue
        # 已用当前 build 规范化过的 record 直接复用，跳过 normalize + frontmatter 重读，
        # 避免每次运行都对整表 record + 每个 note read_text+yaml.safe_load。
        if compiler_build and record.get("normalized_build") == compiler_build:
            updated[source_id] = record
            continue
        normalized = normalize_record_entities(record, config)
        if compiler_build:
            normalized["normalized_build"] = compiler_build
        note_path_value = normalized.get("note_path")
        if isinstance(note_path_value, str) and note_path_value and config.generate_source_notes:
            note_path = config.repo_root / note_path_value
            if note_path.exists():
                maybe_update_note_frontmatter(note_path, normalized, config)
        updated[source_id] = normalized
    return updated


def strip_entity_section_from_note_body(body: str) -> str:
    lines = body.splitlines()
    output: list[str] = []
    skipping = False
    for line in lines:
        heading = line.strip()
        if heading == "## 相关对象":
            skipping = True
            continue
        if skipping:
            if heading.startswith("## ") and heading != "## 相关对象":
                skipping = False
            else:
                continue
        output.append(line)
    return "\n".join(output).strip() + "\n"


def cleanup_post_source_note_entity_sections(config: AppConfig) -> int:
    posts_dir = config.sources_dir / "posts"
    if not posts_dir.exists():
        return 0
    updated = 0
    for path in sorted(posts_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        frontmatter, body = split_frontmatter(text)
        if "## 相关对象" not in body:
            continue
        cleaned_body = strip_entity_section_from_note_body(body)
        if cleaned_body.rstrip() == body.strip():
            continue
        if frontmatter:
            write_markdown(path, frontmatter, cleaned_body.rstrip("\n"))
        else:
            write_text_with_retry(path, cleaned_body)
        updated += 1
    return updated


def cleanup_stale_entity_artifacts(config: AppConfig) -> dict[str, int]:
    stats = {"index_files": 0, "entity_files": 0, "post_notes": 0, "state_files": 0}
    if config.generate_entities:
        return stats
    for stale_index in (config.index_dir / "by-entity.md", config.index_dir / "entities.md"):
        if stale_index.exists():
            stale_index.unlink()
            stats["index_files"] += 1
    if config.entities_dir.exists():
        for path in config.entities_dir.glob("*"):
            if path.is_file():
                path.unlink()
                stats["entity_files"] += 1
    for stale_state in (
        config.state_dir / "entity_lifecycle.json",
        config.state_dir / "review" / "entity_anomalies.md",
        config.state_dir / "review" / "emerging_entities.md",
    ):
        if stale_state.exists():
            stale_state.unlink()
            stats["state_files"] += 1
    stats["post_notes"] = cleanup_post_source_note_entity_sections(config)
    return stats


def cleanup_legacy_wiki_dirs(config: AppConfig) -> dict[str, int]:
    stats = {"legacy_dirs": 0}
    legacy_dirs = [config.emerging_dir, config.domains_dir, config.families_dir]
    if not config.generate_entities:
        legacy_dirs.append(config.entities_dir)
    for path in legacy_dirs:
        if not path.exists() or not path.is_dir():
            continue
        for child in path.glob("*"):
            if child.is_file():
                child.unlink()
        if not any(path.iterdir()):
            path.rmdir()
            stats["legacy_dirs"] += 1
    return stats


def delete_wiki_source_note(record: dict[str, Any], config: AppConfig) -> None:
    note_path_value = str(record.get("note_path", "") or "").strip()
    if not note_path_value or not is_mirrored_wiki_source_note(note_path_value):
        return
    note_path = config.repo_root / note_path_value
    if note_path.exists():
        note_path.unlink()


def record_link_path(record: dict[str, Any], config: AppConfig) -> Path | None:
    note_path = str(record.get("note_path", "") or "").strip()
    source_path = str(record.get("source_path", "") or "").strip()
    source_kind = str(record.get("source_kind", "") or "").strip()
    if not config.generate_source_notes and source_kind == "article" and source_path:
        return config.repo_root / source_path
    if note_path:
        return config.repo_root / note_path
    if source_path:
        return config.repo_root / source_path
    return None


def is_mirrored_wiki_source_note(path_value: str) -> bool:
    normalized = path_value.replace("\\", "/").strip().lower()
    return normalized.startswith(("wiki/sources/", "mymind/wiki/sources/"))


def _strip_legacy_mymind_prefix(value: str) -> str:
    normalized = value.replace("\\", "/")
    if normalized.startswith("mymind/"):
        return normalized[len("mymind/"):]
    return value


def migrate_registry_legacy_paths(registry: dict[str, Any]) -> dict[str, Any]:
    """One-time migration for records written before the flat content root.

    Legacy layouts stored root-relative ids and paths with a leading
    ``mymind/`` segment; the flat content root has no such layer, so those
    records resolve to dead paths and re-register as duplicates. Strip the
    prefix and merge collisions, preferring records already stored flat.
    """
    flat_records: list[tuple[str, Any]] = []
    legacy_records: list[tuple[str, Any]] = []
    for source_id, record in registry.items():
        new_id = source_id.replace(":mymind/", ":", 1) if isinstance(source_id, str) else source_id
        (flat_records if new_id == source_id else legacy_records).append((new_id, record))
    migrated: dict[str, Any] = {}
    for new_id, record in flat_records + legacy_records:
        if new_id in migrated:
            continue
        if not isinstance(record, dict):
            migrated[new_id] = record
            continue
        updated = dict(record)
        for field in ("source_path", "note_path"):
            raw = updated.get(field)
            if isinstance(raw, str) and raw:
                updated[field] = _strip_legacy_mymind_prefix(raw)
        raw_id = updated.get("source_id")
        if isinstance(raw_id, str) and ":mymind/" in raw_id:
            updated["source_id"] = raw_id.replace(":mymind/", ":", 1)
        migrated[new_id] = updated
    if legacy_records or len(migrated) != len(registry):
        print(
            f"[migrate] registry legacy paths normalized: {len(registry)} -> {len(migrated)} records",
            flush=True,
        )
    return migrated


def migrate_registry_link_paths(registry: dict[str, Any], config: AppConfig) -> dict[str, Any]:
    if config.generate_source_notes:
        return registry
    migrated: dict[str, Any] = {}
    for source_id, record in registry.items():
        if not isinstance(record, dict):
            continue
        updated = dict(record)
        source_kind = str(updated.get("source_kind", "") or "").strip()
        source_path = str(updated.get("source_path", "") or "").strip()
        note_path = str(updated.get("note_path", "") or "").strip()
        if source_kind == "article" and source_path:
            if not note_path or is_mirrored_wiki_source_note(note_path):
                updated["note_path"] = source_path
        migrated[source_id] = updated
    return migrated


def prune_mirrored_article_source_notes(config: AppConfig) -> int:
    if config.generate_source_notes:
        return 0
    articles_dir = config.sources_dir / "articles"
    if not articles_dir.exists():
        return 0
    removed = 0
    for path in sorted(articles_dir.rglob("*.md")):
        path.unlink(missing_ok=True)
        removed += 1
    return removed


def collect_existing_source_records(config: AppConfig) -> dict[str, dict[str, Any]]:
    registry_path = config.state_dir / "registry.json"
    if registry_path.exists():
        registry = load_json(registry_path, default={})
        if isinstance(registry, dict) and registry:
            records = {
                source_id: record
                for source_id, record in registry.items()
                if isinstance(record, dict) and str(record.get("source_id", "") or source_id).strip()
            }
            if records:
                return migrate_registry_link_paths(migrate_registry_legacy_paths(records), config)
    records: dict[str, dict[str, Any]] = {}
    for path in config.sources_dir.rglob("*.md"):
        note = parse_source_note(path, config)
        if not note:
            continue
        records[note["source_id"]] = note
    return migrate_registry_link_paths(records, config)


def prune_missing_note_records(
    registry: dict[str, dict[str, Any]],
    manifest: dict[str, Any],
    config: AppConfig,
) -> None:
    missing_ids = [
        source_id
        for source_id, record in registry.items()
        if not record_note_exists(record, config)
    ]
    for source_id in missing_ids:
        registry.pop(source_id, None)
        manifest.pop(source_id, None)


def record_note_exists(record: dict[str, Any], config: AppConfig) -> bool:
    target = record_link_path(record, config)
    if target is None:
        return False
    return target.exists()


def record_label(record: dict[str, Any]) -> str:
    display_title = str(record.get("display_title", "") or "").strip()
    if display_title:
        return display_title
    return str(record.get("title", "") or "").strip()


def display_group_name(group_type: str, name: str) -> str:
    return GROUP_DISPLAY_NAME_MAP.get(group_type, {}).get(name, name)


def display_group_heading(group_type: str, name: str) -> str:
    display_name = display_group_name(group_type, name)
    if display_name != name:
        return f"{display_name}（{name}）"
    return display_name


def heading_variants(key: str) -> tuple[str, ...]:
    return SECTION_HEADING_ALIASES.get(key, (key,))


def section_lines(sections: dict[str, list[str]], key: str) -> list[str]:
    for heading in heading_variants(key):
        if heading in sections:
            return sections.get(heading, [])
    return []


SOURCE_METADATA_TITLE_RE = re.compile(r"^- \*\*(?:原始标题|Original Title)\*\*:", re.IGNORECASE)


def normalize_source_metadata_lines(lines: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_line in lines:
        line = str(raw_line or "").strip()
        if not line or SOURCE_METADATA_TITLE_RE.match(line):
            continue
        key = re.sub(r"\s+", " ", line).casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(line)
    return normalized


def parse_source_note(path: Path, config: AppConfig) -> dict[str, Any] | None:
    text = path.read_text(encoding="utf-8")
    frontmatter, body = split_frontmatter(text)
    if not frontmatter:
        return None
    source_id = str(frontmatter.get("source_id", "")).strip()
    if not source_id:
        return None
    sections = parse_note_sections(body)
    return {
        "source_id": source_id,
        "source_kind": str(frontmatter.get("source_kind", "")).strip(),
        "source_path": str(frontmatter.get("source_path", "")).strip(),
        "note_path": path.relative_to(config.repo_root).as_posix(),
        "title": str(frontmatter.get("title", "")).strip(),
        "display_title": str(frontmatter.get("display_title", "")).strip(),
        "date": str(frontmatter.get("date", "")).strip(),
        "canonical_url": str(frontmatter.get("canonical_url", "")).strip(),
        "author": str(frontmatter.get("author", "")).strip(),
        "content_type": str(frontmatter.get("content_type", "")).strip(),
        "status": str(frontmatter.get("status", "")).strip(),
        "why_it_matters": str(frontmatter.get("why_it_matters", "")).strip(),
        "my_take": str(frontmatter.get("my_take", "")).strip(),
        "actionability": str(frontmatter.get("actionability", "")).strip(),
        "confidence": str(frontmatter.get("confidence", "")).strip(),
        "first_seen": str(frontmatter.get("first_seen", "")).strip(),
        "last_seen": str(frontmatter.get("last_seen", "")).strip(),
        "last_confirmed": str(frontmatter.get("last_confirmed", "")).strip(),
        "revisit_after": str(frontmatter.get("revisit_after", "")).strip(),
        "next_action": str(frontmatter.get("next_action", "")).strip(),
        "concepts": list(frontmatter.get("concepts", []) or []),
        "entities": list(frontmatter.get("entities", []) or []),
        "entity_details": list(frontmatter.get("entity_details", []) or []),
        "entity_provider": str(frontmatter.get("entity_provider", "")).strip(),
        "themes": list(frontmatter.get("themes", []) or []),
        "theme_candidates": list(frontmatter.get("theme_candidates", []) or []),
        "summary": bullet_values(section_lines(sections, "summary")),
        "key_signals": bullet_values(section_lines(sections, "key_signals")),
        "open_questions": bullet_values(section_lines(sections, "open_questions")),
        "metadata_lines": normalize_source_metadata_lines(section_lines(sections, "source_metadata")),
        "compiled_at": str(frontmatter.get("compiled_at", "")).strip(),
    }


_concept_aliases_cache: dict[str, str] | None = None


def get_concept_aliases(config: AppConfig) -> dict[str, str]:
    global _concept_aliases_cache
    if _concept_aliases_cache is None:
        _concept_aliases_cache = load_concept_aliases(config)
    return _concept_aliases_cache


def normalize_record_entities(record: dict[str, Any], config: AppConfig) -> dict[str, Any]:
    record = dict(record)
    record["concepts"] = normalize_concept_list(record.get("concepts", []), aliases=get_concept_aliases(config))
    if not config.generate_entities:
        record.pop("entities", None)
        record.pop("entity_details", None)
        record.pop("entity_provider", None)
        return normalize_record_knowledge_fields(record, config)
    details = record.get("entity_details", [])
    names = record.get("entities", [])
    blocked_names = [str(record.get("author", "")).strip()]
    normalized_details = normalize_entity_details(details, names, blocked_names=blocked_names)
    normalized_names = [item["name"] for item in normalized_details]
    record["entity_details"] = normalized_details
    record["entities"] = normalized_names
    return normalize_record_knowledge_fields(record, config)


def normalize_entity_details(details: Any, fallback_names: Any, *, blocked_names: list[str] | None = None) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    blocked = {entity_name_key(name) for name in (blocked_names or []) if entity_name_key(name)}

    if isinstance(details, list):
        for item in details:
            if not isinstance(item, dict):
                continue
            aliases = item.get("aliases", [])
            if not isinstance(aliases, list):
                aliases = []
            entity_type = str(item.get("type", "unknown")).strip() or "unknown"
            entity_confidence = float(item.get("confidence", 0) or 0)
            entity_salience = normalize_entity_salience(item.get("salience"), confidence=entity_confidence)
            if not should_keep_entity_type(entity_type, salience=entity_salience, confidence=entity_confidence):
                continue
            split_names = split_entity_name(str(item.get("name", "")).strip())
            for raw_name in split_names:
                original_name = str(raw_name or "").strip()
                name = normalize_entity_name(original_name)
                if not name:
                    continue
                key = entity_name_key(name)
                if key in blocked:
                    continue
                if key in seen:
                    continue
                seen.add(key)
                normalized_type = normalize_entity_type(name, entity_type)
                alias_values = [str(alias).strip() for alias in aliases if str(alias).strip()]
                if original_name and original_name != name:
                    alias_values.insert(0, original_name)
                normalized.append(
                    {
                        "name": name,
                        "type": normalized_type,
                        "aliases": dedupe_preserve(alias_values),
                        "confidence": entity_confidence,
                        "salience": entity_salience,
                        "evidence": str(item.get("evidence", "")).strip(),
                    }
                )

    if not normalized and isinstance(fallback_names, list):
        for raw_name in fallback_names:
            for candidate in split_entity_name(str(raw_name).strip()):
                original_name = str(candidate or "").strip()
                name = normalize_entity_name(original_name)
                if not name:
                    continue
                key = entity_name_key(name)
                if key in blocked:
                    continue
                if not name or key in seen:
                    continue
                seen.add(key)
                normalized.append(
                    {
                        "name": name,
                        "type": normalize_entity_type(name, "rule_fallback"),
                        "aliases": [original_name] if original_name and original_name != name else [],
                        "confidence": 0.0,
                        "salience": 2,
                        "evidence": "Matched by local fallback.",
                    }
                )
    return normalized


def split_entity_name(name: str) -> list[str]:
    if not name:
        return []
    parts = [part.strip() for part in re.split(r"[;；]+", name) if part.strip()]
    if len(parts) <= 1:
        return [name]
    return parts[:4]


def normalize_entity_name(name: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(name or "").strip())
    if not cleaned:
        return ""
    cleaned = re.sub(r"\s+\([^)]*@[^)]*\)$", "", cleaned).strip()
    if DOMAIN_ENTITY_RE.fullmatch(cleaned):
        return ""
    canonical = ENTITY_CANONICAL_MAP.get(cleaned.lower()) or ENTITY_CANONICAL_MAP.get(entity_name_key(cleaned))
    if canonical:
        return canonical
    return cleaned


def normalize_entity_type(name: str, entity_type: str) -> str:
    normalized_name = normalize_entity_name(name).lower()
    if normalized_name in ENTITY_TYPE_OVERRIDES:
        return ENTITY_TYPE_OVERRIDES[normalized_name]
    normalized_type = str(entity_type or "").strip().lower()
    type_aliases = {
        "company": "organization",
        "org": "organization",
        "tool": "product",
        "app": "product",
        "application": "product",
        "model": "product",
        "browser": "browser",
        "platform": "platform",
        "protocol": "protocol",
        "standard": "standard",
        "project": "project",
        "person": "person",
        "organization": "organization",
        "product": "product",
        "event": "event",
    }
    if normalized_type in type_aliases:
        return type_aliases[normalized_type]
    if normalized_type in {"object", "unknown", "rule_fallback"}:
        lowered_name = normalized_name
        if lowered_name in {"claude", "claude code", "cursor", "gemini", "chatgpt", "atlas"}:
            return "product"
        if lowered_name in {"chrome"}:
            return "browser"
        if lowered_name in {"x"}:
            return "platform"
        if lowered_name in {"openai", "anthropic", "google", "servicenow"}:
            return "organization"
    return normalized_type or "unknown"


def normalize_entity_salience(raw_value: Any, *, confidence: float = 0.0) -> int:
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        value = 0
    if 1 <= value <= 5:
        return value
    if confidence >= 0.97:
        return 5
    if confidence >= 0.9:
        return 4
    if confidence >= 0.82:
        return 3
    if confidence >= 0.7:
        return 2
    return 1


def should_keep_entity_type(entity_type: str, *, salience: int, confidence: float) -> bool:
    normalized = str(entity_type or "").strip().lower()
    if normalized in {"publication", "media", "channel", "account", "newsletter", "blog"}:
        return salience >= 5 and confidence >= 0.9
    return True


def rename_existing_source_notes(config: AppConfig) -> None:
    notes = []
    for path in sorted(config.sources_dir.rglob("*.md")):
        parsed = parse_source_note(path, config)
        if not parsed:
            continue
        notes.append((path, parsed))

    if not notes:
        return

    grouped: dict[Path, list[tuple[Path, dict[str, Any]]]] = defaultdict(list)
    for path, parsed in notes:
        grouped[path.parent].append((path, parsed))

    rename_map: dict[Path, Path] = {}
    for parent, items in grouped.items():
        counters: dict[str, int] = defaultdict(int)
        for path, parsed in sorted(items, key=lambda item: (item[1].get("title", ""), item[1].get("source_id", ""))):
            base = sanitize_title_filename(parsed.get("title", "")) or "note"
            counters[base] += 1
            suffix = "" if counters[base] == 1 else f"-{counters[base]}"
            target = parent / f"{base}{suffix}.md"
            if path != target:
                rename_map[path] = target

    if not rename_map:
        return

    temp_map: dict[Path, Path] = {}
    for source, target in rename_map.items():
        temp = source.with_name(f"__renaming__{source.name}")
        source.rename(temp)
        temp_map[temp] = target

    for temp, target in temp_map.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        temp.rename(target)

    rewrite_source_note_links(config)


def maybe_update_note_frontmatter(note_path: Path, record: dict[str, Any], config: AppConfig) -> None:
    text = note_path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return
    parts = text.split("---\n", 2)
    if len(parts) < 3:
        return
    _, frontmatter_text, body = parts
    try:
        frontmatter = yaml.safe_load(frontmatter_text) or {}
    except Exception:
        return
    sections = parse_note_sections(body)
    updated = False
    if (
        frontmatter.get("entities") != record.get("entities")
        or frontmatter.get("entity_details") != record.get("entity_details")
        or frontmatter.get("concepts") != record.get("concepts")
        or frontmatter.get("themes") != record.get("themes")
        or frontmatter.get("theme_candidates") != record.get("theme_candidates")
    ):
        frontmatter["concepts"] = record.get("concepts", [])
        frontmatter["entities"] = record.get("entities", [])
        frontmatter["entity_details"] = record.get("entity_details", [])
        frontmatter["themes"] = record.get("themes", [])
        frontmatter["theme_candidates"] = record.get("theme_candidates", [])
        if record.get("entity_provider"):
            frontmatter["entity_provider"] = record.get("entity_provider")
        updated = True
    for key in (
        "display_title",
        "content_type",
        "status",
        "why_it_matters",
        "my_take",
        "actionability",
        "confidence",
        "first_seen",
        "last_seen",
        "last_confirmed",
        "revisit_after",
        "next_action",
    ):
        value = record.get(key, "")
        if frontmatter.get(key) != value:
            frontmatter[key] = value
            updated = True
    summary = list(record.get("summary", []) or bullet_values(section_lines(sections, "summary")))
    key_signals = list(record.get("key_signals", []) or bullet_values(section_lines(sections, "key_signals")))
    open_questions = list(record.get("open_questions", []) or bullet_values(section_lines(sections, "open_questions")))
    metadata_lines = normalize_source_metadata_lines(
        list(record.get("metadata_lines", []) or section_lines(sections, "source_metadata"))
    )
    extra_sections = render_extra_sections(sections)
    rendered_body = render_source_note_body(
        title=str(record.get("title", "") or frontmatter.get("title", "") or note_path.stem),
        display_title=str(record.get("display_title", "") or frontmatter.get("display_title", "") or ""),
        summary=summary or ["No summary was extracted from the source body."],
        key_signals=key_signals or summary or ["No key signals were extracted from the source body."],
        knowledge_fields=record,
        note_path=note_path,
        concept_names=list(record.get("concepts", []) or []),
        entity_names=list(record.get("entities", []) or []),
        theme_names=list(record.get("themes", []) or []),
        open_questions=open_questions or build_open_questions(
            str(record.get("title", "") or frontmatter.get("title", "") or note_path.stem),
            list(record.get("concepts", []) or []),
            list(record.get("entities", []) or []),
        ),
        metadata_lines=metadata_lines or ["- None"],
        extra_sections=extra_sections,
        config=config,
    )
    rewritten_body = rewrite_group_links(rendered_body)
    if not updated and rewritten_body.strip() == body.strip():
        return
    content = "---\n" + yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False).strip() + "\n---\n" + rewritten_body
    write_text_with_retry(note_path, content)


def rewrite_group_links(body: str) -> str:
    lines = body.splitlines()
    rewritten: list[str] = []
    pattern = re.compile(r"^(\s*-\s*\[)(.+?)(\]\()(.+?)(\)\s*)$")
    for line in lines:
        match = pattern.match(line)
        if not match:
            rewritten.append(line)
            continue
        prefix, label, mid, target, suffix = match.groups()
        new_target = target
        new_label = label
        if "/concepts/" in target.replace("\\", "/"):
            canonical_label = normalize_concept_name(label) or label
            base_dir = target.replace("\\", "/").rsplit("/", 1)[0]
            new_label = canonical_label
            new_target = f"{base_dir}/{group_slug(canonical_label, 'concept')}.md"
        elif "/entities/" in target.replace("\\", "/"):
            base_dir = target.replace("\\", "/").rsplit("/", 1)[0]
            new_target = f"{base_dir}/{group_slug(label, 'entity')}.md"
        elif "/themes/" in target.replace("\\", "/"):
            base_dir = target.replace("\\", "/").rsplit("/", 1)[0]
            new_target = f"{base_dir}/{group_slug(label, 'theme')}.md"
        rewritten.append(f"{prefix}{new_label}{mid}{new_target}{suffix}")
    return "\n".join(rewritten)


def rewrite_source_note_links(config: AppConfig) -> None:
    all_md = [path for path in config.wiki_dir.rglob("*.md") if path.is_file()]
    wiki_files = {path.resolve() for path in all_md}
    vault_root = config.wiki_dir.parent.resolve()

    if config.link_style != "obsidian_wikilink":
        wikilink_pattern = re.compile(r"\[\[([^|\]]+)(?:\|([^\]]+))?\]\]")
        for path in all_md:
            text = path.read_text(encoding="utf-8")

            def _wikilink_to_markdown(match: re.Match[str]) -> str:
                raw_target = match.group(1).strip()
                label = (match.group(2) or raw_target.rsplit("/", 1)[-1]).strip()
                target_text = raw_target
                if not target_text.endswith(".md"):
                    target_text = f"{target_text}.md"
                candidates = [
                    (vault_root / target_text).resolve(),
                    (config.wiki_dir / target_text).resolve(),
                    (config.repo_root / target_text).resolve(),
                ]
                candidate = next((item for item in candidates if item in wiki_files), None)
                if not candidate:
                    return label
                return f"[{label}]({markdown_link_target(candidate, path.parent)})"

            rewritten = wikilink_pattern.sub(_wikilink_to_markdown, text)
            if rewritten != text:
                write_text_with_retry(path, rewritten)
        return

    pattern = re.compile(r"\[([^\]]+)\]\(([^)]+\.md)\)")

    for path in all_md:
        text = path.read_text(encoding="utf-8")

        def _replace(match: re.Match[str]) -> str:
            label = match.group(1)
            target = match.group(2)
            if target.startswith("http://") or target.startswith("https://"):
                return match.group(0)
            candidate = (path.parent / target).resolve()
            if candidate not in wiki_files:
                return match.group(0)
            try:
                wiki_target = candidate.relative_to(vault_root).as_posix()
            except ValueError:
                wiki_target = candidate.relative_to(config.repo_root.resolve()).as_posix()
            if wiki_target.endswith(".md"):
                wiki_target = wiki_target[:-3]
            return f"[[{wiki_target}|{label}]]"

        rewritten = pattern.sub(_replace, text)
        if rewritten != text:
            write_text_with_retry(path, rewritten)


def write_text_with_retry(path: Path, text: str, *, attempts: int = 5) -> bool:
    for attempt in range(1, attempts + 1):
        try:
            path.write_text(text, encoding="utf-8")
            return True
        except OSError as exc:
            if attempt >= attempts:
                print(f"[warn] failed to write {path}: {exc}", flush=True)
                return False
            time.sleep(0.2 * attempt)
    return False


def write_group_pages(
    *,
    group_type: str,
    index: dict[str, list[dict[str, Any]]],
    output_dir: Path,
    min_sources: int,
    config: AppConfig,
    text_writer: KnowledgeWikiWriter,
    group_ai_cache: dict[str, Any],
    compiler_build: str,
    only_names: set[str] | None = None,
    allow_ai: bool = True,
    lifecycle: dict[str, dict[str, Any]] | None = None,
) -> None:
    keep_files: set[str] = set()
    if only_names is None:
        items = sorted(index.items())
    else:
        items = [(name, index.get(name, [])) for name in sorted({str(item).strip() for item in only_names if str(item).strip()})]
    total_items = len(items)
    for position, (name, records) in enumerate(items, start=1):
        deduped = dedupe_records(records)
        if len(deduped) < min_sources:
            continue
        print(f"[aggregate:{group_type}] {position}/{total_items} {name} ({len(deduped)} sources)", flush=True)
        slug = group_slug(name, group_type)
        path = output_dir / f"{slug}.md"
        sorted_records = sort_records_for_group(group_type, name, deduped)
        related_concepts_for_ai = related_terms(name, sorted_records, field="concepts", group_type=group_type)
        related_entities_for_ai = related_terms(name, sorted_records, field="entities", group_type=group_type)
        related_themes_for_ai = related_terms(name, sorted_records, field="themes", group_type=group_type)
        related_concepts = related_term_items(name, sorted_records, field="concepts", group_type=group_type)
        related_entities = related_term_items(name, sorted_records, field="entities", group_type=group_type)
        related_themes = related_term_items(name, sorted_records, field="themes", group_type=group_type)
        ai_records = records_for_group_ai(group_type, name, sorted_records)
        cache_key = group_ai_cache_key(group_type, name)
        input_hash = group_ai_input_hash(
            text_writer=text_writer,
            group_type=group_type,
            name=name,
            records=sorted_records,
            ai_records=ai_records,
            related_concepts=related_concepts_for_ai,
            related_entities=related_entities_for_ai,
            related_themes=related_themes_for_ai,
        )
        cached_entry = group_ai_cache.get(cache_key)
        exact_cache_hit = (
            isinstance(cached_entry, dict)
            and cached_entry.get("input_hash") == input_hash
            and isinstance(cached_entry.get("payload"), dict)
        )
        legacy_cache_hit = (
            isinstance(cached_entry, dict)
            and not exact_cache_hit
            and isinstance(cached_entry.get("payload"), dict)
            and existing_group_source_count(path) == len(deduped)
        )
        cache_hit = exact_cache_hit or legacy_cache_hit
        ai_group = dict(cached_entry["payload"]) if cache_hit else None
        if legacy_cache_hit and isinstance(ai_group, dict):
            group_ai_cache[cache_key] = {
                "input_hash": input_hash,
                "payload": ai_group,
                "updated_at": existing_group_updated_at(path) or str(cached_entry.get("updated_at", "") or "") or now_iso(),
            }
        if not cache_hit and allow_ai:
            ai_group = text_writer.generate_group_copy(
                group_type=group_type,
                name=name,
                source_summaries=ai_records,
                related_concepts=related_concepts_for_ai,
                related_entities=related_entities_for_ai,
                related_themes=related_themes_for_ai,
            )
            if isinstance(ai_group, dict):
                group_ai_cache[cache_key] = {
                    "input_hash": input_hash,
                    "payload": ai_group,
                    "updated_at": now_iso(),
                }
        if not isinstance(ai_group, dict):
            ai_group = {}
        keep_files.add(path.name)
        updated_at = now_iso()
        if cache_hit:
            updated_at = existing_group_updated_at(path) or str(cached_entry.get("updated_at", "") or "") or updated_at
        page_status = str((lifecycle or {}).get(name, {}).get("status", CONCEPT_STATUS_PUBLISHED))
        page_layer = str((lifecycle or {}).get(name, {}).get("layer", "")).strip()
        if not page_layer:
            if group_type == "concept":
                page_layer = f"{page_status}_concept"
            elif group_type == "entity":
                page_layer = "published_entity"
            else:
                page_layer = "stable_theme" if page_status == CONCEPT_STATUS_PUBLISHED else "emerging_theme"
        frontmatter = {
            "type": group_type,
            "slug": slug,
            "title": name,
            "display_name": display_group_name(group_type, name),
            "status": page_status,
            "layer": page_layer,
            "source_count": len(deduped),
            "updated_at": updated_at,
        }
        body = build_group_page_body(
            group_type=group_type,
            name=name,
            records=sorted_records,
            page_path=path,
            related_concepts=related_concepts,
            related_entities=related_entities,
            related_themes=related_themes,
            config=config,
            text_writer=text_writer,
            ai_group=ai_group,
            ai_records=ai_records,
        )
        write_markdown(path, frontmatter, body)

    if only_names is None:
        for old_file in output_dir.glob("*.md"):
            if old_file.name not in keep_files:
                old_file.unlink()
        return

    for name, _records in items:
        path = output_dir / f"{group_slug(name, group_type)}.md"
        if path.name not in keep_files and path.exists():
            path.unlink()


def eligible_group_cache_keys(group_type: str, index: dict[str, list[dict[str, Any]]], min_sources: int) -> set[str]:
    active_keys: set[str] = set()
    for name, records in index.items():
        if len(dedupe_records(records)) < min_sources:
            continue
        active_keys.add(group_ai_cache_key(group_type, name))
    return active_keys


def group_ai_cache_key(group_type: str, name: str) -> str:
    return f"{group_type}:{name}"


def group_ai_input_hash(
    *,
    text_writer: KnowledgeWikiWriter,
    group_type: str,
    name: str,
    records: list[dict[str, Any]],
    ai_records: list[dict[str, Any]],
    related_concepts: list[str],
    related_entities: list[str],
    related_themes: list[str],
) -> str:
    payload = {
        "cache_version": GROUP_AI_CACHE_VERSION,
        "provider_signature": group_ai_provider_signature(text_writer),
        "group_type": group_type,
        "name": name,
        "records": [group_ai_record_fingerprint(record) for record in records],
        "ai_records": [group_ai_record_fingerprint(record, include_entity_context=True) for record in ai_records],
        "related_concepts": list(related_concepts),
        "related_entities": list(related_entities),
        "related_themes": list(related_themes),
    }
    return sha1_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def group_ai_provider_signature(text_writer: KnowledgeWikiWriter) -> list[dict[str, str]]:
    return [
        {
            "name": str(getattr(provider, "name", "")),
            "model": str(getattr(provider, "model", "")),
            "base_url": str(getattr(provider, "base_url", "")),
            "kind": str(getattr(provider, "kind", "")),
        }
        for provider in getattr(text_writer, "providers", [])
    ]


def group_ai_record_fingerprint(record: dict[str, Any], *, include_entity_context: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "source_id": str(record.get("source_id", "") or ""),
        "source_kind": str(record.get("source_kind", "") or ""),
        "note_path": str(record.get("note_path", "") or ""),
        "title": str(record.get("title", "") or ""),
        "display_title": str(record.get("display_title", "") or ""),
        "date": str(record.get("date", "") or ""),
        "canonical_url": str(record.get("canonical_url", "") or ""),
        "summary": clean_cache_list(record.get("summary"), limit=4),
        "key_signals": clean_cache_list(record.get("key_signals"), limit=4),
        "why_it_matters": str(record.get("why_it_matters", "") or ""),
        "actionability": str(record.get("actionability", "") or ""),
        "confidence": str(record.get("confidence", "") or ""),
        "concepts": clean_cache_list(record.get("concepts")),
        "entities": clean_cache_list(record.get("entities")),
        "themes": clean_cache_list(record.get("themes")),
    }
    if include_entity_context:
        payload["source_key"] = str(record.get("source_key", "") or "")
        payload["entity_salience"] = record.get("entity_salience")
        payload["entity_evidence"] = str(record.get("entity_evidence", "") or "")
    return payload


def clean_cache_list(value: Any, *, limit: int | None = None) -> list[str]:
    if not isinstance(value, list):
        return []
    items = [str(item).strip() for item in value if str(item).strip()]
    if limit is not None:
        return items[:limit]
    return items


def existing_group_updated_at(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        frontmatter, _body = split_frontmatter(path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    if not frontmatter:
        return ""
    return str(frontmatter.get("updated_at", "") or "").strip()


def existing_group_source_count(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        frontmatter, _body = split_frontmatter(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not frontmatter:
        return None
    try:
        return int(frontmatter.get("source_count"))
    except (TypeError, ValueError):
        return None


def prune_group_ai_cache(group_ai_cache: dict[str, Any], active_cache_keys: set[str]) -> None:
    for cache_key in list(group_ai_cache):
        if cache_key not in active_cache_keys:
            group_ai_cache.pop(cache_key, None)


def write_indexes(
    records: list[dict[str, Any]],
    concept_index: dict[str, list[dict[str, Any]]],
    entity_index: dict[str, list[dict[str, Any]]],
    theme_index: dict[str, list[dict[str, Any]]],
    config: AppConfig,
    text_writer: KnowledgeWikiWriter,
    *,
    allow_ai: bool = True,
) -> None:
    filtered_concepts: dict[str, list[dict[str, Any]]] = {}
    for name, group_records in concept_index.items():
        concept_file = config.concepts_dir / f"{group_slug(name, 'concept')}.md"
        if not concept_file.exists():
            continue
        filtered_concepts[name] = dedupe_records(group_records)
    filtered_entities: dict[str, list[dict[str, Any]]] = {}
    if config.generate_entities:
        seen_entity_files: set[str] = set()
        for name, group_records in sorted(entity_index.items()):
            entity_file = config.entities_dir / f"{group_slug(name, 'entity')}.md"
            file_key = entity_file.name.casefold()
            if not entity_file.exists() or file_key in seen_entity_files:
                continue
            seen_entity_files.add(file_key)
            filtered_entities[name] = dedupe_records(group_records)
    filtered_themes: dict[str, list[dict[str, Any]]] = {}
    for name, group_records in theme_index.items():
        theme_file = config.themes_dir / f"{group_slug(name, 'theme')}.md"
        if not theme_file.exists():
            continue
        filtered_themes[name] = dedupe_records(group_records)

    readme_lines = [
        "# 知识 Wiki",
        "",
        "## 语料概览",
        "",
        f"- 已索引文章：{len(records)}",
        f"- Theme 页：{len(filtered_themes)}",
        f"- Concept 页：{len(filtered_concepts)}",
    ]
    if config.generate_entities:
        readme_lines.append(f"- Entity 页：{len(filtered_entities)}")
    readme_lines.extend(
        [
            "",
            "## 快速入口",
            "",
            "- [按主题浏览](by-topic.md)",
            "- [Themes](themes.md)",
            "- [Concepts](concepts.md)",
            "- [最近更新](recently-updated.md)",
            "- [Review](review.md)",
            "- [Lint 报告](../_state/lint_report.md)",
            "",
            "## 主题热点",
            "",
            *[
                f"- {display_group_name('theme', name)}：{len(records_for_name)} 篇"
                for name, records_for_name in sorted(filtered_themes.items(), key=lambda item: (-len(item[1]), item[0]))[:12]
            ],
        ]
    )
    write_plain_markdown(config.index_dir / "README.md", "\n".join(readme_lines))

    by_topic = ["# 按主题浏览", "", "## 主题", "", "> 先看主题，再进入 concept。", ""]
    for name, group_records in sorted(filtered_themes.items(), key=lambda item: (-len(item[1]), item[0])):
        theme_file = config.themes_dir / f"{group_slug(name, 'theme')}.md"
        by_topic.append(
            f"- [{display_group_name('theme', name)}]({markdown_link_target(theme_file, config.index_dir)}): {len(group_records)} 篇"
        )
    by_topic.extend(["", "## 概念", ""])
    for name, group_records in sorted(filtered_concepts.items(), key=lambda item: (-len(item[1]), item[0])):
        concept_file = config.concepts_dir / f"{group_slug(name, 'concept')}.md"
        by_topic.append(
            f"- [{display_group_name('concept', name)}]({markdown_link_target(concept_file, config.index_dir)}): {len(group_records)} 篇"
        )
    write_plain_markdown(config.index_dir / "by-topic.md", "\n".join(by_topic))
    concept_start = next((index for index, line in enumerate(by_topic) if line == "## 概念"), len(by_topic))
    write_plain_markdown(config.index_dir / "concepts.md", "\n".join(["# Concepts", ""] + by_topic[concept_start + 1 :]))
    theme_lines = ["# Themes", ""]
    for name, group_records in sorted(filtered_themes.items(), key=lambda item: (-len(item[1]), item[0])):
        theme_file = config.themes_dir / f"{group_slug(name, 'theme')}.md"
        theme_lines.append(
            f"- [{display_group_name('theme', name)}]({markdown_link_target(theme_file, config.index_dir)}): {len(group_records)} 篇"
        )
    write_plain_markdown(config.index_dir / "themes.md", "\n".join(theme_lines))

    if config.generate_entities:
        entity_entries: list[tuple[str, int, str, str]] = []
        for name, group_records in filtered_entities.items():
            deduped_records = dedupe_records(group_records)
            entity_file = config.entities_dir / f"{group_slug(name, 'entity')}.md"
            entity_entries.append(
                (
                    name,
                    len(deduped_records),
                    common_entity_type(name, deduped_records),
                    markdown_link_target(entity_file, config.index_dir),
                )
            )
        by_entity = ["# 按对象浏览", "", "> 先按类型定位，再进入具体实体页。", ""]
        for group_label, items in group_entity_entries(entity_entries):
            by_entity.extend([f"## {group_label}", ""])
            for name, count, entity_type, target in items:
                type_suffix = f" · {translate_entity_type(entity_type)}" if entity_type else ""
                by_entity.append(f"- [{name}]({target}): {count} 篇{type_suffix}")
            by_entity.append("")
        write_plain_markdown(config.index_dir / "by-entity.md", "\n".join(by_entity))
        write_plain_markdown(config.index_dir / "entities.md", "\n".join(by_entity))
    else:
        for stale_index in (config.index_dir / "by-entity.md", config.index_dir / "entities.md"):
            if stale_index.exists():
                stale_index.unlink()

    recently_updated = ["# 最近更新", ""]
    recent_records = sorted(records, key=lambda item: (item.get("date", ""), item.get("compiled_at", "")), reverse=True)[: config.recent_limit]
    ai_recent = text_writer.generate_recent_updates_copy(records=recent_records[:8]) if allow_ai else None
    note_map = {
        item.get("title", ""): item.get("note", "")
        for item in (ai_recent or {}).get("item_notes", [])
        if isinstance(item, dict)
    }
    record_map = {record.get("title", ""): record for record in recent_records}
    if ai_recent:
        top_picks = [item for item in ai_recent.get("top_picks", []) or [] if isinstance(item, dict)]
        if top_picks:
            recently_updated.extend(["## 先看这几篇", ""])
            for item in top_picks:
                record = record_map.get(str(item.get("title", "") or ""))
                if not record:
                    continue
                target = record_link_path(record, config)
                if target is None:
                    continue
                link = markdown_link_target(target, config.index_dir)
                reason = str(item.get("reason", "") or "").strip()
                recently_updated.append(f"### [{record_label(record)}]({link})")
                recently_updated.append("")
                recently_updated.append(f"{record.get('date', 'undated')} · {reason}")
                recently_updated.append("")

        overview = ai_recent.get("overview", []) or []
        if overview:
            recently_updated.extend(["## 这轮更新在讲什么", ""])
            recently_updated.extend([str(item) for item in overview if str(item).strip()])
            recently_updated.append("")

        key_questions = ai_recent.get("key_questions", []) or []
        if key_questions:
            recently_updated.extend(["## 这轮共同在回答的问题", ""])
            recently_updated.extend([f"- {item}" for item in key_questions if str(item).strip()])
            recently_updated.append("")

        reading_path = ai_recent.get("reading_path", []) or []
        if reading_path:
            recently_updated.extend(["## 怎么读更划算", ""])
            recently_updated.extend([f"- {item}" for item in reading_path if str(item).strip()])
            recently_updated.append("")

        recently_updated.extend(["## 其余更新", ""])
    else:
        recently_updated.extend(["下面是最近更新的 source 条目。", "", "## 更新条目", ""])
    top_pick_titles = {
        str(item.get("title", "")).strip()
        for item in (ai_recent or {}).get("top_picks", [])
        if isinstance(item, dict) and str(item.get("title", "")).strip()
    }
    remaining_records = [record for record in recent_records if record.get("title", "") not in top_pick_titles]
    for record in remaining_records:
        target = record_link_path(record, config)
        if target is None:
            continue
        note_text = note_map.get(record["title"], "") or build_source_learning_hint(record)
        recently_updated.append(
            f"- [{record_label(record)}]({markdown_link_target(target, config.index_dir)}): {record.get('date', 'undated')} · {note_text}"
        )
    write_plain_markdown(config.index_dir / "recently-updated.md", "\n".join(recently_updated))
    ai_map = [
        "# AI Map",
        "",
        "## 主入口",
        "",
        "- [Themes](themes.md)",
        "- [Concepts](concepts.md)",
    ]
    if config.generate_entities:
        ai_map.append("- [Entities](entities.md)")
    ai_map.extend(
        [
        "- [Emerging](emerging.md)",
        "- [Review](review.md)",
        "",
        "## 当前热点 Theme",
        "",
        ]
    )
    for name, group_records in sorted(filtered_themes.items(), key=lambda item: (-len(item[1]), item[0]))[:20]:
        theme_file = config.themes_dir / f"{group_slug(name, 'theme')}.md"
        ai_map.append(
            f"- [{display_group_name('theme', name)}]({markdown_link_target(theme_file, config.index_dir)}): {len(group_records)} 篇"
        )
    write_plain_markdown(config.index_dir / "ai-map.md", "\n".join(ai_map))
    emerging = [
        "# Emerging",
        "",
        "正式页面只展示 published 的 theme/concept。候选与 emerging 节点在这里 review：",
        "",
        "- [Emerging Concepts](../_state/review/emerging_concepts.md)",
        "- [Concept Candidates](../_state/review/concept_candidates.md)",
        "- [Taxonomy Candidates](../_state/taxonomy_candidates.md)",
        "- [Concept Lifecycle](../_state/concept_lifecycle.json)",
    ]
    write_plain_markdown(config.index_dir / "emerging.md", "\n".join(emerging))
    review = [
        "# Review",
        "",
        "- [Taxonomy Candidates](../_state/taxonomy_candidates.md)",
        "- [Lint Report](../_state/lint_report.md)",
        "- [Concept Lifecycle](../_state/concept_lifecycle.json)",
    ]
    if config.generate_entities:
        review.append("- [Entity Lifecycle](../_state/entity_lifecycle.json)")
    review.extend(
        [
        "- [Theme Lifecycle](../_state/theme_lifecycle.json)",
        "- [Relation Edges](../_state/relation_edges.json)",
        "- [Taxonomy Decisions](../_state/taxonomy_decisions.yaml)",
        ]
    )
    write_plain_markdown(config.index_dir / "review.md", "\n".join(review))


def write_queries(
    records: list[dict[str, Any]],
    concept_index: dict[str, list[dict[str, Any]]],
    entity_index: dict[str, list[dict[str, Any]]],
    theme_index: dict[str, list[dict[str, Any]]],
    config: AppConfig,
) -> None:
    anchor_date = max(
        (
            parse_flexible_date(record.get("date", "")) or parse_flexible_date(record.get("compiled_at", ""))
            for record in records
        ),
        default=datetime.now().date(),
    )
    recent_cutoff = anchor_date - timedelta(days=max(config.high_signal_window_days - 1, 0))
    in_window_records = [
        record
        for record in records
        if (parse_flexible_date(record.get("date", "")) or parse_flexible_date(record.get("compiled_at", "")) or anchor_date) >= recent_cutoff
    ]
    recent_ranked = sorted(
        in_window_records,
        key=lambda item: (
            item.get("date", ""),
            level_rank(str(item.get("actionability", "medium") or "")),
            level_rank(str(item.get("confidence", "medium") or "")),
            len(item.get("entities", []) or []),
            len(item.get("concepts", []) or []),
        ),
        reverse=True,
    )[:12]
    fallback_ranked = [record for record in sorted(records, key=lambda item: candidate_score(item), reverse=True) if record not in recent_ranked][
        : max(config.recent_limit // 2, 8)
    ]

    repeated_pairs = build_repeated_thesis_candidates(records, config)
    evergreen_candidates = [record for record in sorted(records, key=lambda item: candidate_score(item), reverse=True) if is_evergreen_candidate(record, concept_index, theme_index, config)][:20]
    high_actionability = [
        record
        for record in sorted(
            records,
            key=lambda item: (level_rank(str(item.get("confidence", "medium") or "")), item.get("date", "")),
            reverse=True,
        )
        if str(record.get("actionability", "") or "").lower() == "high"
    ][:20]
    watchlist = build_watchlist(entity_index, config)
    stale_records = [
        record
        for record in sorted(records, key=lambda item: (normalize_iso_date(str(item.get("last_confirmed", "") or "")), item.get("date", "")))
        if is_stale_record(record, config)
    ][:20]

    query_index = "\n".join(
        [
            "# 查询视图",
            "",
            "## 视图",
            "",
            "- [最近信号](recent-signals.md)",
            "- [重复主题](repeated-theses.md)",
            "- [长期笔记候选](evergreen-candidates.md)",
            "- [高行动价值](high-actionability.md)",
            "- [重点对象](watchlist.md)",
            "- [待复核知识](stale-knowledge.md)",
        ]
    )
    config.legacy_queries_dir.mkdir(parents=True, exist_ok=True)
    write_plain_markdown(config.legacy_queries_dir / "README.md", query_index)

    recent_signals = [
        "# 最近信号",
        "",
        f"- 时间窗口：以语料中最新日期 {anchor_date.isoformat()} 为锚点，回看最近 {config.high_signal_window_days} 天。",
        "",
    ]
    recent_signals.extend(["## 时间窗口内的新内容", ""])
    if recent_ranked:
        recent_signals.extend(["- 以下内容只来自当前时间窗口内的材料，并优先按日期排序。", ""])
        for record in recent_ranked:
            recent_signals.extend(
                render_query_record(
                    record,
                    config,
                    include_reason=True,
                    entry_reason=build_recent_signal_reason(record, recent_mode="strict", recent_cutoff=recent_cutoff),
                )
            )
    else:
        recent_signals.extend(["- 当前时间窗口内没有新内容。", ""])
    recent_signals.extend(["## 高价值补位", "", "- 这一部分用于补充时间窗口外但仍值得回看的材料。", ""])
    for record in fallback_ranked:
        recent_signals.extend(
            render_query_record(
                record,
                config,
                include_reason=True,
                entry_reason=build_recent_signal_reason(record, recent_mode="fallback", recent_cutoff=recent_cutoff),
            )
        )
    write_plain_markdown(config.legacy_queries_dir / "recent-signals.md", "\n".join(recent_signals))

    repeated_theses = ["# 重复主题", "", "- 当前语料里跨多篇材料、跨日期反复出现的概念/主题组合。", ""]
    if repeated_pairs:
        for pair in repeated_pairs:
            repeated_theses.append(f"## {pair['label']}")
            repeated_theses.append("")
            repeated_theses.append(f"- 支撑材料：{pair['count']} 篇")
            repeated_theses.append(f"- 覆盖日期：{cn_join(pair['dates'])}")
            repeated_theses.append(f"- 示例标题：{cn_join(pair['titles'])}")
            repeated_theses.append("")
    else:
        repeated_theses.append("- 这一轮没有明显的重复主题。")
    write_plain_markdown(config.legacy_queries_dir / "repeated-theses.md", "\n".join(repeated_theses))

    evergreen_lines = ["# 长期笔记候选", "", "- 只有在多篇材料、多个日期下仍成立的内容，才会进入这里。", ""]
    for record in evergreen_candidates:
        evergreen_lines.extend(
            render_query_record(
                record,
                config,
                include_reason=True,
                entry_reason=build_evergreen_reason(record, concept_index, theme_index, config),
            )
        )
    if len(evergreen_lines) == 4:
        evergreen_lines.append("- 这一轮没有足够稳定的长期笔记候选。")
    write_plain_markdown(config.legacy_queries_dir / "evergreen-candidates.md", "\n".join(evergreen_lines))

    action_lines = ["# 高行动价值", "", "- 值得测试、动手或继续跟进的 source。", ""]
    for record in high_actionability:
        action_lines.extend(
            render_query_record(
                record,
                config,
                include_reason=True,
                entry_reason=build_actionability_reason(record),
            )
        )
    if len(action_lines) == 4:
        action_lines.append("- 这一轮没有高行动价值条目。")
    write_plain_markdown(config.legacy_queries_dir / "high-actionability.md", "\n".join(action_lines))

    watchlist_entries = [
        (entity_name, count, entity_type, "")
        for entity_name, count, _sample_titles, entity_type in watchlist
    ]
    grouped_watchlist = group_entity_entries(watchlist_entries)
    sample_map = {entity_name: sample_titles for entity_name, _count, sample_titles, _entity_type in watchlist}
    watchlist_lines = ["# 重点对象", "", "- 当前语料里值得持续跟踪的实体对象。", ""]
    for group_label, items in grouped_watchlist:
        watchlist_lines.extend([f"## {group_label}", ""])
        for entity_name, count, entity_type, _target in items:
            sample_titles = sample_map.get(entity_name, [])
            watchlist_lines.append(f"### {entity_name}")
            watchlist_lines.append("")
            if entity_type:
                watchlist_lines.append(f"- 类型：{translate_entity_type(entity_type)}")
            watchlist_lines.append(f"- 提及次数：{count}")
            watchlist_lines.append("- 进入原因：在当前语料里重复出现，具备持续跟踪价值。")
            if sample_titles:
                watchlist_lines.append(f"- 示例标题：{cn_join(sample_titles)}")
            watchlist_lines.append("")
    if len(watchlist_lines) == 4:
        watchlist_lines.append("- 这一轮没有重点对象。")
    write_plain_markdown(config.legacy_queries_dir / "watchlist.md", "\n".join(watchlist_lines))

    stale_lines = ["# 待复核知识", "", f"- 已超过 {config.stale_days} 天未确认，或复核日期已到的记录。", ""]
    for record in stale_records:
        stale_lines.extend(
            render_query_record(
                record,
                config,
                include_reason=True,
                entry_reason=build_stale_reason(record, config),
            )
        )
    if len(stale_lines) == 4:
        stale_lines.append("- 这一轮没有待复核知识。")
    write_plain_markdown(config.legacy_queries_dir / "stale-knowledge.md", "\n".join(stale_lines))


def build_repeated_thesis_candidates(records: list[dict[str, Any]], config: AppConfig) -> list[dict[str, Any]]:
    pair_counter: Counter[tuple[str, str]] = Counter()
    pair_examples: dict[tuple[str, str], list[str]] = defaultdict(list)
    pair_dates: dict[tuple[str, str], set[str]] = defaultdict(set)
    for record in records:
        concepts = list(record.get("concepts", []) or [])[:4]
        themes = list(record.get("themes", []) or [])[:4]
        for concept_name in concepts:
            for theme_name in themes:
                key = (concept_name, theme_name)
                pair_counter[key] += 1
                label = record_label(record)
                if label not in pair_examples[key]:
                    pair_examples[key].append(label)
                record_date = str(record.get("date", "") or "").strip()
                if record_date:
                    pair_dates[key].add(record_date)
    results: list[dict[str, Any]] = []
    for (concept_name, theme_name), count in pair_counter.most_common(20):
        if count < config.evergreen_min_sources:
            continue
        if len(pair_dates[(concept_name, theme_name)]) < 2:
            continue
        results.append(
            {
                "label": f"{display_group_name('concept', concept_name)} x {display_group_name('theme', theme_name)}",
                "count": count,
                "dates": sorted(pair_dates[(concept_name, theme_name)], reverse=True)[:4],
                "titles": pair_examples[(concept_name, theme_name)][:4],
            }
        )
    return results[:10]


def level_rank(value: str) -> int:
    normalized = str(value or "").strip().lower()
    if normalized == "high":
        return 3
    if normalized == "medium":
        return 2
    if normalized == "low":
        return 1
    return 0


def candidate_score(record: dict[str, Any]) -> tuple[int, int, int, str]:
    return (
        level_rank(str(record.get("actionability", "medium") or "")) + level_rank(str(record.get("confidence", "medium") or "")),
        len(record.get("concepts", []) or []) + len(record.get("themes", []) or []),
        len(record.get("entities", []) or []),
        str(record.get("date", "") or ""),
    )


def build_recent_signal_reason(record: dict[str, Any], *, recent_mode: str, recent_cutoff: date) -> str:
    record_date = parse_flexible_date(str(record.get("date", "") or ""))
    actionability = str(record.get("actionability", "medium") or "medium")
    confidence = str(record.get("confidence", "medium") or "medium")
    if recent_mode == "strict" and record_date:
        return f"原始日期在 {recent_cutoff.isoformat()} 之后，且行动价值={actionability}、可信度={confidence}。"
    return f"作为时间窗口外的高价值补位入选：行动价值={actionability}、可信度={confidence}。"


def supporting_group_matches(
    record: dict[str, Any],
    concept_index: dict[str, list[dict[str, Any]]],
    theme_index: dict[str, list[dict[str, Any]]],
    config: AppConfig,
) -> list[str]:
    matches: list[str] = []
    for concept_name in record.get("concepts", []) or []:
        concept_records = dedupe_records(concept_index.get(concept_name, []))
        distinct_dates = {
            str(item.get("date", "")).strip()
            for item in concept_records
            if str(item.get("date", "")).strip()
        }
        if len(concept_records) >= config.evergreen_min_sources and len(distinct_dates) >= 2:
            matches.append(f"概念 {concept_name}（{len(concept_records)} 篇，{len(distinct_dates)} 个日期）")
    for theme_name in record.get("themes", []) or []:
        theme_records = dedupe_records(theme_index.get(theme_name, []))
        distinct_dates = {
            str(item.get("date", "")).strip()
            for item in theme_records
            if str(item.get("date", "")).strip()
        }
        if len(theme_records) >= config.evergreen_min_sources and len(distinct_dates) >= 2:
            matches.append(f"主题 {theme_name}（{len(theme_records)} 篇，{len(distinct_dates)} 个日期）")
    return dedupe_preserve(matches)


def is_evergreen_candidate(
    record: dict[str, Any],
    concept_index: dict[str, list[dict[str, Any]]],
    theme_index: dict[str, list[dict[str, Any]]],
    config: AppConfig,
) -> bool:
    if str(record.get("source_kind", "") or "") == "post_batch":
        return False
    if str(record.get("confidence", "") or "").lower() == "low":
        return False
    if str(record.get("content_type", "") or "").lower() == "opinion":
        return False
    if str(record.get("content_type", "") or "").lower() not in {"thesis", "tactic"}:
        return False
    if not record.get("concepts") and not record.get("themes"):
        return False
    if level_rank(str(record.get("actionability", "medium") or "")) < 2:
        return False
    return bool(supporting_group_matches(record, concept_index, theme_index, config))


def is_stale_record(record: dict[str, Any], config: AppConfig) -> bool:
    last_confirmed = parse_flexible_date(str(record.get("last_confirmed", "") or ""))
    if last_confirmed:
        return (datetime.now().date() - last_confirmed).days >= config.stale_days
    revisit_after = parse_flexible_date(str(record.get("revisit_after", "") or ""))
    if not revisit_after:
        return False
    return datetime.now().date() >= revisit_after


def build_evergreen_reason(
    record: dict[str, Any],
    concept_index: dict[str, list[dict[str, Any]]],
    theme_index: dict[str, list[dict[str, Any]]],
    config: AppConfig,
) -> str:
    matches = supporting_group_matches(record, concept_index, theme_index, config)
    if matches:
        return f"内容类型={record.get('content_type', 'unknown')}，且被 {cn_join(matches[:2])} 跨日期重复支撑。"
    return "满足长期笔记候选的最低门槛。"


def build_actionability_reason(record: dict[str, Any]) -> str:
    return (
        f"行动价值={record.get('actionability', 'medium')}，"
        f"下一步={record.get('next_action', 'track')}，"
        f"可信度={record.get('confidence', 'medium')}。"
    )


def build_stale_reason(record: dict[str, Any], config: AppConfig) -> str:
    last_confirmed = parse_flexible_date(str(record.get("last_confirmed", "") or ""))
    if last_confirmed:
        overdue_days = (datetime.now().date() - last_confirmed).days
        return f"距离上次确认已 {overdue_days} 天，超过 {config.stale_days} 天阈值。"
    revisit_after = str(record.get("revisit_after", "") or "").strip()
    if revisit_after:
        return f"预定复看日期 {revisit_after} 已到，但还没有新的确认记录。"
    return "缺少有效确认记录。"


def query_text(value: Any, fallback: str = "待补充。") -> str:
    if isinstance(value, list):
        cleaned = [str(item).strip() for item in value if str(item).strip()]
        return " ".join(cleaned) if cleaned else fallback
    text = str(value or "").strip()
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if inner.startswith("'") and inner.endswith("'"):
            inner = inner[1:-1]
            return inner.strip() or fallback
        if inner.startswith('"') and inner.endswith('"'):
            inner = inner[1:-1]
            return inner.strip() or fallback
    return text or fallback


def render_query_record(record: dict[str, Any], config: AppConfig, *, include_reason: bool, entry_reason: str = "") -> list[str]:
    target = record_link_path(record, config)
    if target is None:
        return []
    rel = markdown_link_target(target, config.legacy_queries_dir)
    lines = [
        f"## [{record_label(record)}]({rel})",
        "",
        f"- 日期：{record.get('date', 'undated')}",
        f"- 类型：{record.get('content_type', 'signal')}",
        f"- 状态：{record.get('status', 'inbox')}",
        f"- 行动价值：{record.get('actionability', 'medium')}",
        f"- 可信度：{record.get('confidence', 'medium')}",
        f"- 下一步：{record.get('next_action', 'track')}",
    ]
    if entry_reason:
        lines.append(f"- 进入原因：{entry_reason}")
    if include_reason:
        lines.append(f"- 为什么值得看：{query_text(record.get('why_it_matters'))}")
    if record.get("last_seen"):
        lines.append(f"- 最近出现：{record.get('last_seen')}")
    lines.append(f"- 最近确认：{record.get('last_confirmed') or '待复核'}")
    if record.get("revisit_after"):
        lines.append(f"- 建议复看：{record.get('revisit_after')}")
    lines.append("")
    return lines


def build_watchlist(entity_index: dict[str, list[dict[str, Any]]], config: AppConfig) -> list[tuple[str, int, list[str], str]]:
    results: list[tuple[str, int, list[str], str]] = []
    priority_types = {"company", "product", "project", "organization", "person"}
    for name, group_records in sorted(entity_index.items(), key=lambda item: (-len(dedupe_records(item[1])), item[0])):
        deduped = dedupe_records(group_records)
        if not (config.entities_dir / f"{group_slug(name, 'entity')}.md").exists():
            continue
        count = len(deduped)
        entity_type = common_entity_type(name, deduped)
        if entity_type and entity_type.lower() not in priority_types and count < 3:
            continue
        sample_records = primary_entity_records(name, deduped)
        if not sample_records:
            sample_records = sort_records_for_group("entity", name, deduped)[:3]
        sample_titles = [record_label(record) for record in sample_records[:3]]
        results.append((name, count, sample_titles, entity_type))
        if len(results) >= 15:
            break
    return results


def entity_group_label(entity_type: str, name: str = "") -> str:
    normalized = str(entity_type or "").strip().lower()
    if normalized in {"company", "organization"}:
        return "公司 / 组织"
    if normalized == "person":
        return "人物"
    if normalized in {"project", "protocol", "standard"}:
        return "项目 / 协议"
    if normalized in {"browser", "platform"}:
        return "平台 / 浏览器"
    if normalized == "product":
        lowered_name = str(name or "").strip().lower()
        if lowered_name in {"chrome", "x"}:
            return "平台 / 浏览器"
        return "产品"
    return "其他"


def group_entity_entries(
    entries: list[tuple[str, int, str, str]],
) -> list[tuple[str, list[tuple[str, int, str, str]]]]:
    grouped: dict[str, list[tuple[str, int, str, str]]] = defaultdict(list)
    for name, count, entity_type, target in entries:
        grouped[entity_group_label(entity_type, name)].append((name, count, entity_type, target))
    order = [
        "公司 / 组织",
        "产品",
        "平台 / 浏览器",
        "项目 / 协议",
        "人物",
        "其他",
    ]
    results: list[tuple[str, list[tuple[str, int, str, str]]]] = []
    for label in order:
        items = grouped.get(label, [])
        if not items:
            continue
        items = sorted(items, key=lambda item: (-item[1], item[0]))
        results.append((label, items))
    return results


def write_lint_report(
    records: list[dict[str, Any]],
    concept_index: dict[str, list[dict[str, Any]]],
    entity_index: dict[str, list[dict[str, Any]]],
    theme_index: dict[str, list[dict[str, Any]]],
    config: AppConfig,
) -> None:
    critical_findings: list[str] = []
    review_findings: list[str] = []
    long_tail_notes: list[str] = []
    if not records:
        critical_findings.append("- 本轮没有编译出任何 source notes。")

    title_counter = Counter(record["title"] for record in records)
    for title, count in title_counter.items():
        if count > 1:
            critical_findings.append(f"- 存在重复 source 标题：{title}（{count} 次）")

    url_counter = Counter(
        str(record.get("canonical_url", "") or "").strip()
        for record in records
        if str(record.get("canonical_url", "") or "").strip()
    )
    for canonical_url, count in url_counter.items():
        if count > 1:
            critical_findings.append(f"- 存在重复 canonical URL：{canonical_url}（{count} 次）")

    for record in records:
        if not record_note_exists(record, config):
            target = record_link_path(record, config)
            missing = str(target or record.get("note_path", "") or record.get("source_path", ""))
            critical_findings.append(f"- 缺少可读 source 链接：{missing}")
        if looks_like_blocked_extraction(record):
            critical_findings.append(f"- 疑似抽取失败或被拦截，建议回源清洗：{record_label(record)}")
        if not record.get("concepts") and level_rank(str(record.get("actionability", "low") or "")) >= 2:
            review_findings.append(f"- 高价值条目没有概念标签：{record_label(record)}")
        if not record.get("themes") and level_rank(str(record.get("actionability", "low") or "")) >= 2:
            review_findings.append(f"- 高价值条目没有主题标签：{record_label(record)}")
        if str(record.get("actionability", "") or "").lower() == "high" and not str(record.get("why_it_matters", "") or "").strip():
            review_findings.append(f"- 高行动价值条目缺少“为什么值得看”：{record_label(record)}")
        if is_stale_record(record, config):
            review_findings.append(f"- 已到复看窗口，建议复核：{record_label(record)}")

    one_off_concepts: list[str] = []
    for name, group_records in concept_index.items():
        if len(dedupe_records(group_records)) == 1:
            one_off_concepts.append(display_group_name("concept", name))
    one_off_entities: list[str] = []
    if config.generate_entities:
        for name, group_records in entity_index.items():
            deduped = dedupe_records(group_records)
            if len(deduped) == 1:
                one_off_entities.append(name)
            elif not (config.entities_dir / f"{group_slug(name, 'entity')}.md").exists():
                review_findings.append(f"- 实体提及偏弱，暂不建议生成独立对象页：{name}")
    one_off_themes: list[str] = []
    for name, group_records in theme_index.items():
        if len(dedupe_records(group_records)) == 1:
            one_off_themes.append(display_group_name("theme", name))

    if config.generate_entities:
        entity_variants: dict[str, set[str]] = defaultdict(set)
        for entity_name in entity_index:
            entity_variants[entity_name.lower()].add(entity_name)
        for lowered, variants in entity_variants.items():
            if len(variants) > 1:
                review_findings.append(f"- 实体别名或大小写归一化可能未完成：{comma_list(sorted(variants))}")

    if one_off_concepts:
        long_tail_notes.append(
            f"- 只出现 1 次的概念共 {len(one_off_concepts)} 个，示例：{cn_join(one_off_concepts[:6])}。"
        )
    if config.generate_entities and one_off_entities:
        long_tail_notes.append(
            f"- 只出现 1 次的实体共 {len(one_off_entities)} 个，示例：{cn_join(one_off_entities[:10])}。"
        )
    if one_off_themes:
        long_tail_notes.append(
            f"- 只出现 1 次的主题共 {len(one_off_themes)} 个，示例：{cn_join(one_off_themes[:6])}。"
        )

    report = "\n".join(
        [
            "# Wiki Lint Report",
            "",
            f"- Generated at: {now_iso()}",
            f"- Source notes checked: {len(records)}",
            "",
            "## 必须先处理",
            "",
            *(critical_findings or ["- 这一轮没有阻塞性结构问题。"]),
            "",
            "## 建议尽快处理",
            "",
            *(review_findings or ["- 这一轮没有高优复查项。"]),
            "",
            "## 长尾观察",
            "",
            *(long_tail_notes or ["- 这一轮没有明显的长尾归一化噪声。"]),
        ]
    )
    write_plain_markdown(config.state_dir / "lint_report.md", report)


def write_taxonomy_candidate_report(
    records: list[dict[str, Any]],
    concept_index: dict[str, list[dict[str, Any]]],
    entity_index: dict[str, list[dict[str, Any]]],
    theme_index: dict[str, list[dict[str, Any]]],
    concept_lifecycle: dict[str, dict[str, Any]],
    entity_lifecycle: dict[str, dict[str, Any]],
    config: AppConfig,
) -> None:
    theme_candidates = collect_theme_candidate_stats(records)
    concept_variants = taxonomy_variant_groups(concept_index)
    theme_variants = taxonomy_variant_groups(theme_index)
    lifecycle_counts = Counter(str(item.get("status", CONCEPT_STATUS_CANDIDATE)) for item in concept_lifecycle.values())
    low_frequency_concepts = [
        (
            name,
            len(dedupe_records(group_records)),
            str(concept_lifecycle.get(name, {}).get("status", CONCEPT_STATUS_CANDIDATE)),
            bool(concept_lifecycle.get(name, {}).get("ai_related")),
        )
        for name, group_records in concept_index.items()
        if str(concept_lifecycle.get(name, {}).get("status", CONCEPT_STATUS_CANDIDATE)) == CONCEPT_STATUS_CANDIDATE
    ]
    low_frequency_concepts.sort(key=lambda item: (not item[3], -item[1], item[0]))

    lines = [
        "# Taxonomy Candidates",
        "",
        f"- Generated at: {now_iso()}",
        f"- Approved themes: {len(GROUP_DISPLAY_NAME_MAP.get('theme', {}))}",
        "- AI priority: AI-related concepts get lower promotion thresholds and appear first in review queues.",
        f"- Concept lifecycle: published={lifecycle_counts.get(CONCEPT_STATUS_PUBLISHED, 0)}, emerging={lifecycle_counts.get(CONCEPT_STATUS_EMERGING, 0)}, candidate={lifecycle_counts.get(CONCEPT_STATUS_CANDIDATE, 0)}, deprecated={lifecycle_counts.get(CONCEPT_STATUS_DEPRECATED, 0)}, rejected={lifecycle_counts.get(CONCEPT_STATUS_REJECTED, 0)}",
        "",
        "## Theme 候选",
        "",
        "这些词来自模型输出，但未进入正式 theme。它们应被合并到现有 theme，或在足够稳定后人工晋升。",
        "",
    ]
    if theme_candidates:
        for name, payload in sorted(theme_candidates.items(), key=lambda item: (-item[1]["count"], item[0]))[:80]:
            dates = cn_join(sorted(payload["dates"], reverse=True)[:4])
            samples = "；".join(payload["samples"][:3])
            lines.append(f"### {name}")
            lines.append("")
            lines.append(f"- 出现次数：{payload['count']}")
            lines.append(f"- 覆盖日期：{dates or '未知'}")
            lines.append(f"- 建议归属：{suggest_theme_for_candidate(name)}")
            lines.append(f"- 样例：{samples or '无'}")
            lines.append("")
    else:
        lines.extend(["- 暂无新的 theme 候选。", ""])

    lines.extend(["## Concept 生命周期", ""])
    for status, title in [
        (CONCEPT_STATUS_PUBLISHED, "Published"),
        (CONCEPT_STATUS_EMERGING, "Emerging"),
        (CONCEPT_STATUS_CANDIDATE, "Candidate"),
        (CONCEPT_STATUS_DEPRECATED, "Deprecated"),
        (CONCEPT_STATUS_REJECTED, "Rejected"),
    ]:
        items = [
            (name, payload)
            for name, payload in concept_lifecycle.items()
            if str(payload.get("status")) == status
        ]
        items.sort(key=lambda item: (not bool(item[1].get("ai_related")), -int(item[1].get("source_count", 0) or 0), item[0]))
        lines.extend([f"### {title}", ""])
        if not items:
            lines.extend(["- 暂无。", ""])
            continue
        for name, payload in items[:40]:
            suffix = f" -> {payload.get('canonical')}" if status == CONCEPT_STATUS_DEPRECATED and payload.get("canonical") else ""
            ai_flag = " · AI priority" if payload.get("ai_related") else ""
            lines.append(
                f"- {name}{suffix}: {payload.get('source_count', 0)} source / {payload.get('date_count', 0)} dates · {payload.get('reason', '')}{ai_flag}"
            )
        lines.append("")

    lines.extend(["## Concept 近似重复", ""])
    if concept_variants:
        for key, names in concept_variants[:80]:
            lines.append(f"- {', '.join(names)}")
    else:
        lines.append("- 暂无明显重复 concept。")

    lines.extend(["", "## Theme 近似重复", ""])
    if theme_variants:
        for key, names in theme_variants[:80]:
            lines.append(f"- {', '.join(names)}")
    else:
        lines.append("- 暂无明显重复 theme。")

    lines.extend(["", "## 低频 Concept 候选", ""])
    if low_frequency_concepts:
        for name, count, status, ai_related in low_frequency_concepts[:120]:
            ai_flag = " · AI priority" if ai_related else ""
            lines.append(f"- {name}: {count} source · {status}{ai_flag}")
    else:
        lines.append("- 暂无低频 concept 候选。")

    if config.generate_entities:
        entity_variants = entity_variant_groups(entity_index)
        low_frequency_entities = [
            (
                name,
                len(dedupe_records(group_records)),
                str(entity_lifecycle.get(name, {}).get("status", CONCEPT_STATUS_CANDIDATE)),
                is_ai_related_entity(name, dedupe_records(group_records)),
            )
            for name, group_records in entity_index.items()
            if str(entity_lifecycle.get(name, {}).get("status", CONCEPT_STATUS_CANDIDATE)) != CONCEPT_STATUS_PUBLISHED
        ]
        low_frequency_entities.sort(key=lambda item: (not item[3], -item[1], item[0]))
        lines.extend(["", "## Entity 大小写/别名候选", ""])
        if entity_variants:
            for key, names in entity_variants[:80]:
                lines.append(f"- {', '.join(names)}")
        else:
            lines.append("- 暂无明显大小写/别名重复 entity。")
        lines.extend(["", "## 低频 Entity 候选", ""])
        if low_frequency_entities:
            for name, count, status, ai_related in low_frequency_entities[:120]:
                ai_flag = " · AI priority" if ai_related else ""
                lines.append(f"- {name}: {count} source · {status}{ai_flag}")
        else:
            lines.append("- 暂无低频 entity 候选。")

    write_plain_markdown(config.state_dir / "taxonomy_candidates.md", "\n".join(lines))
    review_dir = config.state_dir / "review"
    review_dir.mkdir(parents=True, exist_ok=True)
    concept_candidates = ["# Concept Candidates", ""]
    emerging_concepts = ["# Emerging Concepts", ""]
    for name, payload in sorted(concept_lifecycle.items(), key=lambda item: (-int(item[1].get("source_count", 0) or 0), item[0])):
        line = f"- {name}: {payload.get('source_count', 0)} sources / {payload.get('date_count', 0)} dates · {payload.get('reason', '')}"
        status = str(payload.get("status", ""))
        if status == CONCEPT_STATUS_CANDIDATE:
            concept_candidates.append(line)
        elif status == CONCEPT_STATUS_EMERGING:
            emerging_concepts.append(line)
    write_plain_markdown(review_dir / "concept_candidates.md", "\n".join(concept_candidates if len(concept_candidates) > 2 else concept_candidates + ["- 暂无。"]))
    write_plain_markdown(review_dir / "emerging_concepts.md", "\n".join(emerging_concepts if len(emerging_concepts) > 2 else emerging_concepts + ["- 暂无。"]))

    if config.generate_entities:
        emerging_entities = ["# Emerging Entities", ""]
        entity_anomalies = ["# Entity Anomalies", ""]
        for name, payload in sorted(entity_lifecycle.items(), key=lambda item: (-int(item[1].get("source_count", 0) or 0), item[0])):
            line = f"- {name}: {payload.get('source_count', 0)} sources / {payload.get('date_count', 0)} dates · {payload.get('entity_type', '')} · {payload.get('reason', '')}"
            status = str(payload.get("status", ""))
            if status == CONCEPT_STATUS_EMERGING:
                emerging_entities.append(line)
            elif status in {CONCEPT_STATUS_REJECTED, CONCEPT_STATUS_MERGED}:
                entity_anomalies.append(line)
        write_plain_markdown(review_dir / "emerging_entities.md", "\n".join(emerging_entities if len(emerging_entities) > 2 else emerging_entities + ["- 暂无。"]))
        write_plain_markdown(review_dir / "entity_anomalies.md", "\n".join(entity_anomalies if len(entity_anomalies) > 2 else entity_anomalies + ["- 暂无。"]))


def collect_theme_candidate_stats(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}
    for record in records:
        for candidate in record.get("theme_candidates", []) or []:
            name = normalize_theme_candidate_name(str(candidate or ""))
            if not name:
                continue
            item = stats.setdefault(name, {"count": 0, "dates": set(), "samples": []})
            item["count"] += 1
            date_value = str(record.get("date", "") or "").strip()
            if date_value:
                item["dates"].add(date_value)
            label = record_label(record)
            if label and label not in item["samples"]:
                item["samples"].append(label)
    return stats


def taxonomy_variant_groups(index: dict[str, list[dict[str, Any]]]) -> list[tuple[str, list[str]]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for name in index:
        key = singular_taxonomy_key(name)
        if key:
            grouped[key].append(name)
    variants = [
        (key, sorted(names))
        for key, names in grouped.items()
        if len(set(names)) > 1
    ]
    variants.sort(key=lambda item: (-len(item[1]), item[0]))
    return variants


def entity_variant_groups(index: dict[str, list[dict[str, Any]]]) -> list[tuple[str, list[str]]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for name in index:
        key = entity_name_key(name)
        if key:
            grouped[key].append(name)
    variants = [
        (key, sorted(names))
        for key, names in grouped.items()
        if len(set(names)) > 1
    ]
    variants.sort(key=lambda item: (-len(item[1]), item[0]))
    return variants


def singular_taxonomy_key(value: str) -> str:
    words = []
    for word in taxonomy_name_key(value).split():
        if len(word) > 4 and word.endswith("s"):
            word = word[:-1]
        words.append(word)
    return " ".join(words)


def suggest_theme_for_candidate(name: str) -> str:
    key = taxonomy_name_key(name)
    if key in THEME_CANONICAL_MAP:
        return THEME_CANONICAL_MAP[key]
    keyword_rules = [
        (("code", "coding", "developer", "vibe", "programming"), "AI Coding Tools"),
        (("agent", "agentic", "workflow", "automation"), "Agent Systems"),
        (("business", "pricing", "market", "monetization", "commerce"), "AI Business"),
        (("startup", "venture", "funding", "investor"), "Venture Capital"),
        (("inference", "gpu", "infrastructure", "latency", "serving", "optimization"), "AI Infrastructure"),
        (("hardware", "chip", "semiconductor", "accelerator"), "AI Hardware"),
        (("safety", "alignment", "risk"), "AI Safety"),
        (("governance", "policy", "regulation", "legal"), "AI Governance"),
        (("security", "attack", "defense", "privacy"), "AI Security"),
        (("ethic", "bias", "fairness"), "AI Ethics"),
        (("research", "paper", "benchmark", "science"), "AI Research"),
        (("eval", "evaluation", "leaderboard"), "Model Evaluation"),
        (("training", "data", "dataset", "synthetic"), "Data And Training"),
        (("multimodal", "vision", "audio", "speech"), "Multimodal AI"),
        (("image", "video", "creative", "content"), "Creative AI"),
        (("enterprise", "workplace", "corporate"), "Enterprise AI"),
        (("consumer", "personal", "assistant"), "Consumer AI"),
        (("education", "learning", "tutor"), "AI Education"),
        (("robot", "robotics", "embodied"), "Robotics"),
        (("open source", "community", "github"), "Open Source Ecosystems"),
        (("context", "prompt", "rag", "retrieval", "knowledge"), "Knowledge Operations"),
    ]
    for keywords, theme in keyword_rules:
        if any(keyword in key for keyword in keywords):
            return theme
    return "Needs Review"


def extract_heading(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def parse_article_metadata(text: str) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r"- \*\*(.+?)\*\*: (.+)", line.strip())
        if match:
            metadata[match.group(1).strip()] = match.group(2).strip()
    return metadata


def extract_article_body(text: str) -> str:
    marker = "## 正文"
    if marker in text:
        return text.split(marker, 1)[1].strip()
    return text


def clean_text(text: str) -> str:
    lines = []
    seen_recent: set[str] = set()
    for raw in text.splitlines():
        line = normalize_source_line(raw)
        if not line:
            lines.append("")
            continue
        if line.startswith("![]("):
            continue
        if re.fullmatch(r"https?://\S+", line):
            continue
        if is_source_boilerplate_line(line):
            continue
        line = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", line)
        if "•" in line and not line.lstrip().startswith("•"):
            bullet_parts = [normalize_source_line(part) for part in re.split(r"[•●]", line) if normalize_source_line(part)]
            if len(bullet_parts) >= 2:
                if lines and lines[-1] != "":
                    lines.append("")
                for part in bullet_parts:
                    lines.append(f"• {part}")
                continue
        dedupe_key = canonical_text_key(line)
        if dedupe_key and dedupe_key in seen_recent:
            continue
        if dedupe_key:
            seen_recent.add(dedupe_key)
        lines.append(line)
    return "\n".join(lines)


def split_paragraphs(text: str) -> list[str]:
    paragraphs: list[str] = []
    current: list[str] = []
    current_heading = ""

    def flush_current() -> None:
        nonlocal current
        if current:
            combined = " ".join(current).strip()
            combined = attach_heading_context(current_heading, combined)
            if is_useful_paragraph(combined):
                paragraphs.append(combined)
            current = []

    for raw_line in text.splitlines():
        line = normalize_source_line(raw_line)
        if not line:
            flush_current()
            continue
        if is_heading_line(line):
            flush_current()
            current_heading = strip_heading_marker(line)
            continue
        if is_list_line(line):
            flush_current()
            paragraph = attach_heading_context(current_heading, line)
            if is_useful_paragraph(paragraph):
                paragraphs.append(paragraph)
            continue
        if len(line) >= 40:
            flush_current()
            paragraph = attach_heading_context(current_heading, line)
            if is_useful_paragraph(paragraph):
                paragraphs.append(paragraph)
            continue
        current.append(line)
        if ends_sentence(line) and len(" ".join(current)) >= 60:
            flush_current()

    flush_current()
    return dedupe_paragraphs([truncate_line(item, 360) for item in paragraphs if len(item.strip()) >= 24])


def summarize_paragraphs(paragraphs: list[str], *, max_items: int) -> list[str]:
    if not paragraphs:
        return ["No summary was extracted from the source body."]
    scored = sorted(
        paragraphs,
        key=lambda item: (
            summary_score(item),
            len(item),
        ),
        reverse=True,
    )
    selected = select_distinct_lines(scored, limit=max_items, max_len=220, prefer_unique_section=True)
    return selected or [truncate_line(item, 220) for item in paragraphs[:max_items]]


def build_key_signals(paragraphs: list[str], *, max_items: int) -> list[str]:
    scored = sorted(
        paragraphs,
        key=lambda item: (
            key_signal_score(item),
            len(item),
        ),
        reverse=True,
    )
    selected = select_distinct_lines(scored, limit=max_items, max_len=220, prefer_unique_section=False)
    return selected or summarize_paragraphs(paragraphs, max_items=max_items)


def build_open_questions(title: str, concepts: list[str], entities: list[str]) -> list[str]:
    questions = [f"What changed most recently around {title}?"]
    if concepts:
        questions.append(f"Which other source notes deepen the theme of {concepts[0]}?")
    if entities:
        questions.append(f"How is {entities[0]} connected to the rest of this corpus?")
    return questions[:3]


def build_source_knowledge_fields(
    *,
    source_kind: str,
    title: str,
    canonical_url: str,
    concepts: list[str],
    entities: list[str],
    themes: list[str],
    summary: list[str],
    key_signals: list[str],
    text: str,
    config: AppConfig,
) -> dict[str, str]:
    content_type = infer_content_type(source_kind=source_kind, title=title, text=text)
    actionability = infer_actionability(
        source_kind=source_kind,
        title=title,
        content_type=content_type,
        concepts=concepts,
        themes=themes,
    )
    confidence = infer_confidence(
        source_kind=source_kind,
        title=title,
        canonical_url=canonical_url,
    )
    first_seen = today_date()
    last_seen = today_date()
    revisit_after = shift_iso_date(last_seen, config.review_days)
    next_action = infer_next_action(
        source_kind=source_kind,
        content_type=content_type,
        actionability=actionability,
        concepts=concepts,
        themes=themes,
    )
    why_it_matters = build_why_it_matters(
        source_kind=source_kind,
        concepts=concepts,
        entities=entities,
        themes=themes,
        summary=summary,
        key_signals=key_signals,
    )
    return {
        "content_type": content_type,
        "status": "inbox",
        "why_it_matters": why_it_matters,
        "my_take": "",
        "actionability": actionability,
        "confidence": confidence,
        "first_seen": first_seen,
        "last_seen": last_seen,
        "last_confirmed": "",
        "revisit_after": revisit_after,
        "next_action": next_action,
    }


def render_source_note_body(
    *,
    title: str,
    display_title: str,
    summary: list[str],
    key_signals: list[str],
    knowledge_fields: dict[str, str],
    note_path: Path,
    concept_names: list[str],
    entity_names: list[str],
    theme_names: list[str],
    open_questions: list[str],
    metadata_lines: list[str],
    config: AppConfig,
    extra_sections: list[str] | None = None,
) -> str:
    normalized_metadata_lines = normalize_source_metadata_lines(metadata_lines)
    return "\n".join(
        [
            f"# {display_title or title}",
            "",
            "## 总结",
            "",
            *[f"- {item}" for item in summary],
            "",
            "## 关键信号",
            "",
            *[f"- {item}" for item in key_signals],
            "",
            "## 为什么值得看",
            "",
            f"- {knowledge_fields.get('why_it_matters') or '待补个人判断。'}",
            "",
            "## 我的判断",
            "",
            f"- {knowledge_fields.get('my_take') or '待补个人判断。'}",
            "",
            "## 行动",
            "",
            f"- **状态**: {knowledge_fields.get('status', 'inbox')}",
            f"- **行动价值**: {knowledge_fields.get('actionability', 'medium')}",
            f"- **可信度**: {knowledge_fields.get('confidence', 'medium')}",
            f"- **下一步**: {knowledge_fields.get('next_action', 'track')}",
            f"- **首次出现**: {knowledge_fields.get('first_seen', today_date())}",
            f"- **最近出现**: {knowledge_fields.get('last_seen', today_date())}",
            f"- **最近确认**: {knowledge_fields.get('last_confirmed') or '待复核'}",
            f"- **建议复看**: {knowledge_fields.get('revisit_after', today_date())}",
            "",
            *(extra_sections or []),
            *([] if not extra_sections else [""]),
            "## 相关概念",
            "",
            *wiki_links(concept_names, note_path.parent, config.concepts_dir),
            *(
                [
                    "",
                    "## 相关对象",
                    "",
                    *wiki_links(entity_names, note_path.parent, config.entities_dir),
                ]
                if config.generate_entities
                else []
            ),
            "",
            "## 相关主题",
            "",
            *wiki_links(theme_names, note_path.parent, config.themes_dir),
            "",
            "## 开放问题",
            "",
            *[f"- {item}" for item in open_questions],
            "",
            "## 来源元数据",
            "",
            f"- **原始标题**: {title}",
            *(normalized_metadata_lines or ["- 暂无"]),
        ]
    )


def infer_content_type(*, source_kind: str, title: str, text: str) -> str:
    if source_kind == "post_batch":
        return "signal"
    lowered = f"{title}\n{text}".lower()
    if any(token in lowered for token in ("guide", "tutorial", "how to", "playbook", "best practice", "指南", "教程", "实战", "技巧", "经验")):
        return "tactic"
    if any(token in lowered for token in ("opinion", "commentary", "观点", "看法", "点评")):
        return "opinion"
    if any(token in lowered for token in ("why", "framework", "strategy", "thesis", "本质", "范式", "趋势", "未来", "革命")):
        return "thesis"
    if any(token in lowered for token in ("report", "survey", "benchmark", "white paper", "paper", "dataset", "报告", "白皮书")):
        return "fact"
    return "signal"


def infer_actionability(
    *,
    source_kind: str,
    title: str,
    content_type: str,
    concepts: list[str],
    themes: list[str],
) -> str:
    if source_kind == "post_batch":
        return "low"
    lowered = title.lower()
    if content_type == "tactic":
        return "high"
    if any(
        token in lowered
        for token in ("plugin", "tool", "workflow", "automation", "skill", "sdk", "cli", "guide", "教程", "指南", "工作流", "自动化", "工具")
    ):
        return "high"
    if any(name in themes for name in ("AI Coding Tools", "Developer Workflow", "Knowledge Operations")):
        return "medium"
    if any(name in concepts for name in ("Workflow Automation", "Knowledge Bases", "Context Engineering")):
        return "medium"
    return "low"


def infer_confidence(*, source_kind: str, title: str, canonical_url: str) -> str:
    if source_kind == "post_batch":
        return "low"
    lowered = title.lower()
    if any(token in lowered for token in ("疑似", "rumor", "传闻", "曝光", "爆料", "?")):
        return "low"
    hostname = ""
    try:
        hostname = urlparse(canonical_url).hostname or ""
    except Exception:
        hostname = ""
    hostname = hostname.lower()
    if any(
        hostname.endswith(domain)
        for domain in (
            "openai.com",
            "anthropic.com",
            "googleblog.com",
            "google.com",
            "microsoft.com",
            "nvidia.com",
            "github.blog",
        )
    ):
        return "high"
    if canonical_url:
        return "medium"
    return "low"


def infer_next_action(
    *,
    source_kind: str,
    content_type: str,
    actionability: str,
    concepts: list[str],
    themes: list[str],
) -> str:
    if source_kind == "post_batch":
        return "track"
    if actionability == "high":
        if any(name in themes for name in ("AI Coding Tools", "Developer Workflow", "Knowledge Operations")):
            return "test"
        if any(name in concepts for name in ("Workflow Automation", "Knowledge Bases", "Code")):
            return "build"
        return "write"
    if content_type == "thesis":
        return "write"
    if actionability == "medium":
        return "track"
    return "ignore"


def build_why_it_matters(
    *,
    source_kind: str,
    concepts: list[str],
    entities: list[str],
    themes: list[str],
    summary: list[str],
    key_signals: list[str],
) -> str:
    anchor = concepts[:1] or themes[:1] or entities[:1]
    if source_kind == "post_batch":
        if anchor:
            return f"Useful as a social-signal snapshot for {anchor[0]}."
        return "Useful as a social-signal snapshot that may need later confirmation."
    if anchor:
        return f"Potentially relevant to {anchor[0]} and worth a personal pass before it disappears into the archive."
    if summary:
        return truncate_line(summary[0], 140)
    if key_signals:
        return truncate_line(key_signals[0], 140)
    return "Worth a personal review before deciding whether to keep, act on, or discard it."


def normalize_source_line(raw: str) -> str:
    line = str(raw or "").replace("\u00a0", " ").strip()
    line = re.sub(r"\s+", " ", line)
    line = re.sub(r'^class="language-[^"]*">', "", line).strip()
    return line


def is_source_boilerplate_line(line: str) -> bool:
    lowered = line.lower()
    if not lowered:
        return True
    if re.match(r"^原创\s+\S+.*\d{4}-\d{2}-\d{2}", line):
        return True
    if lowered in {"阅读原文", "继续滑动看下一个", "分享", "点赞", "在看"}:
        return True
    if "微信扫一扫" in line or "公众号" in line:
        return True
    return False


def canonical_text_key(text: str) -> str:
    normalized = re.sub(r"[^\w\u4e00-\u9fff]+", "", str(text or "").lower())
    return normalized[:120]


def is_heading_line(line: str) -> bool:
    stripped = line.strip()
    if re.match(r"^#{1,6}\s*", stripped):
        return True
    if re.match(r"^[一二三四五六七八九十0-9]+[、.]\s*", stripped):
        return len(stripped) <= 40
    return False


def strip_heading_marker(line: str) -> str:
    stripped = line.strip()
    stripped = re.sub(r"^#{1,6}\s*", "", stripped).strip()
    stripped = re.sub(r"^[一二三四五六七八九十0-9]+[、.]\s*", "", stripped).strip()
    return stripped


def attach_heading_context(heading: str, text: str) -> str:
    clean_heading = strip_heading_marker(heading) if heading else ""
    clean_text = str(text or "").strip()
    if not clean_heading:
        return clean_text
    if clean_text.startswith(clean_heading):
        return clean_text
    if len(clean_heading) > 30:
        return clean_text
    return f"{clean_heading}: {clean_text}"


def is_list_line(line: str) -> bool:
    stripped = line.strip()
    return bool(re.match(r"^(?:[-*•●]|[0-9]+[.)])\s*", stripped))


def ends_sentence(line: str) -> bool:
    return bool(re.search(r"[。！？.!?：:]$", line.strip()))


def is_useful_paragraph(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < 24:
        return False
    if is_source_boilerplate_line(stripped):
        return False
    if stripped.startswith("![]("):
        return False
    if re.fullmatch(r"[#\-\s•●0-9.()]+", stripped):
        return False
    return True


def dedupe_paragraphs(paragraphs: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in paragraphs:
        key = canonical_text_key(item)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def summary_score(text: str) -> int:
    score = 0
    lowered = text.lower()
    if is_list_line(text):
        score += 2
    if ":" in text[:40]:
        score += 2
    if any(token in lowered for token in ("一句话", "换句话说", "核心", "本质", "区别", "联系", "分层", "总结", "最容易记住", "解决的是")):
        score += 6
    if any(token in lowered for token in ("tool", "skill", "plugin", "mcp", "acp", "agent", "workflow", "协议")):
        score += 4
    if any(token in lowered for token in ("是什么", "最适合", "核心特征", "核心价值")):
        score += 3
    if re.search(r"\d", text):
        score += 1
    if len(text) > 240:
        score -= 2
    if "这篇文章试图" in text or "原创 " in text:
        score -= 6
    return score


def key_signal_score(text: str) -> int:
    score = 0
    lowered = text.lower()
    if is_list_line(text):
        score += 5
    if ":" in text[:40]:
        score += 2
    if any(token in lowered for token in ("区别", "联系", "最适合", "核心问题", "本质", "一句话理解", "核心价值", "核心特征")):
        score += 5
    if any(token in lowered for token in ("tool", "skill", "plugin", "mcp", "acp", "agent", "session", "runtime", "协议")):
        score += 4
    if re.search(r"\d", text):
        score += 1
    if len(text) > 240:
        score -= 1
    if "这篇文章试图" in text or "原创 " in text:
        score -= 6
    return score


def select_distinct_lines(items: list[str], *, limit: int, max_len: int, prefer_unique_section: bool) -> list[str]:
    selected: list[str] = []
    seen: set[str] = set()
    seen_sections: set[str] = set()
    deferred: list[str] = []
    for item in items:
        candidate = truncate_line(item, max_len)
        key = canonical_text_key(candidate)
        if not key or key in seen:
            continue
        section_key = section_identity(candidate)
        if prefer_unique_section and section_key and section_key in seen_sections:
            deferred.append(candidate)
            continue
        seen.add(key)
        if section_key:
            seen_sections.add(section_key)
        selected.append(candidate)
        if len(selected) >= limit:
            return selected
    for candidate in deferred:
        key = canonical_text_key(candidate)
        if not key or key in seen:
            continue
        seen.add(key)
        selected.append(candidate)
        if len(selected) >= limit:
            break
    return selected


def section_identity(text: str) -> str:
    prefix = str(text or "").split(":", 1)[0].strip()
    if not prefix:
        return ""
    if len(prefix) > 30:
        return ""
    return canonical_text_key(prefix)


def normalize_record_knowledge_fields(record: dict[str, Any], config: AppConfig) -> dict[str, Any]:
    normalized = dict(record)
    raw_themes = list(normalized.get("themes", []) or [])
    raw_theme_candidates = list(normalized.get("theme_candidates", []) or [])
    normalized["themes"] = normalize_theme_list(raw_themes)
    normalized["theme_candidates"] = dedupe_preserve(
        extract_theme_candidates(raw_themes) + extract_theme_candidates(raw_theme_candidates)
    )
    if not normalized["themes"]:
        fallback_text = "\n".join(
            [
                str(normalized.get("title", "") or ""),
                *[str(item) for item in normalized.get("summary", []) or []],
                *[str(item) for item in normalized.get("key_signals", []) or []],
            ]
        )
        normalized["themes"] = normalize_theme_list(
            extract_themes(fallback_text, concepts=list(normalized.get("concepts", []) or []))
        )
    content_type = str(normalized.get("content_type", "") or "").strip().lower()
    if content_type not in CONTENT_TYPES:
        content_type = infer_content_type(
            source_kind=str(normalized.get("source_kind", "") or ""),
            title=str(normalized.get("title", "") or ""),
            text="\n".join(str(item) for item in normalized.get("summary", []) or []),
        )
    normalized["content_type"] = content_type

    status = str(normalized.get("status", "") or "").strip().lower()
    if status not in STATUS_VALUES:
        status = "inbox"
    normalized["status"] = status

    actionability = str(normalized.get("actionability", "") or "").strip().lower()
    if actionability not in LEVEL_VALUES:
        actionability = infer_actionability(
            source_kind=str(normalized.get("source_kind", "") or ""),
            title=str(normalized.get("title", "") or ""),
            content_type=content_type,
            concepts=list(normalized.get("concepts", []) or []),
            themes=list(normalized.get("themes", []) or []),
        )
    normalized["actionability"] = actionability

    confidence = str(normalized.get("confidence", "") or "").strip().lower()
    if confidence not in LEVEL_VALUES:
        confidence = infer_confidence(
            source_kind=str(normalized.get("source_kind", "") or ""),
            title=str(normalized.get("title", "") or ""),
            canonical_url=str(normalized.get("canonical_url", "") or ""),
        )
    normalized["confidence"] = confidence

    next_action = str(normalized.get("next_action", "") or "").strip().lower()
    if next_action not in ACTION_VALUES:
        next_action = infer_next_action(
            source_kind=str(normalized.get("source_kind", "") or ""),
            content_type=content_type,
            actionability=actionability,
            concepts=list(normalized.get("concepts", []) or []),
            themes=list(normalized.get("themes", []) or []),
        )
    normalized["next_action"] = next_action

    normalized["why_it_matters"] = str(normalized.get("why_it_matters", "") or "").strip()
    normalized["my_take"] = str(normalized.get("my_take", "") or "").strip()

    first_seen = str(normalized.get("first_seen", "") or "").strip() or infer_record_anchor_date(normalized)
    if not parse_flexible_date(first_seen):
        first_seen = today_date()
    normalized["first_seen"] = normalize_iso_date(first_seen)

    last_seen = str(normalized.get("last_seen", "") or "").strip() or str(normalized.get("compiled_at", "") or "").strip() or normalized["first_seen"]
    if not parse_flexible_date(last_seen):
        last_seen = normalized["first_seen"]
    normalized["last_seen"] = normalize_iso_date(last_seen)

    last_confirmed = str(normalized.get("last_confirmed", "") or "").strip()
    if parse_flexible_date(last_confirmed):
        normalized["last_confirmed"] = normalize_iso_date(last_confirmed)
    else:
        normalized["last_confirmed"] = ""

    revisit_after = str(normalized.get("revisit_after", "") or "").strip()
    if not parse_flexible_date(revisit_after):
        revisit_anchor = normalized["last_confirmed"] or normalized["last_seen"]
        revisit_after = shift_iso_date(revisit_anchor, config.review_days)
    normalized["revisit_after"] = normalize_iso_date(revisit_after)

    if not normalized["why_it_matters"]:
        normalized["why_it_matters"] = build_why_it_matters(
            source_kind=str(normalized.get("source_kind", "") or ""),
            concepts=list(normalized.get("concepts", []) or []),
            entities=list(normalized.get("entities", []) or []),
            themes=list(normalized.get("themes", []) or []),
            summary=list(normalized.get("summary", []) or []),
            key_signals=list(normalized.get("key_signals", []) or []),
        )
    return normalized


def infer_record_anchor_date(record: dict[str, Any]) -> str:
    for value in (
        str(record.get("compiled_at", "") or "").strip(),
        str(record.get("date", "") or "").strip(),
    ):
        if parse_flexible_date(value):
            return normalize_iso_date(value)
    return today_date()


def extract_concepts(text: str) -> list[str]:
    lowered = text.lower()
    results: list[str] = []
    for name, patterns, _themes in CONCEPT_RULES:
        if any(pattern in lowered for pattern in patterns):
            results.append(name)
    results.extend(generic_terms(text, limit=4))
    return normalize_concept_list(results)[:8]


def load_concept_aliases(config: AppConfig) -> dict[str, str]:
    lifecycle = load_json(config.state_dir / "concept_lifecycle.json", default={})
    aliases = lifecycle.get("aliases", {})
    if not isinstance(aliases, dict):
        return {}
    return {str(k).strip(): str(v).strip() for k, v in aliases.items() if str(k).strip() and str(v).strip()}


def normalize_concept_list(items: list[str], *, aliases: dict[str, str] | None = None) -> list[str]:
    _aliases = aliases or {}
    normalized: list[str] = []
    seen: set[str] = set()
    for item in items:
        name = normalize_concept_name(item)
        if not name:
            continue
        name = _aliases.get(name, name)
        lowered = name.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(name)
    return normalized


def taxonomy_name_key(value: str) -> str:
    text = str(value or "").strip().replace("&", " and ")
    text = re.sub(r"['’]", "", text)
    text = re.sub(r"[-_/]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def format_taxonomy_name(value: str) -> str:
    text = str(value or "").strip().replace("_", " ")
    text = re.sub(r"\s+", " ", text).strip(" -_/")
    if not text:
        return ""
    if len(text) > 48:
        return ""
    if len(text.split()) > 5:
        return ""
    if not re.search(r"[A-Za-z]", text):
        return ""
    if re.search(r"[。！？；;:：]", text):
        return ""
    if taxonomy_name_key(text) in {"n/a", "none", "other", "misc", "miscellaneous", "general", "news", "article"}:
        return ""
    if text.lower() == text:
        text = " ".join(part.capitalize() for part in text.split())
    acronyms = {"AI", "API", "CLI", "GPU", "IDE", "LLM", "MCP", "OCR", "RAG", "SDK", "SQL", "UI", "UX"}
    words: list[str] = []
    for raw_word in text.split():
        pieces = raw_word.split("-")
        normalized_pieces: list[str] = []
        for piece in pieces:
            if not piece:
                continue
            upper = piece.upper()
            if upper in acronyms or (piece.isupper() and len(piece) <= 5):
                normalized_pieces.append(upper)
            elif piece.islower():
                normalized_pieces.append(piece.capitalize())
            else:
                normalized_pieces.append(piece)
        if normalized_pieces:
            words.append("-".join(normalized_pieces))
    normalized = " ".join(words).strip()
    return normalized


def normalize_theme_name(name: str) -> str:
    formatted = format_taxonomy_name(name)
    if not formatted:
        return ""
    raw_key = taxonomy_name_key(str(name or ""))
    key = taxonomy_name_key(formatted)
    display_map = {taxonomy_name_key(item): item for item in GROUP_DISPLAY_NAME_MAP.get("theme", {})}
    if raw_key in display_map:
        return display_map[raw_key]
    if key in display_map:
        return display_map[key]
    if raw_key in THEME_CANONICAL_MAP:
        return THEME_CANONICAL_MAP[raw_key]
    if key in THEME_CANONICAL_MAP:
        return THEME_CANONICAL_MAP[key]
    return ""


def normalize_theme_list(items: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for item in items:
        name = normalize_theme_name(item)
        if not name:
            continue
        lowered = name.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(name)
    return normalized


def normalize_theme_candidate_name(name: str) -> str:
    formatted = format_taxonomy_name(name)
    if not formatted:
        return ""
    if normalize_theme_name(formatted):
        return ""
    return formatted


def extract_theme_candidates(items: list[str]) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    for item in items:
        candidate = normalize_theme_candidate_name(str(item or ""))
        if not candidate:
            continue
        key = taxonomy_name_key(candidate)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(candidate)
    return candidates


def normalize_concept_name(name: str) -> str:
    formatted = format_taxonomy_name(name)
    if not formatted:
        return ""
    raw_key = taxonomy_name_key(str(name or ""))
    key = taxonomy_name_key(formatted)
    if raw_key in CONCEPT_DROP_SET or key in CONCEPT_DROP_SET:
        return ""
    if raw_key in CONCEPT_CANONICAL_MAP:
        return CONCEPT_CANONICAL_MAP[raw_key]
    if key in CONCEPT_CANONICAL_MAP:
        return CONCEPT_CANONICAL_MAP[key]
    display_map = {taxonomy_name_key(item): item for item in GROUP_DISPLAY_NAME_MAP.get("concept", {})}
    if raw_key in display_map:
        return display_map[raw_key]
    if key in display_map:
        return display_map[key]
    return formatted


def extract_entities_rules(text: str) -> list[str]:
    lowered = text.lower()
    results: list[str] = []
    for name, patterns in ENTITY_RULES:
        if any(pattern in lowered for pattern in patterns):
            results.append(name)
    return dedupe_preserve(results)[:8]


def entity_name_key(name: str) -> str:
    normalized = re.sub(r"\s+", "", str(name or "")).strip().lower()
    normalized = re.sub(r"[·•\-—_（）()【】\[\]「」『』:：,，。.!?？/\\]+", "", normalized)
    return normalized


def extract_themes(text: str, *, concepts: list[str]) -> list[str]:
    lowered = text.lower()
    results: list[str] = []
    for name, patterns in THEME_RULES:
        if any(pattern in lowered for pattern in patterns):
            results.append(name)
    for concept_name, _patterns, themes in CONCEPT_RULES:
        if concept_name in concepts:
            results.extend(themes)
    return dedupe_preserve(results)[:6]


def generic_terms(text: str, *, limit: int) -> list[str]:
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9\-\+]{3,}", text)
    counter = Counter()
    for token in tokens:
        normalized = token.strip().lower()
        if normalized in STOPWORDS or normalized.startswith("http"):
            continue
        if normalized not in GENERIC_CONCEPT_KEYS:
            continue
        if len(normalized) > 30:
            continue
        concept_name = CONCEPT_CANONICAL_MAP.get(normalized)
        if not concept_name:
            continue
        counter[concept_name] += 1
    results = []
    for concept_name, count in counter.most_common(20):
        if count < 3:
            continue
        if concept_name not in results:
            results.append(concept_name)
        if len(results) >= limit:
            break
    return results


def load_post_items(text: str) -> list[dict[str, Any]]:
    raw = json.loads(text)
    items: list[dict[str, Any]] = []
    for tweet in raw:
        author = tweet.get("author") or {}
        metrics = tweet.get("metrics") or {}
        if tweet.get("is_retweet") and tweet.get("retweeted_tweet"):
            base_content = tweet["retweeted_tweet"].get("content", "")
            screen_name = tweet["retweeted_tweet"].get("author_screen_name", "") or author.get("screen_name", "")
        else:
            base_content = tweet.get("content", "")
            screen_name = author.get("screen_name", "")
        items.append(
            {
                "tweet_id": tweet.get("tweet_id", ""),
                "author": author.get("name", ""),
                "screen_name": screen_name,
                "content": clean_text(str(base_content or "")),
                "likes": int(metrics.get("likes", 0) or 0),
                "reposts": int(metrics.get("reposts", 0) or 0),
                "quotes": int(metrics.get("quotes", 0) or 0),
                "score": int(metrics.get("likes", 0) or 0) + int(metrics.get("reposts", 0) or 0) * 2 + int(metrics.get("quotes", 0) or 0) * 3,
                "urls": extract_urls(tweet),
            }
        )
    return items


def extract_urls(tweet: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for container in (tweet, tweet.get("retweeted_tweet") or {}, tweet.get("quoted_tweet") or {}):
        for entity in container.get("url_entities") or []:
            expanded = entity.get("expanded_url") or entity.get("url")
            if expanded:
                urls.append(expanded)
    return dedupe_preserve(urls)


def extract_domains(tweets: list[dict[str, Any]]) -> list[str]:
    counter = Counter()
    for tweet in tweets:
        for url in tweet["urls"]:
            try:
                hostname = urlparse(url).hostname or ""
            except Exception:
                hostname = ""
            if hostname:
                counter[hostname] += 1
    return [domain for domain, _count in counter.most_common(10)]


def cluster_post_items(
    tweets: list[dict[str, Any]],
    *,
    min_cluster: int = 3,
    max_cluster: int = 25,
    max_groups: int = 8,
) -> list[tuple[str, list[dict[str, Any]]]]:
    if len(tweets) <= min_cluster:
        return [("timeline", tweets)]

    _cluster_stopwords = STOPWORDS | {
        "http", "https", "www", "com", "html", "status",
        "going", "really", "thing", "things", "need", "know",
        "think", "want", "look", "looking", "got", "get", "getting",
        "great", "nice", "cool", "love", "wow", "yes", "sure",
        "right", "wrong", "already", "another", "every", "never",
        "always", "back", "could", "would", "might",
        "must", "shall", "since", "before", "between", "both",
        "each", "other", "then", "those", "these", "because",
        "doing", "having", "making", "taking", "working",
        "running", "trying", "giving", "sharing",
        "just", "like", "that", "with", "from", "also",
        "people", "time", "full", "next", "here", "ever",
        "insane", "meses",
    }

    tweet_keywords: list[set[str]] = []
    keyword_counter: Counter[str] = Counter()
    for tweet in tweets:
        text = str(tweet.get("content", "")).lower()
        tokens = set(re.findall(r"[a-z][a-z0-9\-]{3,}", text))
        cleaned = {t for t in tokens if t not in _cluster_stopwords and not t.startswith("http")}
        tweet_keywords.append(cleaned)
        for kw in cleaned:
            keyword_counter[kw] += 1

    max_df = len(tweets) * 0.15
    seed_keywords = [kw for kw, cnt in keyword_counter.most_common() if min_cluster <= cnt <= max_df]
    if not seed_keywords:
        top = sorted(tweets, key=lambda t: (t["score"], t["likes"]), reverse=True)
        chunks: list[tuple[str, list[dict[str, Any]]]] = []
        for i in range(0, len(top), max_cluster):
            chunks.append(("timeline", top[i : i + max_cluster]))
        return chunks[:max_groups]

    assigned: set[int] = set()
    clusters: list[tuple[str, list[dict[str, Any]]]] = []

    for seed_kw in seed_keywords:
        if len(clusters) >= max_groups - 1:
            break
        indices = [i for i in range(len(tweets)) if i not in assigned and seed_kw in tweet_keywords[i]]
        if len(indices) < min_cluster:
            continue
        group = [tweets[i] for i in indices]
        assigned.update(indices)
        clusters.append((seed_kw.title(), group))

    unassigned = [tweets[i] for i in range(len(tweets)) if i not in assigned]
    if unassigned:
        sorted_unassigned = sorted(unassigned, key=lambda t: (t["score"], t["likes"]), reverse=True)
        for i in range(0, len(sorted_unassigned), max_cluster):
            chunk = sorted_unassigned[i : i + max_cluster]
            if len(chunk) >= min_cluster or not clusters:
                clusters.append(("misc", chunk))
            else:
                label, group = clusters[-1]
                clusters[-1] = (label, group + chunk)

    clusters.sort(key=lambda c: sum(t["score"] for t in c[1]), reverse=True)

    final: list[tuple[str, list[dict[str, Any]]]] = []
    for label, group in clusters:
        if len(group) > max_cluster:
            sorted_group = sorted(group, key=lambda t: (t["score"], t["likes"]), reverse=True)
            for i in range(0, len(sorted_group), max_cluster):
                chunk_label = label if i == 0 else f"{label} {i // max_cluster + 1}"
                final.append((chunk_label, sorted_group[i : i + max_cluster]))
        else:
            final.append((label, group))

    return final


def entity_detail_for_record(record: dict[str, Any], name: str) -> dict[str, Any] | None:
    target = normalize_entity_name(name).lower()
    for detail in record.get("entity_details", []) or []:
        if not isinstance(detail, dict):
            continue
        detail_name = normalize_entity_name(str(detail.get("name", "")).strip())
        if detail_name and detail_name.lower() == target:
            return detail
    return None


def entity_salience_for_record(record: dict[str, Any], name: str) -> int:
    detail = entity_detail_for_record(record, name)
    if detail:
        return normalize_entity_salience(detail.get("salience"), confidence=float(detail.get("confidence", 0) or 0))
    if name in record.get("entities", []):
        return 2
    return 0


def record_text_blob(record: dict[str, Any]) -> str:
    snippets: list[str] = [
        str(record.get("title", "") or ""),
        str(record.get("display_title", "") or ""),
        str(record.get("why_it_matters", "") or ""),
        str(record.get("my_take", "") or ""),
    ]
    snippets.extend(str(item) for item in record.get("summary", []) or [])
    snippets.extend(str(item) for item in record.get("key_signals", []) or [])
    return "\n".join(snippets).lower()


def keyword_hits(text: str, keywords: tuple[str, ...]) -> int:
    hits = 0
    for keyword in keywords:
        normalized = str(keyword or "").strip().lower()
        if normalized and normalized in text:
            hits += 1
    return hits


def concept_relevance_score(name: str, record: dict[str, Any]) -> int:
    text = record_text_blob(record)
    score = keyword_hits(text, CONCEPT_RELEVANCE_HINTS.get(name, ()))
    if name.lower() in text:
        score += 2
    concept_count = len(record.get("concepts", []) or [])
    if concept_count:
        score += max(0, 3 - concept_count)
    if level_rank(str(record.get("actionability", "medium") or "")) >= 2:
        score += 1
    if level_rank(str(record.get("confidence", "medium") or "")) >= 2:
        score += 1
    return score


def concept_has_direct_signal(name: str, record: dict[str, Any]) -> bool:
    text = record_text_blob(record)
    return keyword_hits(text, CONCEPT_RELEVANCE_HINTS.get(name, ())) > 0 or name.lower() in text


def theme_relevance_score(name: str, record: dict[str, Any]) -> int:
    text = record_text_blob(record)
    score = keyword_hits(text, THEME_RELEVANCE_HINTS.get(name, ()))
    if name.lower() in text:
        score += 2
    theme_count = len(record.get("themes", []) or [])
    if theme_count:
        score += max(0, 2 - min(theme_count, 2))
    if level_rank(str(record.get("confidence", "medium") or "")) >= 2:
        score += 1
    return score


def theme_has_direct_signal(name: str, record: dict[str, Any]) -> bool:
    text = record_text_blob(record)
    return keyword_hits(text, THEME_RELEVANCE_HINTS.get(name, ())) > 0 or name.lower() in text


def sort_records_for_group(group_type: str, name: str, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if group_type == "concept":
        return sorted(
            records,
            key=lambda item: (concept_has_direct_signal(name, item), concept_relevance_score(name, item), item.get("date", ""), item["title"]),
            reverse=True,
        )
    if group_type == "theme":
        return sorted(
            records,
            key=lambda item: (theme_has_direct_signal(name, item), theme_relevance_score(name, item), item.get("date", ""), item["title"]),
            reverse=True,
        )
    if group_type != "entity":
        return sorted(records, key=lambda item: (item.get("date", ""), item["title"]), reverse=True)
    return sorted(
        records,
        key=lambda item: (entity_salience_for_record(item, name), item.get("date", ""), item["title"]),
        reverse=True,
    )


def primary_entity_records(name: str, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = sort_records_for_group("entity", name, records)
    primary = [record for record in ranked if entity_salience_for_record(record, name) >= ENTITY_HIGH_SALIENCE]
    if primary:
        return primary
    return ranked[: min(3, len(ranked))]


def entity_reading_records(name: str, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = sort_records_for_group("entity", name, records)
    anchored = [record for record in ranked if entity_salience_for_record(record, name) >= ENTITY_HIGH_SALIENCE]
    if anchored:
        return anchored[:3]
    medium = [record for record in ranked if entity_salience_for_record(record, name) >= 3]
    if medium:
        return medium[:2]
    return ranked[:1]


def common_entity_type(name: str, records: list[dict[str, Any]]) -> str:
    counter = Counter()
    for record in records:
        detail = entity_detail_for_record(record, name)
        if detail and str(detail.get("type", "")).strip():
            normalized_type = normalize_entity_type(name, str(detail.get("type", "")).strip())
            counter[normalized_type] += 1
    if not counter:
        return ""
    return counter.most_common(1)[0][0]


def build_group_summary(group_type: str, name: str, records: list[dict[str, Any]]) -> list[str]:
    if group_type == "entity":
        return build_entity_group_summary(name, records)
    kinds = Counter(record["source_kind"] for record in records)
    top_titles = [record_label(record) for record in sorted(records, key=lambda item: item.get("date", ""), reverse=True)[:5]]
    display_name = display_group_name(group_type, name)
    lines = [
        f"{display_name} 在当前语料里关联 {len(records)} 篇材料。",
        f"支撑材料主要来自：{comma_list([f'{count} 篇 {kind}' for kind, count in kinds.items()])}。",
    ]
    if top_titles:
        lines.append(f"最近最能代表这一页的材料包括：{comma_list(top_titles[:4])}。")
    if group_type == "theme":
        lines.append("这个主题页更像专题入口，用来把相关概念、对象和 source 串起来。")
    elif group_type == "concept":
        lines.append("这个概念页聚合的是可复用的方法、机制和问题框架，而不是单个命名对象。")
    else:
        lines.append("这个对象页追踪的是一个具体命名实体，例如公司、产品、人物或项目。")
    return lines[:4]


def build_entity_group_summary(name: str, records: list[dict[str, Any]]) -> list[str]:
    primary_records = primary_entity_records(name, records)
    if not primary_records:
        primary_records = records[:3]
    top_titles = [record_label(record) for record in primary_records[:3]]
    primary_type = common_entity_type(name, primary_records)
    type_phrase = f"，当前更常被当作{translate_entity_type(primary_type)}来讨论" if primary_type else ""
    if top_titles:
        first_line = (
            f"{name}{type_phrase}，在 {len(primary_records)} 篇高相关材料里反复出现，代表性材料包括：{comma_list(top_titles)}。"
        )
    else:
        first_line = f"{name}{type_phrase}，在 {len(primary_records)} 篇高相关材料里反复出现。"
    lines = [first_line]

    evidence_lines: list[str] = []
    seen_evidence: set[str] = set()
    for record in primary_records:
        detail = entity_detail_for_record(record, name)
        if not detail:
            continue
        evidence = str(detail.get("evidence", "")).strip()
        if not evidence:
            continue
        key = re.sub(r"\s+", " ", evidence).strip().lower()
        if key in seen_evidence:
            continue
        seen_evidence.add(key)
        evidence_lines.append(evidence)
    lines.extend(evidence_lines[:2])

    related_entities = related_terms(name, records, field="entities", group_type="entity")
    if related_entities:
        lines.append(f"它最常和这些对象一起出现：{comma_list(related_entities[:5])}。")
    return lines[:4]


def records_for_group_ai(group_type: str, name: str, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if group_type != "entity":
        return records
    selected: list[dict[str, Any]] = []
    for index, record in enumerate(sort_records_for_group("entity", name, records)[:12], start=1):
        enriched = dict(record)
        enriched["source_key"] = f"S{index}"
        enriched["entity_salience"] = entity_salience_for_record(record, name)
        detail = entity_detail_for_record(record, name) or {}
        enriched["entity_evidence"] = str(detail.get("evidence", "")).strip()
        selected.append(enriched)
    return selected


def build_simple_group_page_body(
    *,
    group_type: str,
    name: str,
    records: list[dict[str, Any]],
    page_path: Path,
    related_concepts: list[dict[str, Any]],
    related_themes: list[dict[str, Any]],
    config: AppConfig,
) -> str:
    overview = build_group_summary(group_type, name, records)
    source_limit = config.source_limit_per_section
    sections = [
        f"# {display_group_heading(group_type, name)}",
        "",
        *render_group_section("概览", [f"- {line}" for line in overview]),
        *render_group_section(
            "相关文章",
            build_learning_source_links(
                group_type, name, records, page_path.parent, config.repo_root, limit=source_limit, config=config
            ),
        ),
    ]
    if group_type == "concept":
        sections.extend(
            render_group_section(
                "相关主题",
                related_link_lines(related_themes, page_path.parent, config.themes_dir, limit=config.theme_related_limit),
            )
        )
    else:
        concept_links = [
            item
            for item in related_concepts
            if taxonomy_name_key(str(item.get("name", ""))) != taxonomy_name_key(name)
            and taxonomy_name_key(str(item.get("name", ""))) not in theme_reserved_name_keys()
        ]
        sections.extend(
            render_group_section(
                "相关概念",
                related_link_lines(concept_links, page_path.parent, config.concepts_dir, limit=config.concept_related_limit),
            )
        )
        sections.extend(
            render_group_section(
                "相关主题",
                related_link_lines(
                    [item for item in related_themes if taxonomy_name_key(str(item.get("name", ""))) != taxonomy_name_key(name)],
                    page_path.parent,
                    config.themes_dir,
                    limit=config.theme_related_limit,
                ),
            )
        )
    return "\n".join(sections).strip()


def build_group_page_body(
    *,
    group_type: str,
    name: str,
    records: list[dict[str, Any]],
    page_path: Path,
    related_concepts: list[dict[str, Any]],
    related_entities: list[dict[str, Any]],
    related_themes: list[dict[str, Any]],
    config: AppConfig,
    text_writer: KnowledgeWikiWriter,
    ai_group: dict[str, Any] | None = None,
    ai_records: list[dict[str, Any]] | None = None,
) -> str:
    if config.simple_group_pages:
        return build_simple_group_page_body(
            group_type=group_type,
            name=name,
            records=records,
            page_path=page_path,
            related_concepts=related_concepts,
            related_themes=related_themes,
            config=config,
        )
    ai_payload = ai_group
    ai_source_records = ai_records or records_for_group_ai(group_type, name, records)
    if group_type == "concept":
        return build_concept_page_body(
            name=name,
            records=records,
            page_path=page_path,
            related_concepts=related_concepts,
            related_entities=related_entities,
            related_themes=related_themes,
            config=config,
            ai_group=ai_payload or {},
        )
    if group_type == "entity":
        return build_entity_page_body(
            name=name,
            records=records,
            page_path=page_path,
            related_concepts=related_concepts,
            related_entities=related_entities,
            related_themes=related_themes,
            config=config,
            ai_group=ai_payload or {},
            ai_records=ai_source_records,
        )
    sections = [
        f"# {display_group_heading(group_type, name)}",
        "",
        *render_group_section("概览", list((ai_payload or {}).get("overview") or build_group_overview_lines(group_type, name, records)),
        ),
        *render_group_section("当前讨论重点", list((ai_payload or {}).get("discussion") or collect_group_learning_lines(group_type, name, records, limit=5))),
        *render_group_section("近期信号", build_recent_signal_lines(records, limit=5)),
        *render_group_section(
            "建议先读",
            build_learning_source_links(group_type, name, records, page_path.parent, config.repo_root, limit=5, config=config),
        ),
        *render_group_section("相关概念", related_link_lines(related_concepts, page_path.parent, config.concepts_dir, limit=config.concept_related_limit)),
        *render_group_section("相关对象", related_link_lines(related_entities, page_path.parent, config.entities_dir, limit=config.entity_related_limit)),
        *render_group_section("相关主题", related_link_lines(related_themes, page_path.parent, config.themes_dir, limit=5)),
    ]
    return "\n".join(sections).strip()


def build_entity_page_body(
    *,
    name: str,
    records: list[dict[str, Any]],
    page_path: Path,
    related_concepts: list[dict[str, Any]],
    related_entities: list[dict[str, Any]],
    related_themes: list[dict[str, Any]],
    config: AppConfig,
    ai_group: dict[str, Any],
    ai_records: list[dict[str, Any]],
) -> str:
    definition = str(ai_group.get("definition", "") or "").strip()
    why_track = str(ai_group.get("why_track", "") or "").strip()
    changes = list(ai_group.get("changes", []) or [])
    overview = list(ai_group.get("overview", []) or [])
    source_key_map = {
        str(record.get("source_key", "")).strip(): record
        for record in ai_records
        if str(record.get("source_key", "")).strip()
    }
    selected_source_keys = [key for key in list(ai_group.get("recommended_source_keys", []) or []) if key in source_key_map]
    selected_records = [source_key_map[key] for key in selected_source_keys] or ai_records
    sections = [
        f"# {display_group_heading('entity', name)}",
        "",
        *render_group_section("概览", [f"- {item}" for item in overview] if overview else ["- 暂无 AI 综述。"]),
        *render_group_section("这是什么", [f"- {definition}"] if definition else ["- 暂无 AI 定义。"]),
        *render_group_section("为什么值得跟踪", [f"- {why_track}"] if why_track else ["- 暂无 AI 判断。"]),
        *render_group_section("当前变化", [f"- {item}" for item in changes] if changes else ["- 暂无 AI 提炼出的稳定变化。"]),
        *render_group_section("近期信号", build_recent_signal_lines(records, limit=5)),
        *render_group_section(
            "建议先读",
            build_learning_source_links("entity", name, selected_records, page_path.parent, config.repo_root, limit=4, config=config),
        ),
        *render_group_section("相关概念", related_link_lines(related_concepts, page_path.parent, config.concepts_dir, limit=config.concept_related_limit)),
        *render_group_section("相关对象", related_link_lines(related_entities, page_path.parent, config.entities_dir, limit=8)),
        *render_group_section("相关主题", related_link_lines(related_themes, page_path.parent, config.themes_dir, limit=5)),
    ]
    return "\n".join(sections).strip()


def build_concept_page_body(
    *,
    name: str,
    records: list[dict[str, Any]],
    page_path: Path,
    related_concepts: list[dict[str, Any]],
    related_entities: list[dict[str, Any]],
    related_themes: list[dict[str, Any]],
    config: AppConfig,
    ai_group: dict[str, Any],
) -> str:
    quick_takeaways = list(ai_group.get("quick_takeaways", []) or [])
    definition = str(ai_group.get("definition", "") or "").strip()
    problem = str(ai_group.get("problem", "") or "").strip()
    mechanisms = list(ai_group.get("mechanisms", []) or [])
    study_path = list(ai_group.get("study_path", []) or [])
    examples = list(ai_group.get("examples", []) or [])
    confusions = list(ai_group.get("confusions", []) or [])
    discussion = list(ai_group.get("discussion", []) or collect_group_learning_lines("concept", name, records, limit=4))
    sections = [
        f"# {display_group_heading('concept', name)}",
        "",
        *render_group_section("概览", list(ai_group.get("overview", []) or build_group_overview_lines("concept", name, records))),
        *render_group_section("3 分钟理解", [f"- {item}" for item in quick_takeaways] if quick_takeaways else ["- 先抓住它是什么、解决什么问题、和相近概念差在哪。"]),
        *render_group_section("这是什么", [f"- {definition}"] if definition else ["- 这一概念在当前语料里还缺少稳定定义，建议先看下面的阅读入口。"]),
        *render_group_section("它解决什么问题", [f"- {problem}"] if problem else ["- 这批材料还没有足够的信息来稳定回答这个问题。"]),
        *render_group_section("核心机制", [f"- {item}" for item in mechanisms] if mechanisms else ["- 暂无可用机制说明。"]),
        *render_group_section("怎么学这个概念", [f"- {item}" for item in study_path] if study_path else ["- 先看定义和问题，再用典型例子把概念落到真实场景里。"]),
        *render_group_section("当前讨论重点", [f"- {item}" for item in discussion] if discussion else ["- 暂无当前讨论重点。"]),
        *render_group_section(
            "典型例子",
            [f"- {item}" for item in examples]
            if examples
            else build_learning_source_links("concept", name, records, page_path.parent, config.repo_root, limit=3, config=config),
        ),
        *render_group_section("容易混淆的点", [f"- {item}" for item in confusions] if confusions else ["- 暂无明显易混点。"]),
        *render_group_section("近期信号", build_recent_signal_lines(records, limit=5)),
        *render_group_section(
            "建议先读",
            build_learning_source_links("concept", name, records, page_path.parent, config.repo_root, limit=5, config=config),
        ),
        *render_group_section("相关概念", related_link_lines(related_concepts, page_path.parent, config.concepts_dir, limit=config.concept_related_limit)),
        *render_group_section("相关对象", related_link_lines(related_entities, page_path.parent, config.entities_dir, limit=config.entity_related_limit)),
        *render_group_section("相关主题", related_link_lines(related_themes, page_path.parent, config.themes_dir, limit=5)),
    ]
    return "\n".join(sections).strip()


def render_group_section(title: str, lines: list[str]) -> list[str]:
    cleaned = [line for line in lines if str(line).strip()]
    if not cleaned:
        cleaned = ["- 暂无内容。"]
    return [f"## {title}", "", *cleaned, ""]


def build_group_overview_lines(group_type: str, name: str, records: list[dict[str, Any]]) -> list[str]:
    titles = [record_label(record) for record in records[:3]]
    display_name = display_group_name(group_type, name)
    if group_type == "concept":
        lines = [
            f"- `{display_name}` 在当前语料中关联 {len(records)} 篇材料，更适合作为一个可复用的概念来理解，而不是单一对象。",
        ]
        if titles:
            lines.append(f"- 这组材料里，最值得先看的入口有：{cn_join(titles)}。")
        return lines
    if group_type == "entity":
        primary_type = common_entity_type(name, records)
        type_text = translate_entity_type(primary_type) if primary_type else "对象"
        lines = [
            f"- `{display_name}` 在当前语料中被当作{type_text}来讨论，目前关联 {len(records)} 篇材料。",
        ]
        if titles:
            lines.append(f"- 如果你想快速理解它，建议先从：{cn_join(titles)} 开始。")
        return lines
    lines = [
        f"- `{display_name}` 是一个主题篮子，用来把相关概念、对象和 source 串起来，目前覆盖 {len(records)} 篇材料。",
    ]
    if titles:
        lines.append(f"- 当前这个主题下最能代表问题空间的材料有：{cn_join(titles)}。")
    return lines


def collect_group_learning_lines(group_type: str, name: str, records: list[dict[str, Any]], *, limit: int) -> list[str]:
    candidates: list[tuple[int, str]] = []
    seen: set[str] = set()
    for idx, record in enumerate(records[:8]):
        base_score = max(0, 20 - idx * 2)
        for position, line in enumerate(list(record.get("summary", []) or []) + list(record.get("key_signals", []) or [])):
            text = str(line or "").strip()
            if not text:
                continue
            key = canonical_text_key(text)
            if not key or key in seen:
                continue
            seen.add(key)
            score = base_score - position
            if group_type == "entity" and name.lower() in text.lower():
                score += 4
            if group_type == "concept" and any(token in text.lower() for token in ("本质", "核心", "记忆", "评测", "推理", "工作流", "上下文")):
                score += 3
            if len(text) > 180:
                score -= 1
            candidates.append((score, truncate_line(text, 180)))
    ordered = [f"- {text}" for _score, text in sorted(candidates, key=lambda item: (item[0], len(item[1])), reverse=True)[:limit]]
    if ordered:
        return ordered
    return ["- 这一页目前还缺少足够的摘要材料，建议先看下面的 source。"]


def build_learning_source_links(
    group_type: str,
    name: str,
    records: list[dict[str, Any]],
    source_dir: Path,
    repo_root: Path,
    *,
    limit: int,
    config: AppConfig,
) -> list[str]:
    lines: list[str] = []
    ordered = sort_records_for_group(group_type, name, records)
    if group_type == "entity":
        primary = entity_reading_records(name, ordered)
        if primary:
            ordered = primary
    elif group_type == "concept":
        focused = [record for record in ordered if concept_has_direct_signal(name, record)]
        if focused:
            ordered = focused
        else:
            return ["- 当前语料里还没有足够聚焦这个概念的 source，建议先读上面的概览，再回到相关主题继续扩展。"]
    elif group_type == "theme":
        focused = [record for record in ordered if theme_has_direct_signal(name, record)]
        if focused:
            ordered = focused
    ordered = ordered[:limit]
    for record in ordered:
        target = record_link_path(record, config)
        if target is None:
            continue
        rel = markdown_link_target(target, source_dir)
        hint = build_source_learning_hint(record)
        lines.append(f"- [{record_label(record)}]({rel})：{hint}")
    return lines or ["- 暂无推荐阅读。"]


def build_source_learning_hint(record: dict[str, Any]) -> str:
    for line in list(record.get("summary", []) or []) + list(record.get("key_signals", []) or []):
        text = truncate_line(str(line or "").strip(), 80)
        if text:
            return text
    why = truncate_line(str(record.get("why_it_matters", "") or "").strip(), 80)
    if why:
        return why
    return f"{record.get('date', 'undated')} · {record.get('source_kind', 'source')}"


def build_recent_signal_lines(records: list[dict[str, Any]], *, limit: int) -> list[str]:
    ordered = sorted(
        dedupe_records(records),
        key=lambda item: (normalize_iso_date(str(item.get("date", "") or "")), str(item.get("compiled_at", "") or "")),
        reverse=True,
    )
    lines: list[str] = []
    seen: set[str] = set()
    for record in ordered:
        signal = ""
        for item in list(record.get("key_signals", []) or []) + list(record.get("summary", []) or []):
            text = truncate_line(str(item or "").strip(), 120)
            if text:
                signal = text
                break
        if not signal:
            signal = build_source_learning_hint(record)
        key = canonical_text_key(signal)
        if not key or key in seen:
            continue
        seen.add(key)
        lines.append(f"- {record.get('date', 'undated')} · {signal}")
        if len(lines) >= limit:
            break
    return lines or ["- 暂无近期信号。"]


def translate_entity_type(entity_type: str) -> str:
    mapping = {
        "person": "人物",
        "organization": "组织",
        "company": "公司",
        "product": "产品",
        "browser": "浏览器",
        "platform": "平台",
        "project": "项目",
        "protocol": "协议",
        "standard": "标准",
        "event": "事件",
        "publication": "出版物",
    }
    return mapping.get(str(entity_type or "").strip().lower(), "对象")


def related_term_items(name: str, records: list[dict[str, Any]], *, field: str, group_type: str) -> list[dict[str, Any]]:
    counter = Counter()
    mentions = Counter()
    for record in records:
        weight = 1
        if group_type == "entity":
            weight = max(1, entity_salience_for_record(record, name) - 1)
        elif group_type == "concept":
            weight = max(1, concept_relevance_score(name, record))
        elif group_type == "theme":
            weight = max(1, theme_relevance_score(name, record))
        for item in record.get(field, []):
            if item != name:
                counter[item] += weight
                mentions[item] += 1
    items = [
        {
            "name": item,
            "mentions": mentions[item],
            "score": counter[item],
        }
        for item, _count in counter.most_common()
    ]
    items.sort(key=lambda item: (-int(item["mentions"]), -int(item["score"]), str(item["name"]).lower()))
    return items


def related_terms(name: str, records: list[dict[str, Any]], *, field: str, group_type: str) -> list[str]:
    counter = Counter()
    mentions = Counter()
    relevant_records = records
    if group_type == "entity":
        relevant_records = primary_entity_records(name, records)
        if not relevant_records:
            relevant_records = records
    if group_type == "concept":
        concept_focused = [record for record in records if concept_has_direct_signal(name, record)]
        if concept_focused:
            relevant_records = concept_focused
    elif group_type == "theme":
        theme_focused = [record for record in records if theme_has_direct_signal(name, record)]
        if theme_focused:
            relevant_records = theme_focused

    for record in relevant_records:
        weight = 1
        if group_type == "entity":
            weight = max(1, entity_salience_for_record(record, name) - 1)
        elif group_type == "concept":
            weight = max(1, concept_relevance_score(name, record))
        elif group_type == "theme":
            weight = max(1, theme_relevance_score(name, record))
        for item in record.get(field, []):
            if item != name:
                counter[item] += weight
                mentions[item] += 1
    if group_type == "concept" and field in {"entities", "themes", "concepts"} and relevant_records:
        min_mentions = 2 if len(relevant_records) >= 4 else 1
        min_ratio = 0.45 if len(relevant_records) >= 4 else 0.5 if len(relevant_records) >= 2 else 1.0
        items = [
            item
            for item, _count in counter.most_common()
            if mentions[item] >= min_mentions and (mentions[item] / len(relevant_records)) >= min_ratio
        ]
        return items[:6]
    if group_type == "theme" and field in {"entities", "concepts", "themes"} and relevant_records:
        min_mentions = 2 if len(relevant_records) >= 5 else 1
        min_ratio = 0.35 if len(relevant_records) >= 5 else 0.5 if len(relevant_records) >= 2 else 1.0
        items = [
            item
            for item, _count in counter.most_common()
            if mentions[item] >= min_mentions and (mentions[item] / len(relevant_records)) >= min_ratio
        ]
        return items[:8]
    if group_type == "entity" and field in {"concepts", "themes", "entities"} and relevant_records:
        min_mentions = 2 if len(relevant_records) >= 3 else 1
        min_ratio = 0.45 if len(relevant_records) >= 4 else 0.5 if len(relevant_records) >= 2 else 1.0
        items = [
            item
            for item, _count in counter.most_common()
            if mentions[item] >= min_mentions and (mentions[item] / len(relevant_records)) >= min_ratio
        ]
        filtered: list[str] = []
        for item in items:
            related_strength = max(entity_salience_for_record(record, item) for record in relevant_records)
            if related_strength >= 3 or mentions[item] >= 2:
                filtered.append(item)
        return filtered[:6]
    return [item for item, _count in counter.most_common(8)]


def related_link_lines(items: list[dict[str, Any]], source_dir: Path, target_dir: Path, *, limit: int | None = None) -> list[str]:
    if not items:
        return ["- 暂无"]
    lines: list[str] = []
    group_type = singular_group_name(target_dir.name)
    selected = items[:limit] if limit is not None else items
    for item in selected:
        name = str(item.get("name", "") or "").strip()
        if not name:
            continue
        mentions = int(item.get("mentions", 0) or 0)
        target = target_dir / f"{group_slug(name, singular_group_name(target_dir.name))}.md"
        label = display_group_name(group_type, name)
        suffix = f" ({mentions})" if mentions > 0 else ""
        if target.exists():
            lines.append(f"- [{label}]({markdown_link_target(target, source_dir)}){suffix}")
    return lines or ["- 暂无"]


def wiki_links(names: list[str], source_dir: Path, target_dir: Path) -> list[str]:
    if not names:
        return ["- 暂无"]
    lines = []
    group_type = singular_group_name(target_dir.name)
    for name in names:
        target = target_dir / f"{group_slug(name, singular_group_name(target_dir.name))}.md"
        label = display_group_name(group_type, name)
        if target.exists():
            lines.append(f"- [{label}]({markdown_link_target(target, source_dir)})")
    return lines or ["- 暂无"]


def source_links(
    records: list[dict[str, Any]],
    source_dir: Path,
    repo_root: Path,
    config: AppConfig,
    *,
    group_type: str = "",
) -> list[str]:
    lines = []
    ordered = records
    if group_type in {"concept", "theme"} and records:
        ordered = sort_records_for_group(group_type, "", records)
    elif group_type != "entity":
        ordered = sorted(records, key=lambda item: (item.get("date", ""), item["title"]), reverse=True)
    for record in ordered:
        target = record_link_path(record, config)
        if target is None:
            continue
        rel = markdown_link_target(target, source_dir)
        lines.append(f"- [{record_label(record)}]({rel}): {record.get('date', 'undated')}")
    return lines or ["- 暂无"]


def write_markdown(path: Path, frontmatter: dict[str, Any], body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "---\n" + yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False).strip() + "\n---\n\n" + body.strip() + "\n"
    write_text_with_retry(path, content)


def write_plain_markdown(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_with_retry(path, text.strip() + "\n")


def load_json(path: Path, *, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except Exception:
        return default


def slim_record(record: dict[str, Any]) -> dict[str, Any]:
    """Strip empty fields and verbose entity_details for smaller registry.json."""
    out: dict[str, Any] = {}
    for k, v in record.items():
        if not v and v != 0:
            continue
        if k == "entity_details":
            out[k] = [
                {
                    "name": e["name"],
                    "type": e.get("type", ""),
                    **({"salience": e["salience"]} if e.get("salience") is not None else {}),
                    **({"confidence": e["confidence"]} if e.get("confidence") is not None else {}),
                }
                for e in v
            ]
        else:
            out[k] = v
    return out


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not write_text_with_retry(path, json.dumps(payload, ensure_ascii=False, indent=2)):
        raise OSError(f"failed to write {path}")


def count_markdown_files(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for _item in path.rglob("*.md"))


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    parts = text.split("---\n", 2)
    if len(parts) < 3:
        return {}, text
    _prefix, frontmatter_text, body = parts
    try:
        frontmatter = yaml.safe_load(frontmatter_text) or {}
    except Exception:
        return {}, text
    if not isinstance(frontmatter, dict):
        return {}, text
    return frontmatter, body


def parse_note_sections(body: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current = ""
    for raw_line in body.splitlines():
        line = raw_line.rstrip()
        heading_match = re.match(r"^##\s+(.+?)\s*$", line)
        if heading_match:
            current = heading_match.group(1).strip()
            sections.setdefault(current, [])
            continue
        if not current:
            continue
        sections[current].append(line)
    return sections


def bullet_values(lines: list[str]) -> list[str]:
    values: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        stripped = re.sub(r"^-\s*", "", stripped).strip()
        if stripped:
            values.append(stripped)
    return values


def render_extra_sections(sections: dict[str, list[str]]) -> list[str]:
    standard = {heading for headings in SECTION_HEADING_ALIASES.values() for heading in headings}
    rendered: list[str] = []
    for heading, lines in sections.items():
        if heading in standard:
            continue
        rendered.extend([f"## {heading}", ""])
        rendered.extend(line for line in lines if line.strip())
        rendered.append("")
    if rendered and not rendered[-1].strip():
        rendered.pop()
    return rendered


def stable_slug(text: str, salt: str) -> str:
    ascii_part = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    ascii_part = re.sub(r"-{2,}", "-", ascii_part)
    if not ascii_part:
        ascii_part = "note"
    digest = hashlib.sha1(salt.encode("utf-8")).hexdigest()[:8]
    return f"{ascii_part[:60]}-{digest}"


def group_slug(name: str, group_type: str) -> str:
    sanitized = sanitize_title_filename(name)
    if sanitized:
        return sanitized
    return stable_slug(name, f"{group_type}:{name}")


def singular_group_name(dirname: str) -> str:
    if dirname.endswith("ies"):
        return dirname[:-3] + "y"
    if dirname.endswith("s"):
        return dirname[:-1]
    return dirname


def sanitize_title_filename(name: str) -> str:
    value = re.sub(r"[<>:\"/\\\\|?*]", " ", name).strip()
    value = re.sub(r"\s+", " ", value)
    value = value.strip(". ")
    if not value:
        return ""
    if len(value) > 80:
        value = value[:80].rstrip()
    return value


def allocate_source_note_path(*, base_dir: Path, title: str, source_id: str) -> Path:
    base_dir.mkdir(parents=True, exist_ok=True)
    stem = sanitize_title_filename(title) or "note"
    candidate = base_dir / f"{stem}.md"
    if source_note_matches(candidate, source_id):
        return candidate
    if not candidate.exists():
        return candidate

    index = 2
    while True:
        numbered = base_dir / f"{stem}-{index}.md"
        if source_note_matches(numbered, source_id):
            return numbered
        if not numbered.exists():
            return numbered
        index += 1


def source_note_matches(path: Path, source_id: str) -> bool:
    if not path.exists():
        return False
    frontmatter, _body = split_frontmatter(path.read_text(encoding="utf-8"))
    return bool(frontmatter and str(frontmatter.get("source_id", "")).strip() == source_id)


def today_date() -> str:
    return datetime.now().date().isoformat()


def parse_flexible_date(raw: str) -> date | None:
    value = str(raw or "").strip()
    if not value:
        return None
    normalized = normalize_date(value)
    if re.fullmatch(r"\d{8}", normalized):
        try:
            return datetime.strptime(normalized, "%Y%m%d").date()
        except ValueError:
            return None
    try:
        return datetime.fromisoformat(value[:19]).date()
    except ValueError:
        return None


def normalize_iso_date(raw: str) -> str:
    parsed = parse_flexible_date(raw)
    if not parsed:
        return today_date()
    return parsed.isoformat()


def shift_iso_date(raw: str, days: int) -> str:
    parsed = parse_flexible_date(raw)
    if not parsed:
        parsed = datetime.now().date()
    return (parsed + timedelta(days=days)).isoformat()


def date_bucket_for(date_str: str) -> str:
    match = re.search(r"(\d{8})", date_str)
    if match:
        return match.group(1)
    return "undated"


def normalize_date(raw: str) -> str:
    if not raw:
        return ""
    if re.fullmatch(r"\d{8}", raw):
        return raw
    digits = re.findall(r"\d+", raw)
    if len(digits) >= 3 and len(digits[0]) == 4:
        return f"{digits[0]}{digits[1].zfill(2)}{digits[2].zfill(2)}"
    return raw


def compiler_build_id(config: AppConfig) -> str:
    paths = [
        config.skill_root / "config.yaml",
        config.skill_root / "src" / "knowledge_wiki_compiler" / "app.py",
        config.skill_root / "src" / "knowledge_wiki_compiler" / "config.py",
        config.skill_root / "src" / "knowledge_wiki_compiler" / "content_ai.py",
        config.skill_root / "src" / "knowledge_wiki_compiler" / "entity_ai.py",
    ]
    digest = hashlib.sha1()
    for path in paths:
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        try:
            digest.update(path.read_bytes())
        except FileNotFoundError:
            digest.update(b"<missing>")
        digest.update(b"\0")
    return digest.hexdigest()[:12]


def sha1_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def dedupe_preserve(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        cleaned = item.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return result


def dedupe_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for record in records:
        key = record["source_id"]
        if key in seen:
            continue
        seen.add(key)
        result.append(record)
    return result


def truncate_line(text: str, limit: int) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def comma_list(items: list[str]) -> str:
    cleaned = [item for item in items if item]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    return ", ".join(cleaned[:-1]) + f", and {cleaned[-1]}"


def cn_join(items: list[str]) -> str:
    cleaned = [item for item in items if item]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    if len(cleaned) == 2:
        return f"{cleaned[0]}、{cleaned[1]}"
    return "、".join(cleaned[:-1]) + f"，以及 {cleaned[-1]}"


def looks_like_blocked_extraction(record: dict[str, Any]) -> bool:
    snippets = []
    snippets.extend(str(item) for item in record.get("summary", []) or [])
    snippets.extend(str(item) for item in record.get("key_signals", []) or [])
    lowered = " ".join(snippets).lower()
    return any(
        token in lowered
        for token in (
            "enable javascript and cookies to continue",
            "no summary was extracted from the source body",
            "access denied",
            "captcha",
        )
    )
