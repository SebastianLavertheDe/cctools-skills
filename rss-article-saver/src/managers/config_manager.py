"""
Configuration Manager - Loads YAML config
"""

import os
import sys
import yaml
from typing import Dict
from pathlib import Path

from ..runtime_paths import require_skill_data_dir, require_mymind_root, resolve_data_path, resolve_mymind_path


class RSSConfig:
    """RSS configuration management"""

    def __init__(
        self,
        config_file: str = "",
        mymind_root: str | None = None,
        skill_data_dir: str | None = None,
    ):
        skill_root = Path(__file__).resolve().parents[2]
        config_path = Path(config_file).expanduser() if config_file else skill_root / "config.yaml"
        if not config_path.is_absolute():
            config_path = skill_root / config_path
        self.config_file = str(config_path.resolve())
        self.skill_root = skill_root
        self.mymind_root = require_mymind_root(mymind_root)
        self.skill_data_dir = require_skill_data_dir(skill_data_dir)
        self.config = self._load_config()

    def _load_config(self) -> Dict:
        """Load YAML configuration"""
        try:
            if not os.path.exists(self.config_file):
                raise FileNotFoundError(f"Config file {self.config_file} not found")

            with open(self.config_file, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
                print(f"Loaded config: {self.config_file}")
                return config
        except Exception as e:
            print(f"Failed to load config: {e}")
            sys.exit(1)

    def get_article_base_dir(self) -> str:
        """Get article base directory (supports ~ and relative paths)"""
        raw = str(self.config.get('article_base_dir', '') or 'article')
        return str(resolve_mymind_path(raw, self.mymind_root, "article_base_dir"))

    def get_opml_file(self) -> str:
        """Get OPML file path"""
        raw = str(self.config.get('opml_file', 'subscriptions.opml'))
        if raw.startswith(("http://", "https://")):
            return raw
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = self.skill_root / candidate
        return str(candidate.resolve())

    def get_cache_file(self) -> str:
        return str(resolve_data_path("article_cache.json", self.skill_data_dir, "article cache"))

    def get_counter_file(self) -> str:
        return str(resolve_data_path("article-counter.json", self.skill_data_dir, "article counter"))

    def get_max_articles_per_feed(self) -> int:
        """Get max articles to process per feed"""
        return self.config.get('max_articles_per_feed', 20)

    def get_max_article_age_days(self) -> int:
        """Get max article age in days (0 = no filter)"""
        return self.config.get('max_article_age_days', 0)

    def get_content_settings(self) -> Dict:
        """Get content extraction settings"""
        return self.config.get('content', {})

    def get_ai_settings(self) -> Dict:
        """Get AI processing settings"""
        return self.config.get('ai', {})

    def get_translation_settings(self) -> Dict:
        """Get translation settings"""
        return self.config.get('translation', {'enabled': False, 'provider': 'deepseek'})
