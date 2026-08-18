from __future__ import annotations

import json
import re
import time
from typing import Any

from .ai_client import (
    AIProvider,
    AIProviderError,
    build_providers,
    call_provider_text,
    extract_json_text,
    load_env_file,
)

from .config import AppConfig

_CONTENT_TYPES = {"fact", "opinion", "signal", "tactic", "thesis"}
_LEVEL_VALUES   = {"high", "medium", "low"}
_ACTION_VALUES  = {"write", "test", "track", "ignore", "share", "build", "none"}


class KnowledgeWikiWriter:
    def __init__(self, config: AppConfig):
        self.config = config
        self._load_env_file(config.shared_ai_env_file)
        self.providers = self._build_providers()

    @property
    def enabled(self) -> bool:
        return bool(self.providers)

    def _short_label(self, value: str, *, limit: int = 100) -> str:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if len(text) <= limit:
            return text
        return text[: limit - 1].rstrip() + "..."

    def generate_source_copy(
        self,
        *,
        source_kind: str,
        title: str,
        author: str,
        canonical_url: str,
        content: str,
        available_concepts: list[str],
        available_themes: list[str],
        entities: list[str],
    ) -> dict[str, Any] | None:
        if not self.providers:
            return None
        trimmed = content[:8000]
        prompt = f"""你在为个人知识库生成 source note。请基于材料输出高质量、可学习的中文内容，不要照抄原文段落，不要输出英文模板。

请只输出一个 JSON 对象，包含：
- display_title: 1 条字符串。10-32 字，生成适合个人知识库展示的中文标题
- summary: 4 条数组。每条 35-90 字，概括这篇材料最值得理解的内容
- key_signals: 4 条数组。每条 25-80 字，提炼事实、方法、变化或判断
- why_it_matters: 1 条字符串。30-80 字，说清它为什么值得进入个人知识库
- open_questions: 3 条数组。每条 18-50 字，提出值得继续追踪的问题
- concepts_tags: 数组。识别这篇材料真正涉及的概念，2-5 项。请输出可复用、稳定的英文概念标签，使用 1-4 个词的 Title Case 短语；概念要指方法、框架、能力、问题域或机制，不要输出公司名、产品名、人名、一次性事件名，也不要写整句
- themes_tags: 数组。识别这篇材料真正落入的主题，1-4 项。只能从 theme_examples 里选择，不要创建新 theme；新热词、新方法、新现象应放进 concepts_tags，而不是 themes_tags
- content_type: 1 条字符串。从以下选项中选一个：fact / opinion / signal / tactic / thesis
  fact=基于数据/研究的客观事实；opinion=作者观点/评论；signal=新闻动态/发布/公告；tactic=指南/教程/可操作方法；thesis=框架性判断/趋势分析
- actionability: 1 条字符串。从以下选项中选一个：high / medium / low
  high=读完有明确下一步动作；medium=值得记录跟踪；low=只作背景了解
- next_action: 1 条字符串。从以下选项中选一个：write / test / track / ignore / share / build / none
  write=写成笔记；test=动手验证；build=可作为工程基础；track=持续关注；share=值得分享；ignore=暂不处理；none=不确定

要求：
- 全部用中文（display_title / summary / key_signals / why_it_matters / open_questions）
- concepts_tags 必须输出英文概念短语，不要输出中文，不要输出完整句子
- concepts_tags 优先复用 concept_examples 里的稳定概念；但如果材料反复讨论某个特定方法论、新术语或新范式（如 Loop Engineering、Vibe Coding），优先把它提炼为独立概念，而不是用泛概念覆盖
- themes_tags 必须从 theme_examples 里选择英文主题短语，不要输出中文，不要输出完整句子
- themes_tags 是高层导航入口；不要把 Vibe Coding、Context Engineering、某个产品名或短期热点当成 theme
- `display_title` 要简洁、自然，保留必要的产品名/公司名，不要直译得生硬
- 不要把标题小节名机械拷贝成句子
- 不要写"本文介绍了""文章提到"等空话
- 优先写"本质、区别、方法、限制、影响"
- 如果信息不足，就尽量保守，不要瞎编

输入：
- source_kind: {source_kind}
- title: {title}
- author: {author or 'N/A'}
- canonical_url: {canonical_url or 'N/A'}
- entities: {', '.join(entities) if entities else 'N/A'}
- concept_examples: {', '.join(available_concepts)}
- theme_examples: {', '.join(available_themes)}

内容：
{trimmed}
"""
        result = self._call_json(prompt, purpose=f"source_copy {source_kind}: {self._short_label(title)}")
        if not isinstance(result, dict):
            return None
        return {
            "display_title": self._clean_text(result.get("display_title")),
            "summary": self._clean_lines(result.get("summary"), limit=4),
            "key_signals": self._clean_lines(result.get("key_signals"), limit=4),
            "why_it_matters": self._clean_text(result.get("why_it_matters")),
            "open_questions": self._clean_lines(result.get("open_questions"), limit=3),
            "concepts_tags": self._clean_concept_names(result.get("concepts_tags"), limit=5),
            "themes_tags": self._clean_theme_names(result.get("themes_tags"), limit=4),
            "content_type": self._clean_enum_value(result.get("content_type"), _CONTENT_TYPES, "signal"),
            "actionability": self._clean_enum_value(result.get("actionability"), _LEVEL_VALUES, "medium"),
            "next_action": self._clean_enum_value(result.get("next_action"), _ACTION_VALUES, "track"),
        }

    def generate_display_title(
        self,
        *,
        title: str,
        summary: list[str],
        concepts: list[str],
        entities: list[str],
        themes: list[str],
    ) -> str:
        if not self.providers:
            return ""
        prompt = f"""你在为个人知识库生成文章的中文展示标题。

请只输出一个 JSON 对象，格式为：
- display_title: 1 条字符串。10-32 字，适合作为 wiki 页面、列表页、链接文案使用

要求：
- 全部用中文
- 保留必要的产品名、公司名、协议名
- 不要照抄英文原题
- 不要做夸张标题党
- 像一个清楚、简洁、自然的中文文章标题

输入：
- original_title: {title}
- summary: {json.dumps(summary[:3], ensure_ascii=False)}
- concepts: {", ".join(concepts[:6]) if concepts else "N/A"}
- entities: {", ".join(entities[:6]) if entities else "N/A"}
- themes: {", ".join(themes[:6]) if themes else "N/A"}
"""
        result = self._call_json(prompt, purpose=f"display_title: {self._short_label(title)}")
        if not isinstance(result, dict):
            return ""
        return self._clean_text(result.get("display_title"))

    def generate_group_copy(
        self,
        *,
        group_type: str,
        name: str,
        source_summaries: list[dict[str, Any]],
        related_concepts: list[str],
        related_entities: list[str],
        related_themes: list[str],
    ) -> dict[str, Any] | None:
        if not self.providers:
            return None

        compact_sources = []
        for index, item in enumerate(source_summaries[:8], start=1):
            compact_sources.append(
                {
                    "source_key": f"S{index}",
                    "title": item.get("title", ""),
                    "display_title": item.get("display_title", "") or item.get("title", ""),
                    "date": item.get("date", ""),
                    "summary": item.get("summary", [])[:2],
                    "signals": item.get("key_signals", [])[:2],
                    "entity_salience": item.get("entity_salience"),
                    "entity_evidence": item.get("entity_evidence", ""),
                }
            )

        if group_type == "concept":
            prompt = f"""你在为个人知识库生成一个"概念词条页"，风格接近 Wikipedia，但更偏学习和实用理解。

请只输出一个 JSON 对象，包含：
- overview: 2 条数组。每条 35-90 字，告诉读者这个概念应该怎么读、为什么值得学
- quick_takeaways: 3 条数组。每条 25-70 字，给出"3 分钟先抓住什么"
- definition: 1 条字符串。60-140 字，解释"这是什么"
- problem: 1 条字符串。50-120 字，解释"它解决什么问题"
- mechanisms: 3 条数组。每条 35-100 字，解释核心机制/关键观点
- study_path: 3 条数组。每条 35-100 字，告诉用户这个概念应该按什么顺序理解、每一步重点看什么
- examples: 3 条数组。每条 25-80 字，给出这批材料里最典型的例子或体现
- confusions: 2 条数组。每条 25-70 字，说明它容易和什么混淆、差别在哪里
- discussion: 3 条数组。每条 35-100 字，总结这批材料当下主要在怎么讨论这个概念

要求：
- 全部用中文
- 不要照抄 source 标题
- 不要写"本文介绍了""文章提到"等空话
- 要像在给人解释概念，不要像在做日志摘录
- 如果材料不足，就尽量保守，但仍要尽量形成完整解释

输入：
- concept_name: {name}
- related_concepts: {", ".join(related_concepts[:8]) if related_concepts else "N/A"}
- related_entities: {", ".join(related_entities[:8]) if related_entities else "N/A"}
- related_themes: {", ".join(related_themes[:8]) if related_themes else "N/A"}
- source_summaries_json: {json.dumps(compact_sources, ensure_ascii=False)}
"""
        elif group_type == "entity":
            prompt = f"""你在为个人知识库生成一个"对象页"，对象可能是公司、产品、平台、人物或项目。

请只输出一个 JSON 对象，包含：
- keep_page: 布尔值。如果这个对象只是被顺带提到、没有形成稳定讨论，就返回 false
- keep_reason: 1 条字符串。20-80 字，说明为什么保留或为什么不保留
- overview: 2 条数组。每条 30-90 字，告诉读者"这个对象是什么、为什么值得关注、应该从什么角度读"
- definition: 1 条字符串。50-120 字，解释"这是什么"
- why_track: 1 条字符串。40-100 字，解释"为什么值得持续跟踪"
- changes: 3 条数组。每条 35-110 字，只总结"这个对象本身最近在发生什么变化"
- recommended_source_keys: 数组。最多 3 项，只能从提供的 source_key 里选，表示最值得先读的材料
- selected_related_concepts: 数组。最多 6 项，只能从 related_concepts 里选，且必须真的有助于理解这个对象
- selected_related_entities: 数组。最多 6 项，只能从 related_entities 里选，且必须和这个对象存在稳定直接关系
- selected_related_themes: 数组。最多 6 项，只能从 related_themes 里选，且必须是这个对象长期落入的主题

要求：
- 全部用中文
- 不要照抄 source 标题
- 不要写空泛套话
- 你现在扮演"严格的对象页裁判"，不是内容补完器；宁可少选，也不要把弱相关材料硬凑成页
- 必须围绕 `{name}` 这个对象本身来写
- 先解释它是什么，再解释围绕它发生了什么变化、争议、能力、定位或用法
- 不要把同批新闻里的行业趋势硬揉成这个对象的主题
- 如果某条材料只是顺带提到这个对象，不要拿它展开大段论述
- 不要把别的公司、模型、产品的动态写成这个对象的重点，除非它们直接定义了 `{name}` 的定位或变化
- 写成"对象档案页"的感觉，而不是专题综述页
- 你必须优先参考每条材料里的 `entity_salience` 和 `entity_evidence`
- `entity_salience >= 4` 才能视为高显著；`entity_salience <= 2` 基本视为顺带提及，默认不要采用
- 如果这个对象没有在至少两条材料里被当作主要对象、高显著对象或稳定讨论中心，请将 keep_page 设为 false
- 如果只有 1 条材料是真正直接在讲 `{name}`，但另一条材料只是背景或顺带提及，也要返回 keep_page=false
- 只有在"1 条基础定义材料 + 1 条直接更新材料"同时成立时，才允许在两条材料下保留页面
- 选择 recommended_source_keys 时宁缺毋滥；如果只有 1 条真正相关，就只返回 1 条
- `recommended_source_keys` 只能选那些"读完后能直接理解 `{name}`"的材料；弱相关背景稿、行业对比稿、只顺带出现 `{name}` 的稿件一律不要选
- related_* 也要宁缺毋滥；弱相关、顺带提及、只是同批新闻背景的项目一律不要选
- 如果拿不准，就返回空数组，不要凑数
- 如果 keep_page=false，其他字段也尽量保持克制，可以返回空数组和简短理由

输入：
- entity_name: {name}
- related_concepts: {", ".join(related_concepts[:8]) if related_concepts else "N/A"}
- related_entities: {", ".join(related_entities[:8]) if related_entities else "N/A"}
- related_themes: {", ".join(related_themes[:8]) if related_themes else "N/A"}
- source_summaries_json: {json.dumps(compact_sources, ensure_ascii=False)}
"""
        else:
            prompt = f"""你在为个人知识库生成一个"主题页"，目标是帮助用户理解这一组材料共同关心的问题空间。

请只输出一个 JSON 对象，包含：
- overview: 2 条数组。每条 30-90 字，用中文解释这个主题值得怎么读
- discussion: 4 条数组。每条 35-110 字，提炼这一组材料真正共同在讨论的问题

要求：
- 全部用中文
- 不要照抄 source 标题
- 不要写空泛套话
- 要解释这一组材料共同关心的问题，不要退化成链接索引
- 允许提炼趋势，但不要把完全无关的条目硬拼成一个宏大结论
- 写成"专题入口页"的表达，不要像日志或数据库说明

输入：
- theme_name: {name}
- related_concepts: {", ".join(related_concepts[:8]) if related_concepts else "N/A"}
- related_entities: {", ".join(related_entities[:8]) if related_entities else "N/A"}
- related_themes: {", ".join(related_themes[:8]) if related_themes else "N/A"}
- source_summaries_json: {json.dumps(compact_sources, ensure_ascii=False)}
"""
        result = self._call_json(
            prompt,
            purpose=f"group_copy {group_type}: {self._short_label(name)} sources={len(source_summaries)}",
        )
        if not isinstance(result, dict):
            return None
        payload = {
            "overview": self._clean_lines(result.get("overview"), limit=2),
            "discussion": self._clean_lines(result.get("discussion"), limit=4),
        }
        if group_type == "concept":
            payload.update(
                {
                    "quick_takeaways": self._clean_lines(result.get("quick_takeaways"), limit=3),
                    "definition": self._clean_text(result.get("definition")),
                    "problem": self._clean_text(result.get("problem")),
                    "mechanisms": self._clean_lines(result.get("mechanisms"), limit=3),
                    "study_path": self._clean_lines(result.get("study_path"), limit=3),
                    "examples": self._clean_lines(result.get("examples"), limit=3),
                    "confusions": self._clean_lines(result.get("confusions"), limit=2),
                }
            )
        elif group_type == "entity":
            payload.update(
                {
                    "keep_page": bool(result.get("keep_page")),
                    "keep_reason": self._clean_text(result.get("keep_reason")),
                    "definition": self._clean_text(result.get("definition")),
                    "why_track": self._clean_text(result.get("why_track")),
                    "changes": self._clean_lines(result.get("changes"), limit=3),
                    "recommended_source_keys": self._clean_source_keys(result.get("recommended_source_keys")),
                    "selected_related_concepts": self._clean_exact_names(result.get("selected_related_concepts"), related_concepts, limit=6),
                    "selected_related_entities": self._clean_exact_names(result.get("selected_related_entities"), related_entities, limit=6),
                    "selected_related_themes": self._clean_exact_names(result.get("selected_related_themes"), related_themes, limit=6),
                }
            )
        return payload

    def generate_recent_updates_copy(self, *, records: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not self.providers:
            return None
        compact_records = []
        for record in records[:8]:
            compact_records.append(
                {
                    "title": record.get("title", ""),
                    "date": record.get("date", ""),
                    "why_it_matters": record.get("why_it_matters", ""),
                    "summary": list(record.get("summary", []) or [])[:2],
                    "concepts": list(record.get("concepts", []) or [])[:3],
                    "entities": list(record.get("entities", []) or [])[:3],
                }
            )
        prompt = f"""你在为个人知识库生成"最近更新"首页。这一页不是文件列表，而是帮助用户快速知道"最近最值得先看什么、这一轮更新共同在讨论什么、应该怎么读更划算"。

请只输出一个 JSON 对象，包含：
- overview: 2 条数组。每条 45-120 字，用中文概括这一轮更新最值得关注的变化
- key_questions: 3 条数组。每条 25-70 字，概括这轮更新共同在回答的核心问题
- top_picks: 数组。最多 3 项，每项格式为 {{"title":"原始标题", "reason":"35-90字中文理由"}}，表示最值得优先读的条目
- reading_path: 3 条数组。每条 35-100 字，告诉用户应该按什么顺序读、各自看点是什么
- item_notes: 数组。每项格式为 {{"title":"原始标题", "note":"20-60字中文一句话导读"}}，最多 8 项

要求：
- 全部用中文
- 不要泛泛而谈
- 不要照抄标题
- 重点回答"为什么值得先看"和"这轮变化背后的共同主题"
- 保留 title 原文，不要改写 title

输入：
- recent_records_json: {json.dumps(compact_records, ensure_ascii=False)}
"""
        result = self._call_json(prompt, purpose=f"recent_updates records={len(compact_records)}")
        if not isinstance(result, dict):
            return None
        top_picks: list[dict[str, str]] = []
        raw_top_picks = result.get("top_picks", [])
        if isinstance(raw_top_picks, list):
            for item in raw_top_picks:
                if not isinstance(item, dict):
                    continue
                title = self._clean_text(item.get("title"))
                reason = self._clean_text(item.get("reason"))
                if title and reason:
                    top_picks.append({"title": title, "reason": reason})
                if len(top_picks) >= 3:
                    break
        item_notes: list[dict[str, str]] = []
        raw_notes = result.get("item_notes", [])
        if isinstance(raw_notes, list):
            for item in raw_notes:
                if not isinstance(item, dict):
                    continue
                title = self._clean_text(item.get("title"))
                note = self._clean_text(item.get("note"))
                if title and note:
                    item_notes.append({"title": title, "note": note})
                if len(item_notes) >= 8:
                    break
        return {
            "overview": self._clean_lines(result.get("overview"), limit=2),
            "key_questions": self._clean_lines(result.get("key_questions"), limit=3),
            "top_picks": top_picks,
            "reading_path": self._clean_lines(result.get("reading_path"), limit=3),
            "item_notes": item_notes,
        }

    def _build_providers(self) -> list[AIProvider]:
        try:
            return build_providers()
        except ValueError:
            return []

    def _call_json(self, prompt: str, *, purpose: str) -> dict[str, Any] | None:
        for provider in self.providers:
            for attempt in range(3):
                started = time.perf_counter()
                print(
                    f"  [AI:start] purpose={purpose} provider={provider['name']} model={provider['model']} attempt={attempt + 1}/3",
                    flush=True,
                )
                try:
                    text = call_provider_text(
                        provider,
                        prompt,
                        temperature=0.3,
                        max_tokens=2500,
                        timeout=300,
                        retries=1,
                    )
                    payload = json.loads(self._extract_json(text))
                    if isinstance(payload, dict):
                        elapsed = time.perf_counter() - started
                        print(
                            f"  [AI:done] purpose={purpose} provider={provider['name']} attempt={attempt + 1}/3 elapsed={elapsed:.2f}s",
                            flush=True,
                        )
                        return payload
                except AIProviderError as e:
                    elapsed = time.perf_counter() - started
                    status_code = e.status_code
                    response_text = self._short_label(e.response_text, limit=240)
                    print(
                        f"  [AI:fail] purpose={purpose} provider={provider['name']} attempt={attempt + 1}/3 "
                        f"elapsed={elapsed:.2f}s status={status_code} error={e} response={response_text}",
                        flush=True,
                    )
                    if status_code is not None and 400 <= status_code < 500 and status_code != 429:
                        break
                    if attempt < 2:
                        time.sleep(2 ** attempt)
                except Exception as e:
                    elapsed = time.perf_counter() - started
                    print(
                        f"  [AI:fail] purpose={purpose} provider={provider['name']} attempt={attempt + 1}/3 elapsed={elapsed:.2f}s error={e}",
                        flush=True,
                    )
                    if attempt < 2:
                        time.sleep(2 ** attempt)
        print(f"  [AI:empty] purpose={purpose} providers_exhausted=true", flush=True)
        return None

    def _extract_json(self, text: str) -> str:
        return extract_json_text(text)

    def _load_env_file(self, env_path: Path) -> None:
        load_env_file(env_path)

    def _clean_lines(self, value: Any, *, limit: int) -> list[str]:
        if not isinstance(value, list):
            return []
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in value:
            text = self._clean_text(item)
            if not text:
                continue
            key = re.sub(r"\s+", "", text).lower()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(text)
            if len(cleaned) >= limit:
                break
        return cleaned

    def _clean_text(self, value: Any) -> str:
        text = str(value or "").strip()
        text = re.sub(r"^\-\s*", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _clean_source_keys(self, value: Any) -> list[str]:
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return []
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in value:
            text = str(item or "").strip().upper()
            if not re.fullmatch(r"S\d+", text):
                continue
            if text in seen:
                continue
            seen.add(text)
            cleaned.append(text)
            if len(cleaned) >= 3:
                break
        return cleaned

    def _clean_enum_value(self, value: Any, allowed: set[str], default: str) -> str:
        text = str(value or "").strip().lower()
        return text if text in allowed else default

    def _clean_exact_names(self, value: Any, allowed: list[str], *, limit: int) -> list[str]:
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return []
        allowed_map = {str(item).strip().lower(): str(item).strip() for item in allowed if str(item).strip()}
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in value:
            key = str(item or "").strip().lower()
            if not key or key not in allowed_map:
                continue
            canonical = allowed_map[key]
            lowered = canonical.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            cleaned.append(canonical)
            if len(cleaned) >= limit:
                break
        return cleaned

    def _clean_theme_names(self, value: Any, *, limit: int) -> list[str]:
        if isinstance(value, str):
            value = [part.strip() for part in re.split(r"[\n,，;；]+", value) if part.strip()]
        if not isinstance(value, list):
            return []
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in value:
            text = self._normalize_theme_name(item)
            if not text:
                continue
            lowered = text.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            cleaned.append(text)
            if len(cleaned) >= limit:
                break
        return cleaned

    def _clean_concept_names(self, value: Any, *, limit: int) -> list[str]:
        if isinstance(value, str):
            value = [part.strip() for part in re.split(r"[\n,，;；]+", value) if part.strip()]
        if not isinstance(value, list):
            return []
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in value:
            text = self._normalize_concept_name(item)
            if not text:
                continue
            lowered = text.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            cleaned.append(text)
            if len(cleaned) >= limit:
                break
        return cleaned

    def _normalize_concept_name(self, value: Any) -> str:
        text = self._clean_text(value).replace("_", " ")
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
        lowered = text.lower()
        if lowered in {"n/a", "none", "other", "misc", "miscellaneous", "general", "news", "article"}:
            return ""
        if lowered == text:
            text = " ".join(part.capitalize() for part in text.split())
        acronyms = {"AI", "API", "CLI", "GPU", "IDE", "LLM", "MCP", "OCR", "RAG", "SDK", "SQL", "UI", "UX"}
        words: list[str] = []
        for raw_word in text.split():
            parts = raw_word.split("-")
            normalized_parts: list[str] = []
            for part in parts:
                if not part:
                    continue
                upper = part.upper()
                if upper in acronyms or (part.isupper() and len(part) <= 5):
                    normalized_parts.append(upper)
                elif part.islower():
                    normalized_parts.append(part.capitalize())
                else:
                    normalized_parts.append(part)
            if normalized_parts:
                words.append("-".join(normalized_parts))
        text = " ".join(words).strip()
        if not text:
            return ""
        return text

    def _normalize_theme_name(self, value: Any) -> str:
        text = self._clean_text(value).replace("_", " ")
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
        lowered = text.lower()
        if lowered in {"n/a", "none", "other", "misc", "miscellaneous", "general", "news", "article"}:
            return ""
        if lowered == text:
            text = " ".join(part.capitalize() for part in text.split())
        acronyms = {"AI", "API", "CLI", "GPU", "IDE", "LLM", "MCP", "OCR", "RAG", "SDK", "SQL", "UI", "UX"}
        words: list[str] = []
        for raw_word in text.split():
            parts = raw_word.split("-")
            normalized_parts: list[str] = []
            for part in parts:
                if not part:
                    continue
                upper = part.upper()
                if upper in acronyms or (part.isupper() and len(part) <= 5):
                    normalized_parts.append(upper)
                elif part.islower():
                    normalized_parts.append(part.capitalize())
                else:
                    normalized_parts.append(part)
            if normalized_parts:
                words.append("-".join(normalized_parts))
        text = " ".join(words).strip()
        if not text:
            return ""
        return text
