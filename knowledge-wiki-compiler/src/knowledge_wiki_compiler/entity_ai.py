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


class LLMEntityExtractor:
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

    def extract_entities(
        self,
        *,
        source_kind: str,
        title: str,
        date: str,
        author: str,
        canonical_url: str,
        content: str,
        top_authors: list[str] | None = None,
        top_domains: list[str] | None = None,
    ) -> tuple[list[dict[str, Any]], str]:
        if not self.providers:
            return [], "rules"

        prompt = self._build_prompt(
            source_kind=source_kind,
            title=title,
            date=date,
            author=author,
            canonical_url=canonical_url,
            content=content,
            top_authors=top_authors or [],
            top_domains=top_domains or [],
        )

        purpose = f"entity_extract {source_kind}: {self._short_label(title)}"
        for provider in self.providers:
            result = self._call_provider(provider, prompt, purpose=purpose)
            if result:
                entities = self._normalize_entities(
                    result,
                    author=author,
                    top_authors=top_authors or [],
                )
                if entities:
                    return entities[: self.config.entity_max_count], provider["name"]
        return [], "rules"

    def _build_providers(self) -> list[AIProvider]:
        try:
            return build_providers()
        except ValueError:
            return []

    def _build_prompt(
        self,
        *,
        source_kind: str,
        title: str,
        date: str,
        author: str,
        canonical_url: str,
        content: str,
        top_authors: list[str],
        top_domains: list[str],
    ) -> str:
        trimmed = content[: self.config.entity_max_input_chars]
        ignored_names = [name.strip() for name in [author, *top_authors] if str(name).strip()]
        return f"""你是一个知识库编译器，任务是从材料中做“深度命名实体识别”。

目标：
- 识别对长期知识库有价值的实体
- 优先识别：person, organization, product, project, protocol, standard, event
- 不要把概念词、主题词、普通名词当成实体，例如：AI、agent、workflow、reason、benchmark
- 不要把来源署名、公众号名、媒体账号名、作者名当成实体输出，尤其不要因为 metadata 里出现 author/top_authors 就输出它们
- 不要把 source brand、媒体名、博客名、newsletter 名、频道名当成实体，除非这篇材料本身就是在讨论这个对象
- 能规范化就规范化，例如把同一对象统一成最常见名字
- 最多输出 {self.config.entity_max_count} 个实体
- 给每个实体打 salience，范围 1-5：
  5 = 该实体是这份材料的核心主角或核心对象
  4 = 该实体对理解材料非常重要
  3 = 该实体明确相关，但不是讨论中心
  2 = 该实体只是顺带提及，但仍值得保留
  1 = 该实体只是偶然出现，通常不应该输出
- 如果不确定，就不要乱猜

请只输出一个 JSON 对象，格式如下：
{{
  "entities": [
    {{
      "name": "Anthropic",
      "type": "organization",
      "aliases": ["Claude provider"],
      "confidence": 0.94,
      "salience": 5,
      "evidence": "Why this entity is relevant in one short sentence."
    }}
  ]
}}

输入材料：
- source_kind: {source_kind}
- title: {title}
- date: {date}
- author: {author or "N/A"}
- canonical_url: {canonical_url or "N/A"}
- top_authors: {", ".join(top_authors) if top_authors else "N/A"}
- top_domains: {", ".join(top_domains) if top_domains else "N/A"}
- ignore_as_entities: {", ".join(ignored_names) if ignored_names else "N/A"}

内容：
{trimmed}
"""

    def _call_provider(self, provider: AIProvider, prompt: str, *, purpose: str) -> dict[str, Any] | None:
        for attempt in range(3):
            started = time.perf_counter()
            print(
                f"  [Entity AI:start] purpose={purpose} provider={provider['name']} model={provider['model']} attempt={attempt + 1}/3",
                flush=True,
            )
            try:
                text = call_provider_text(
                    provider,
                    prompt,
                    temperature=0.1,
                    max_tokens=2000,
                    timeout=300,
                    retries=1,
                )
                payload = json.loads(self._extract_json(text))
                if isinstance(payload, dict):
                    elapsed = time.perf_counter() - started
                    print(
                        f"  [Entity AI:done] purpose={purpose} provider={provider['name']} attempt={attempt + 1}/3 elapsed={elapsed:.2f}s",
                        flush=True,
                    )
                    return payload
            except AIProviderError as e:
                elapsed = time.perf_counter() - started
                status_code = e.status_code
                response_text = self._short_label(e.response_text, limit=240)
                print(
                    f"  [Entity AI:fail] purpose={purpose} provider={provider['name']} attempt={attempt + 1}/3 "
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
                    f"  [Entity AI:fail] purpose={purpose} provider={provider['name']} attempt={attempt + 1}/3 elapsed={elapsed:.2f}s error={e}",
                    flush=True,
                )
                if attempt < 2:
                    time.sleep(2 ** attempt)
        print(f"  [Entity AI:empty] purpose={purpose} provider={provider['name']} attempts_exhausted=true", flush=True)
        return None

    def _normalize_entities(
        self,
        payload: dict[str, Any],
        *,
        author: str,
        top_authors: list[str],
    ) -> list[dict[str, Any]]:
        raw_entities = payload.get("entities", [])
        if not isinstance(raw_entities, list):
            return []

        blocked_keys = self._blocked_name_keys(author, top_authors)
        seen: set[str] = set()
        entities: list[dict[str, Any]] = []
        for item in raw_entities:
            if not isinstance(item, dict):
                continue
            aliases = item.get("aliases", [])
            if not isinstance(aliases, list):
                aliases = []
            normalized_item = {
                "type": str(item.get("type", "unknown")).strip() or "unknown",
                "aliases": [str(alias).strip() for alias in aliases if str(alias).strip()],
                "confidence": float(item.get("confidence", 0) or 0),
                "salience": self._normalize_salience(item.get("salience"), confidence=float(item.get("confidence", 0) or 0)),
                "evidence": str(item.get("evidence", "")).strip(),
            }
            if not self._should_keep_entity_type(
                normalized_item["type"],
                salience=normalized_item["salience"],
                confidence=normalized_item["confidence"],
            ):
                continue
            for name in self._split_entity_name(str(item.get("name", "")).strip()):
                if self._should_skip_entity_name(name, blocked_keys):
                    continue
                lowered = name.lower()
                if lowered in seen:
                    continue
                seen.add(lowered)
                entities.append(
                    {
                        "name": name,
                        **normalized_item,
                    }
                )
        return entities

    def _blocked_name_keys(self, author: str, top_authors: list[str]) -> set[str]:
        blocked: set[str] = set()
        for name in [author, *top_authors]:
            key = self._name_key(name)
            if key:
                blocked.add(key)
        return blocked

    def _should_skip_entity_name(self, name: str, blocked_keys: set[str]) -> bool:
        if not name:
            return True
        key = self._name_key(name)
        if not key:
            return True
        if key in blocked_keys:
            return True
        return False

    def _should_keep_entity_type(self, entity_type: str, *, salience: int, confidence: float) -> bool:
        normalized = str(entity_type or "").strip().lower()
        if normalized in {"publication", "media", "channel", "account", "newsletter", "blog"}:
            return salience >= 5 and confidence >= 0.9
        return True

    def _name_key(self, name: str) -> str:
        normalized = re.sub(r"\s+", "", str(name or "")).strip().lower()
        normalized = re.sub(r"[·•\-—_（）()【】\[\]「」『』:：,，。.!?？/\\]+", "", normalized)
        return normalized

    def _normalize_salience(self, raw_value: Any, *, confidence: float) -> int:
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

    def _extract_json(self, text: str) -> str:
        return extract_json_text(text)

    def _load_env_file(self, env_path: Path) -> None:
        load_env_file(env_path)

    def _split_entity_name(self, name: str) -> list[str]:
        if not name:
            return []
        parts = [part.strip() for part in re.split(r"[;；]+", name) if part.strip()]
        if len(parts) <= 1:
            return [name]
        return parts[:4]
