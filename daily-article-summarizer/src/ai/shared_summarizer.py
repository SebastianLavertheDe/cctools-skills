"""Article summarizer adapter backed by the package-local AI client."""

from __future__ import annotations

import json
from typing import Dict, Optional

from ..ai_client import call_provider_text, extract_json_text, resolve_provider


class SharedArticleSummarizer:
    provider_name = ""
    display_name = ""

    def __init__(self):
        self.provider = resolve_provider()
        self.model = self.provider.model
        self.base_url = self.provider.base_url

    def summarize_article(
        self, title: str, content: str, max_retries: int = 3
    ) -> Optional[Dict]:
        if len(content) > 8000:
            content = content[:8000] + "..."

        prompt = build_daily_summary_prompt(title, content)

        try:
            content_text = call_provider_text(
                self.provider,
                prompt,
                temperature=0.3,
                max_tokens=4000,
                timeout=120,
                retries=max_retries,
                response_format_json=True,
            )
        except Exception as e:
            print(f"    Error: {self.display_name} summarization failed: {e}")
            return None

        if not content_text:
            print(f"    Warning: Empty response from {self.display_name} API")
            return None

        json_str = extract_json_text(content_text)
        try:
            result = json.loads(json_str)
        except json.JSONDecodeError as e:
            print(f"    Warning: Failed to parse {self.display_name} JSON response: {e}")
            print(f"    Response was: {json_str[:200]}...")
            return None

        required = ["translated_title", "summary", "key_points", "category", "score"]
        if not all(key in result for key in required):
            print(f"    Warning: Missing required fields in {self.display_name} response")
            return None

        return {
            "translated_title": result.get("translated_title", title),
            "summary": result.get("summary", ""),
            "key_points": result.get("key_points", []),
            "category": result.get("category", "Other"),
            "score": int(result.get("score", 70)),
        }


def build_daily_summary_prompt(title: str, content: str) -> str:
    return f"""请忠实总结以下文章或帖子，并对原文信息质量进行评分。

## 文章标题
{title}

## 文章内容
{content}

## 要求
请以JSON格式输出总结，包含以下字段：
- translated_title: 翻译后的中文标题（准确概括原文即可，不要扩写成原文没有表达的主题）
- summary: 80-200字的中文摘要，只概括原文明确写出的内容
- key_points: 0-5个关键点（数组；每条只写原文明确出现的信息）
- category: 文章分类（只能从 AI, System Design, Backend, Frontend, DevOps, Science, Writing, Startup, Prompt, Other 之一选择）
- score: 原文信息质量评分（0-100的整数；重点看原文是否有足够信息、事实是否清楚、是否值得阅读）

## 忠实性要求
- 严格基于“文章内容”总结，不要根据标题、链接、图片URL、Reddit板块名、常识、历史知识或外部新闻补全信息。
- 不要加入你自己的理解、判断、背景解释、行业意义、战略意图、延伸分析或未在原文出现的结论。
- 如果原文只有标题、链接、图片URL、视频URL、占位文本、空正文，或信息不足以总结，请直接说明无法总结的原因。
- 无法总结时仍输出JSON：translated_title使用标题的直译或原题；summary写“原文信息不足，无法总结：...”并说明具体原因；key_points输出空数组；category用Other；score给0-20。

## 表达风格
- 不要使用这些模板化句式：不是...但是...、应该...而非...、在于...而非...、不在于...而在于...、不...而...、不...而是...、不...而在于...、更多是...而非...、不应是...而应该是...、不只是...还有...、不只有...还有...、不是...而是...、不再是...而是...、之所以...是因为...、既是...也是...
- 不要写先否定再用“而/而是/而在于”转折的句子，直接说事实和结论。
- 用简洁自然的中文复述原文事实，不要为了好看而加工成观点文。
- summary 和 key_points 可以短，不要为了凑字数添加原文没有的信息。

## 输出约束
- 只输出一个JSON对象，不要包含其他说明、前后缀文本或代码块标记
- translated_title、summary 必须为中文
- key_points 必须是字符串数组
- category 必须严格匹配枚举值之一
- score 必须是整数（0-100）

只输出JSON，不要包含其他说明或代码块标记。"""
