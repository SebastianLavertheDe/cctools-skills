#!/usr/bin/env python3
"""
Daily Article Summarizer
Scans project-root mymind/article/ for today's articles, summarizes them with AI, and writes daily markdown output
"""

import os
import sys
import io
import argparse
import importlib.util

# Fix Windows console encoding (GBK -> UTF-8)
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from src.managers.article_scanner import ArticleScanner
from src.managers.cache_manager import CacheManager
from src.managers.summarizer import ArticleSummarizer
import yaml
from runtime_paths import (
    RuntimePathError,
    optional_run_dir,
    require_skill_data_dir,
    require_mymind_root,
    resolve_data_path,
    resolve_mymind_path,
)

MIN_MD_SCORE = 55


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize daily articles into Markdown.")
    parser.add_argument(
        "--date",
        help="Target article date in YYYYMMDD format. Defaults to today.",
    )
    parser.add_argument(
        "--reddit-only",
        action="store_true",
        help="Only summarize Reddit posts for the target date. Skips article scan and same-day post summary.",
    )
    parser.add_argument("--config", default="", help="Package config path; defaults to config.yaml inside this Skill.")
    parser.add_argument("--mymind-root", default="", help="Explicit mymind root; defaults to CCTOOLS_MYMIND_ROOT.")
    parser.add_argument("--skill-data-dir", default="", help="App-private Skill data directory.")
    parser.add_argument("--run-dir", default="", help="App-private Run working directory.")
    args = parser.parse_args()

    if args.date:
        try:
            datetime.strptime(args.date, "%Y%m%d")
        except ValueError as exc:
            raise SystemExit(f"Invalid --date value: {args.date} (expected YYYYMMDD)") from exc

    return args


def load_config(config_file: str = "") -> dict:
    """Load configuration from YAML file"""
    try:
        config_path = Path(config_file).expanduser() if config_file else Path(__file__).resolve().parent / "config.yaml"
        if not config_path.is_absolute():
            config_path = Path(__file__).resolve().parent / config_path
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except Exception as e:
        print(f"Error loading config: {e}")
        return {}


def _select_platform_paths(raw_paths, platform: str):
    if isinstance(raw_paths, dict):
        return raw_paths.get(platform, raw_paths)
    return raw_paths


def _resolve_source_directories(raw_paths, platform: str, mymind_root: Path) -> list[str]:
    selected_paths = _select_platform_paths(raw_paths, platform)
    if isinstance(selected_paths, str):
        selected_paths = [selected_paths]
    elif not selected_paths:
        selected_paths = []

    resolved_paths: list[str] = []
    for raw_path in selected_paths:
        resolved_paths.append(str(resolve_mymind_path(str(raw_path), mymind_root, "source directory")))
    return resolved_paths


def _ensure_summary_file(summary_file: str, date: str) -> None:
    """Create daily summary file with header if missing"""
    if os.path.exists(summary_file):
        return

    with open(summary_file, "w", encoding="utf-8") as f:
        f.write(f"# 📅 {date[:4]}-{date[4:6]}-{date[6:8]} 每日总结\n\n")
        f.write("**共总结 0 篇文章**\n\n")
        f.write(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write("---\n\n")


def _parse_summary_header(summary_file: str) -> tuple[int, str | None]:
    """Parse existing summary file for count and last category"""
    if not os.path.exists(summary_file):
        return 0, None

    count = 0
    last_category = None
    try:
        with open(summary_file, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("**共总结 "):
                    try:
                        count_str = line.split("**共总结 ", 1)[1].split(" 篇文章", 1)[0]
                        count = int(count_str)
                    except Exception:
                        pass
                if line.startswith("## 📚 "):
                    last_category = line.strip()[5:]
    except Exception as e:
        print(f"  Warning: Failed to read summary file header: {e}")

    return count, last_category


def _append_summary(
    summary_file: str,
    summary,
    last_category: str | None,
    project_root: str | None = None,
) -> tuple[str | None, bool]:
    """Append a single summary to the daily file and return (last_category, written)."""
    if summary.score < MIN_MD_SCORE:
        return last_category, False

    with open(summary_file, "a", encoding="utf-8") as f:
        if summary.category != last_category:
            f.write(f"## 📚 {summary.category}\n\n")
            last_category = summary.category

        score_emoji = "⭐" if summary.score >= 80 else "📖" if summary.score >= 60 else "📄"
        f.write(f"### {score_emoji} {summary.title}\n\n")
        f.write(f"**评分**: {summary.score}/100\n\n")
        if summary.author:
            f.write(f"**作者**: {summary.author}\n\n")
        f.write(f"**摘要**:\n{summary.summary}\n\n")

        if summary.key_points:
            f.write("**关键要点**:\n")
            for point in summary.key_points[:5]:
                f.write(f"- {point}\n")
            f.write("\n")

        if summary.source_url:
            f.write(f"**链接**: [{summary.source_url}]({summary.source_url})\n\n")

        if summary.file_path:
            source_path = summary.file_path
            try:
                base_dir = os.path.dirname(summary_file)
                rel_path = os.path.relpath(summary.file_path, base_dir)
                source_path = rel_path
            except Exception:
                if project_root:
                    try:
                        rel_path = os.path.relpath(summary.file_path, project_root)
                        if not rel_path.startswith(".."):
                            source_path = rel_path
                    except Exception:
                        pass
            f.write(f"**源文件**: [{source_path}](<{source_path}>)\n\n")

        f.write("---\n\n")

    return last_category, True


def _resolve_source_path(summary_file: str, file_path: str, project_root: str | None = None) -> str:
    """Resolve a readable source path for markdown output."""
    source_path = file_path
    try:
        base_dir = os.path.dirname(summary_file)
        rel_path = os.path.relpath(file_path, base_dir)
        source_path = rel_path
    except Exception:
        if project_root:
            try:
                rel_path = os.path.relpath(file_path, project_root)
                if not rel_path.startswith(".."):
                    source_path = rel_path
            except Exception:
                pass
    return source_path


def _refresh_cached_summary_paths(
    target_date: str,
    cache_manager: CacheManager,
    articles: list,
    summaries: list,
) -> bool:
    """Refresh cached source file paths when files were renamed after summarization."""
    articles_by_link: dict[str, object] = {}
    articles_by_title_author: dict[tuple[str, str], object] = {}

    for article in articles:
        link = _normalize_whitespace(getattr(article, "link", ""))
        if link and link not in articles_by_link:
            articles_by_link[link] = article

        title = _normalize_whitespace(getattr(article, "title", ""))
        author = _normalize_whitespace(getattr(article, "author", ""))
        if title and (title, author) not in articles_by_title_author:
            articles_by_title_author[(title, author)] = article

    changed = False
    for summary in summaries:
        current_path = str(getattr(summary, "file_path", "") or "").strip()
        if current_path and os.path.exists(current_path):
            continue

        source_url = _normalize_whitespace(getattr(summary, "source_url", ""))
        match = articles_by_link.get(source_url) if source_url else None
        if match is None:
            title = _normalize_whitespace(getattr(summary, "title", ""))
            author = _normalize_whitespace(getattr(summary, "author", ""))
            if title:
                match = articles_by_title_author.get((title, author))

        if match is None:
            if current_path:
                summary.file_path = ""
                cache_bucket = cache_manager.cache.get(target_date, {})
                if summary.cache_key in cache_bucket and isinstance(cache_bucket[summary.cache_key], dict):
                    cache_bucket[summary.cache_key]["file_path"] = ""
                changed = True
            continue

        new_path = str(getattr(match, "file_path", "") or "").strip()
        if not new_path or new_path == current_path:
            continue

        summary.file_path = new_path
        cache_bucket = cache_manager.cache.get(target_date, {})
        if summary.cache_key in cache_bucket and isinstance(cache_bucket[summary.cache_key], dict):
            cache_bucket[summary.cache_key]["file_path"] = new_path
        changed = True

    if changed:
        cache_manager._save_cache()
    return changed


def _render_post_summary_section(
    post_summary: dict | None,
    post_source_path: Path | None,
    project_root: str | None = None,
) -> list[str]:
    """Render post summary section for the daily summary markdown."""
    if not post_source_path:
        return []

    source_display = post_source_path.as_posix()
    if project_root:
        try:
            rel_path = os.path.relpath(post_source_path, project_root)
            if not rel_path.startswith(".."):
                source_display = rel_path
        except Exception:
            pass

    out: list[str] = ["## 🧵 当天 Post 总结", "", f"**来源**: {source_display}"]

    if not post_summary:
        out.extend(["", "未生成当天 Post 总结。", ""])
        return out

    source_count = post_summary.get("source_count")
    candidate_count = post_summary.get("candidate_count")
    if source_count is not None or candidate_count is not None:
        out.append(
            f"**统计**: 原始帖子 {source_count or 0} 条，参与总结 {candidate_count or 0} 条"
        )
    out.append("")

    overview = str(post_summary.get("overview", "") or "").strip()
    if overview:
        out.extend(["**概览**:", overview, ""])

    themes = post_summary.get("themes", [])
    if isinstance(themes, list):
        theme_lines = [str(theme).strip() for theme in themes if str(theme).strip()]
        if theme_lines:
            out.append("**今日主线**:")
            for theme in theme_lines:
                out.append(f"- {theme}")
            out.append("")

    def render_post_items(section_title: str, items: object, section_summary: str = "") -> None:
        if not isinstance(items, list):
            return
        lines = []
        for item in items:
            if not isinstance(item, dict):
                continue
            author_name = str(item.get("author_name", "")).strip() or "未知作者"
            author_screen_name = str(item.get("author_screen_name", "")).strip()
            summary_text = str(item.get("summary", "")).strip()
            link = str(item.get("link", "")).strip()
            if not summary_text:
                continue
            handle = f" (@{author_screen_name})" if author_screen_name else ""
            if link:
                lines.append(f"- **{author_name}{handle}**: {summary_text} [原帖]({link})")
            else:
                lines.append(f"- **{author_name}{handle}**: {summary_text}")
        if lines:
            out.append(section_title)
            if section_summary:
                out.append(section_summary)
            out.extend(lines)
            out.append("")

    category_groups = post_summary.get("categories", [])
    rendered_category = False
    if isinstance(category_groups, list):
        for group in category_groups:
            if not isinstance(group, dict):
                continue
            category_name = str(group.get("name", "")).strip()
            category_summary = str(group.get("summary", "")).strip()
            items = group.get("items", [])
            if not category_name or not isinstance(items, list):
                continue
            before_count = len(out)
            render_post_items(f"**{category_name}**:", items, category_summary)
            if len(out) > before_count:
                rendered_category = True

    if not rendered_category:
        render_post_items("**重点帖子**:", post_summary.get("highlights", []))
        render_post_items("**其他值得关注帖子**:", post_summary.get("secondary_highlights", []))

    error_text = str(post_summary.get("error", "") or "").strip()
    if error_text:
        out.extend([f"**备注**: {error_text}", ""])

    return out


def _write_full_summary_file(
    summary_file: str,
    date: str,
    summaries: list,
    project_root: str | None = None,
    post_summary: dict | None = None,
    post_source_path: Path | None = None,
    reddit_summary: dict | None = None,
    reddit_source_path: str | None = None,
) -> int:
    """Rewrite the daily summary file with post summary and article summaries."""
    deduped_summaries, _ = _dedupe_items(
        summaries,
        key_fn=_summary_identity_key,
        prefer_fn=_prefer_summary,
    )
    visible_summaries = [
        summary for summary in deduped_summaries if getattr(summary, "score", 0) >= MIN_MD_SCORE
    ]

    lines = [
        f"# 📅 {date[:4]}-{date[4:6]}-{date[6:8]} 每日总结",
        "",
        f"**共总结 {len(visible_summaries)} 篇文章**",
        "",
        f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "---",
        "",
    ]

    post_lines = _render_post_summary_section(
        post_summary=post_summary,
        post_source_path=post_source_path,
        project_root=project_root,
    )
    if post_lines:
        lines.extend(post_lines)
        if visible_summaries or reddit_summary:
            lines.extend(["---", ""])

    reddit_lines = _render_reddit_section(
        reddit_summary=reddit_summary,
        reddit_source_path=reddit_source_path,
        project_root=project_root,
    )
    if reddit_lines:
        lines.extend(reddit_lines)
        if visible_summaries:
            lines.extend(["---", ""])

    current_category = None
    sorted_summaries = sorted(visible_summaries, key=lambda s: (s.category, s.title))
    for summary in sorted_summaries:
        if summary.category != current_category:
            lines.extend([f"## 📚 {summary.category}", ""])
            current_category = summary.category

        score_emoji = "⭐" if summary.score >= 80 else "📖" if summary.score >= 60 else "📄"
        lines.extend([f"### {score_emoji} {summary.title}", ""])
        lines.extend([f"**评分**: {summary.score}/100", ""])
        if summary.author:
            lines.extend([f"**作者**: {summary.author}", ""])
        lines.extend(["**摘要**:", summary.summary, ""])

        if summary.key_points:
            lines.append("**关键要点**:")
            for point in summary.key_points[:5]:
                lines.append(f"- {point}")
            lines.append("")

        if summary.source_url:
            lines.extend([f"**链接**: [{summary.source_url}]({summary.source_url})", ""])

        if summary.file_path:
            source_path = _resolve_source_path(summary_file, summary.file_path, project_root)
            lines.extend([f"**源文件**: [{source_path}](<{source_path}>)", ""])

        lines.extend(["---", ""])

    with open(summary_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip() + "\n")

    return len(visible_summaries)


def _load_post_summary_module():
    """Load post summarization helpers from sibling daily-topic-selector.

    Desktop Workflow can still run daily-topic-selector as its own step.
    Local/cron keeps the previous in-process enrichment so
    ``当天 Post 总结`` is filled when the sibling skill exists in the repo.
    Installed packages without that sibling skip this enrichment.
    """
    helper_path = (
        Path(__file__).resolve().parent.parent
        / "daily-topic-selector"
        / "scripts"
        / "generate_daily_topics.py"
    )
    if not helper_path.exists():
        print("  Post summary helper is not bundled; leaving post summarization to Workflow.")
        return None

    helper_dir = str(helper_path.parent)
    if helper_dir not in sys.path:
        sys.path.insert(0, helper_dir)

    spec = importlib.util.spec_from_file_location("daily_topic_post_helpers", helper_path)
    if spec is None or spec.loader is None:
        print(f"  Warning: Failed to load post summary helper from {helper_path}")
        return None

    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        print(f"  Warning: Failed to import post summary helper: {exc}")
        return None
    return module


def _normalize_whitespace(text: str) -> str:
    return " ".join((text or "").split()).strip()


def _parse_datetime_sort_key(value: str | None) -> tuple[int, str]:
    """Return a comparable key for datetime-like strings."""
    normalized = _normalize_whitespace(value or "")
    if not normalized:
        return (1, "")

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return (0, datetime.strptime(normalized, fmt).isoformat())
        except ValueError:
            continue

    return (1, normalized)


def _article_identity_key(article) -> str:
    """Build a stable identity key for deduplicating source articles."""
    link = _normalize_whitespace(getattr(article, "link", ""))
    if link:
        return f"link:{link}"

    title = _normalize_whitespace(getattr(article, "title", ""))
    author = _normalize_whitespace(getattr(article, "author", ""))
    published_date = _normalize_whitespace(getattr(article, "published_date", "") or "")
    if title:
        return f"title:{title}|author:{author}|published:{published_date}"

    return f"file:{getattr(article, 'filename', '')}"


def _summary_identity_key(summary) -> str:
    """Build a stable identity key for deduplicating generated summaries."""
    source_url = _normalize_whitespace(getattr(summary, "source_url", ""))
    if source_url:
        return f"url:{source_url}"

    title = _normalize_whitespace(getattr(summary, "title", ""))
    author = _normalize_whitespace(getattr(summary, "author", ""))
    if title:
        return f"title:{title}|author:{author}"

    return f"file:{os.path.basename(getattr(summary, 'file_path', ''))}"


def _prefer_article(candidate, existing) -> bool:
    """Choose the canonical article when duplicate source files exist."""
    candidate_key = (
        _parse_datetime_sort_key(getattr(candidate, "saved_time", None)),
        _parse_datetime_sort_key(getattr(candidate, "published_date", None)),
        getattr(candidate, "filename", ""),
    )
    existing_key = (
        _parse_datetime_sort_key(getattr(existing, "saved_time", None)),
        _parse_datetime_sort_key(getattr(existing, "published_date", None)),
        getattr(existing, "filename", ""),
    )
    return candidate_key < existing_key


def _prefer_summary(candidate, existing) -> bool:
    """Choose the stronger summary when duplicate source URLs were cached."""
    candidate_key = (
        -int(getattr(candidate, "score", 0) or 0),
        _parse_datetime_sort_key(getattr(candidate, "processed_at", None)),
        os.path.basename(getattr(candidate, "file_path", "")),
    )
    existing_key = (
        -int(getattr(existing, "score", 0) or 0),
        _parse_datetime_sort_key(getattr(existing, "processed_at", None)),
        os.path.basename(getattr(existing, "file_path", "")),
    )
    return candidate_key < existing_key


def _dedupe_items(items: list, key_fn, prefer_fn) -> tuple[list, int]:
    """Deduplicate a sequence while keeping a deterministic canonical item."""
    selected: dict[str, object] = {}
    order: list[str] = []
    duplicates = 0

    for item in items:
        key = key_fn(item)
        existing = selected.get(key)
        if existing is None:
            selected[key] = item
            order.append(key)
            continue

        duplicates += 1
        if prefer_fn(item, existing):
            selected[key] = item

    return [selected[key] for key in order], duplicates


def _truncate_summary_text(text: str, max_length: int) -> str:
    return text[:max_length].rstrip("，、；;:：,. ") + ("…" if len(text) > max_length else "")


def _split_post_content_segments(content: str) -> list[str]:
    segments: list[str] = []
    for block in re.split(r"\n+", content):
        block = _normalize_whitespace(block)
        if not block:
            continue
        for segment in re.split(r"(?<=[。！？!?；;])\s*", block):
            segment = _normalize_whitespace(segment)
            if segment:
                segments.append(segment)
    return segments


def _is_preface_segment(segment: str) -> bool:
    normalized = _normalize_whitespace(segment).lower()
    if not normalized:
        return True

    if re.match(
        r"^(感谢.+分享|感谢.+细节|推荐阅读|推荐一个|转发|转一个|神奇了|坏事儿了|我靠|这个挺实用|我也来说说|感谢实践哥分享)",
        normalized,
    ):
        return True

    signal_terms = (
        "claude",
        "codex",
        "agent",
        "gpt",
        "qwen",
        "gemini",
        "proxy",
        "http_proxy",
        "https_proxy",
        "settings.json",
        "obsidian",
        "插件",
        "代理",
        "配置",
        "报错",
        "403",
        "方法",
        "教程",
        "工作流",
        "开源",
        "发布",
        "模型",
        "工具",
    )
    return len(normalized) <= 18 and not _content_has_any_term(normalized, signal_terms)


def _has_strong_summary_signal(segment: str) -> bool:
    normalized = _normalize_whitespace(segment).lower()
    signal_terms = (
        "claude",
        "codex",
        "agent",
        "gpt",
        "qwen",
        "gemini",
        "proxy",
        "http_proxy",
        "https_proxy",
        "settings.json",
        "obsidian",
        "claudian",
        "插件",
        "代理",
        "配置",
        "报错",
        "403",
        "方法",
        "教程",
        "工作流",
        "开源",
        "发布",
        "模型",
        "工具",
        "路径",
    )
    return (
        _content_has_any_term(normalized, signal_terms)
        or "~/" in segment
        or "/" in segment
        or "{" in segment
        or '"env"' in normalized
    )


def _build_post_display_summary(post: dict, fallback_summary: str = "", max_length: int = 72) -> str:
    summary = _normalize_whitespace(fallback_summary)
    if summary:
        return summary

    content = _normalize_whitespace(str(post.get("content", "")))
    if not content:
        return ""

    content = re.sub(r"https?://\S+", "", content).strip()
    if not content:
        return ""

    segments = _split_post_content_segments(content)
    if not segments:
        return ""

    prefer_informative_original = bool(post.get("is_retweet")) and str(post.get("summarize_target", "")) == "原帖"
    if prefer_informative_original:
        for segment in segments:
            if _is_preface_segment(segment):
                continue
            if _has_strong_summary_signal(segment):
                return _truncate_summary_text(segment, max_length)
        for segment in segments:
            if not _is_preface_segment(segment):
                return _truncate_summary_text(segment, max_length)

    for segment in segments:
        if _is_preface_segment(segment):
            continue
        return _truncate_summary_text(segment, max_length)

    return _truncate_summary_text(segments[0], max_length)


def _content_contains_term(content: str, term: str) -> bool:
    normalized_term = term.strip().lower()
    if not normalized_term:
        return False
    if re.fullmatch(r"[a-z0-9.+#-]+(?: [a-z0-9.+#-]+)*", normalized_term):
        pattern = r"(?<![a-z0-9])" + re.escape(normalized_term).replace(r"\ ", r"\s+") + r"(?![a-z0-9])"
        return re.search(pattern, content) is not None
    return normalized_term in content


def _content_has_any_term(content: str, terms: tuple[str, ...]) -> bool:
    return any(_content_contains_term(content, term) for term in terms)


def _content_term_count(content: str, terms: tuple[str, ...]) -> int:
    return sum(1 for term in terms if _content_contains_term(content, term))


def _post_signal_score(post: dict, watchlist_terms: list[str], priority_authors: set[str]) -> float:
    content = str(post.get("content", "")).lower()
    author_screen_name = str(post.get("author_screen_name", "")).strip().lower()
    verified_bonus = 40 if post.get("verified") else 0
    watchlist_bonus = 18 if any(term in content for term in watchlist_terms) else 0
    priority_author_bonus = 120 if author_screen_name in priority_authors else 0
    retweet_penalty = 10 if post.get("is_retweet") else 0
    return (
        int(post.get("likes", 0))
        + int(post.get("reposts", 0)) * 4
        + int(post.get("quotes", 0)) * 3
        + int(post.get("replies", 0)) * 2
        + int(post.get("bookmarks", 0)) * 2
        + verified_bonus
        + watchlist_bonus
        + priority_author_bonus
        - retweet_penalty
    )


def _build_daily_summary_post_candidates(
    post_source_path: Path,
    watchlist_terms: list[str],
    priority_authors: set[str],
) -> tuple[list[dict], int]:
    """Build broader daily-summary candidates, including retweets and original retweeted content."""
    raw = json.loads(post_source_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        return [], 0

    source_count = len(raw)
    deduped: dict[str, dict] = {}

    for item in raw:
        if not isinstance(item, dict):
            continue

        author = item.get("author", {}) or {}
        metrics = item.get("metrics", {}) or {}
        retweeted_tweet = item.get("retweeted_tweet") or {}

        is_retweet = bool(item.get("is_retweet", False))
        summarize_original = (
            is_retweet
            and isinstance(retweeted_tweet, dict)
            and retweeted_tweet.get("content")
            and retweeted_tweet.get("link")
        )

        if summarize_original:
            content = _normalize_whitespace(str(retweeted_tweet.get("content", "")))
            author_name = str(retweeted_tweet.get("author_name", "")).strip()
            author_screen_name = str(retweeted_tweet.get("author_screen_name", "")).strip()
            tweet_id = str(retweeted_tweet.get("tweet_id", "")).strip()
            link = str(retweeted_tweet.get("link", "")).strip()
            summarize_target = "原帖"
        else:
            content = _normalize_whitespace(str(item.get("content", "")))
            author_name = str(author.get("name", "")).strip()
            author_screen_name = str(author.get("screen_name", "")).strip()
            tweet_id = str(item.get("tweet_id", "")).strip()
            link = str(item.get("link", "")).strip()
            summarize_target = "转发帖" if is_retweet else "原帖"

        if not content:
            quoted_tweet = item.get("quoted_tweet") or {}
            if isinstance(quoted_tweet, dict):
                content = _normalize_whitespace(str(quoted_tweet.get("content", "")))

        if not content:
            continue

        candidate = {
            "tweet_id": tweet_id or str(item.get("tweet_id", "")).strip(),
            "author_name": author_name or str(author.get("name", "")).strip(),
            "author_screen_name": author_screen_name or str(author.get("screen_name", "")).strip(),
            "verified": bool(author.get("verified", False)),
            "content": content,
            "created_at": str(item.get("created_at", "")).strip(),
            "link": link or str(item.get("link", "")).strip(),
            "likes": int(metrics.get("likes", 0) or 0),
            "reposts": int(metrics.get("reposts", 0) or 0),
            "replies": int(metrics.get("replies", 0) or 0),
            "quotes": int(metrics.get("quotes", 0) or 0),
            "bookmarks": int(metrics.get("bookmarks", 0) or 0),
            "is_retweet": is_retweet,
            "summarize_target": summarize_target,
            "surface_author_name": str(author.get("name", "")).strip(),
            "surface_author_screen_name": str(author.get("screen_name", "")).strip(),
        }

        dedupe_key = candidate["tweet_id"] or candidate["link"] or str(item.get("tweet_id", "")).strip()
        existing = deduped.get(dedupe_key)
        if not existing:
            deduped[dedupe_key] = candidate
            continue

        current_score = _post_signal_score(candidate, watchlist_terms, priority_authors)
        existing_score = _post_signal_score(existing, watchlist_terms, priority_authors)
        if current_score > existing_score:
            deduped[dedupe_key] = candidate

    return list(deduped.values()), source_count


def _select_daily_summary_post_candidates(
    posts: list[dict],
    watchlist_terms: list[str],
    priority_authors: set[str],
    limit: int,
) -> list[dict]:
    ranked = sorted(
        posts,
        key=lambda post: (
            _post_signal_score(post, watchlist_terms, priority_authors),
            str(post.get("created_at", "")),
            str(post.get("tweet_id", "")),
        ),
        reverse=True,
    )
    if limit > 0:
        return ranked[:limit]
    return ranked


def _build_daily_summary_post_block(index: int, post: dict, priority_authors: set[str]) -> str:
    author_screen_name = str(post.get("author_screen_name", "")).strip().lower()
    is_priority = author_screen_name in priority_authors
    surface_author_name = str(post.get("surface_author_name", "")).strip()
    surface_author_screen_name = str(post.get("surface_author_screen_name", "")).strip()
    surfaced_by = ""
    if post.get("is_retweet") and surface_author_screen_name:
        surfaced_by = f"{surface_author_name} (@{surface_author_screen_name})"

    lines = [
        f"[{index}] 总结目标: {post.get('summarize_target', '原帖')}",
        f"作者: {post.get('author_name', '')} (@{post.get('author_screen_name', '')}){' verified' if post.get('verified') else ''}",
        f"重点作者: {'是' if is_priority else '否'}",
        f"互动: likes={post.get('likes', 0)}, reposts={post.get('reposts', 0)}, replies={post.get('replies', 0)}, quotes={post.get('quotes', 0)}, bookmarks={post.get('bookmarks', 0)}",
        f"时间: {post.get('created_at', '') or 'unknown'}",
    ]
    if surfaced_by:
        lines.append(f"转发者: {surfaced_by}")
    lines.append(f"内容: {str(post.get('content', ''))[:280]}")
    return "\n".join(lines)


def _is_trivially_low_signal(post: dict) -> bool:
    """Cheap pre-filter: drop posts with no usable text content."""
    content = _normalize_whitespace(str(post.get("content", "")).lower())
    if not content:
        return True
    content = re.sub(r"https?://\S+", "", content).strip()
    if not content:
        return True
    if len(re.sub(r"[\W_]+", "", content, flags=re.UNICODE)) < 8:
        return True
    return False


_CLASSIFY_SYSTEM_PROMPT = (
    "你是中文科技编辑，负责判断社交媒体帖子是否值得收录进每日 AI/科技日报。\n"
    "判断标准：\n"
    "1. 与 AI、大模型、Agent、编程工具、科技产品、科技公司、融资、行业趋势相关 → 保留\n"
    "2. 纯社交互动、吃瓜八卦、情绪发泄、与科技无关 → 丢弃\n"
    "3. 不确定时选择保留\n"
    "输出严格 JSON 格式。"
)

_CLASSIFY_USER_TEMPLATE = (
    "请判断以下帖子是否与 AI/科技相关，值得收录进日报。\n\n"
    "标准：AI/大模型/Agent/编程/科技产品/公司动态/融资/行业趋势 → keep=true\n"
    "纯吃瓜/情绪/生活分享/与科技无关 → keep=false\n"
    "不确定 → keep=true\n\n"
    '输出 JSON：{{"items": [{{"tweet_id": "...", "keep": true/false}}]}}\n\n'
    "帖子列表：\n{posts_text}"
)


def _classify_one_batch(
    helper,
    provider_configs: list,
    batch: list[dict],
) -> set[str]:
    """Call LLM to classify one batch of posts. Returns set of kept tweet_ids."""
    posts_text_parts: list[str] = []
    for idx, post in enumerate(batch, 1):
        content = _normalize_whitespace(str(post.get("content", "")))
        posts_text_parts.append(
            f"[{idx}] tweet_id={post.get('tweet_id', '')}\n"
            f"作者: {post.get('author_name', '')} (@{post.get('author_screen_name', '')})\n"
            f"互动: likes={post.get('likes', 0)}, reposts={post.get('reposts', 0)}, quotes={post.get('quotes', 0)}\n"
            f"内容: {content[:400]}"
        )
    posts_text = "\n\n".join(posts_text_parts)
    user_prompt = _CLASSIFY_USER_TEMPLATE.format(posts_text=posts_text)

    for provider_name, base_url, model, api_key in provider_configs:
        try:
            raw = helper.call_chat_completion(
                base_url, api_key, model,
                _CLASSIFY_SYSTEM_PROMPT, user_prompt,
            )
            if not raw:
                continue
            json_text = helper.extract_json_from_response(raw)
            if not json_text:
                continue
            parsed = json.loads(json_text)
            kept_ids: set[str] = set()
            for item in parsed.get("items", []):
                if item.get("keep"):
                    tid = str(item.get("tweet_id", "")).strip()
                    if tid:
                        kept_ids.add(tid)
            return kept_ids
        except Exception:
            continue

    # All providers failed — keep all posts in batch as fallback
    print(f"    Warning: post classification LLM call failed, keeping all {len(batch)} posts in batch")
    return {str(p.get("tweet_id", "")).strip() for p in batch}


def _classify_post_signal_batch(
    helper,
    posts: list[dict],
    batch_size: int = 25,
) -> list[dict]:
    """Filter posts: keep AI-related posts via LLM classification."""
    # Stage 1: cheap pre-filter
    to_classify = [p for p in posts if not _is_trivially_low_signal(p)]
    trivial_count = len(posts) - len(to_classify)

    # Stage 2: LLM classify all non-trivial posts in batches
    kept: list[dict] = []
    if to_classify:
        provider_configs = helper.choose_ai_providers("auto")
        all_kept_ids: set[str] = set()
        for start in range(0, len(to_classify), batch_size):
            batch = to_classify[start : start + batch_size]
            batch_kept = _classify_one_batch(helper, provider_configs, batch)
            all_kept_ids.update(batch_kept)
        kept = [
            p for p in to_classify
            if str(p.get("tweet_id", "")).strip() in all_kept_ids
        ]

    print(
        f"  Post filter: {len(posts)} candidates → {len(kept)} kept "
        f"(trivially filtered: {trivial_count}, LLM classified: {len(to_classify)})"
    )
    return kept


def _classify_daily_summary_post(post: dict) -> str:
    content = str(post.get("content", "")).lower()
    if _content_has_any_term(
        content,
        ("video", "image", "tts", "audio", "3d", "render", "seedance", "hyperframes", "lyra", "world", "图像", "视频", "语音", "多模态"),
    ):
        return "视频 / 图像 / 多模态"
    if _content_has_any_term(
        content,
        ("paper", "research", "benchmark", "arxiv", "论文", "研究", "基准", "方法", "framework", "memory transfer", "olympiadbench"),
    ):
        return "研究 / 论文 / 方法"
    if _content_has_any_term(
        content,
        ("funding", "valuation", "raised", "acquisition", "ipo", "估值", "融资", "收购", "订阅", "max", "公司", "战略", "市场"),
    ):
        return "公司 / 融资 / 行业动态"
    if _content_has_any_term(
        content,
        ("codex", "claude code", "cursor", "copilot", "cli", "sdk", "github", "tool", "skill", "agent", "开发", "编程", "代码", "工作流", "terminal", "debug", "插件", "浏览器"),
    ):
        return "Agent / 编程 / 开发者工具"
    if _content_has_any_term(
        content,
        ("guide", "tips", "tutorial", "workflow", "实测", "教程", "技巧", "经验", "用法", "推荐", "详解", "总结", "观点"),
    ):
        return "使用技巧 / 实战经验 / 观点"
    return "模型 / 产品发布"


def _summarize_daily_summary_posts_with_ai(
    helper,
    posts: list[dict],
    batch_size: int = 8,
) -> tuple[dict[str, str], str, str]:
    targets = [
        post
        for post in posts
        if str(post.get("tweet_id", "")).strip() and str(post.get("content", "")).strip()
    ]
    if not targets:
        return {}, "", ""

    system_prompt = (
        "你是中文科技编辑，负责把当天帖子总结成日报短句。"
        "必须严格基于给定帖子内容，不得编造。"
        "如果帖子是转帖且内容已经是原帖，请直接总结原帖信息点，不要复述“感谢分享”“转一个”“我试了下”这类社交开场。"
        "每条总结控制在 18-40 个中文字符，尽量具体，适合直接放进每日总结列表。"
        "不要使用“不是...但是...”“应该...而非...”“在于...而非...”“不在于...而在于...”“不...而...”“不...而是...”“不...而在于...”“不是...而是...”“不只有...还有...”“之所以...是因为...”“既是...也是...”等模板化句式，不要写先否定再转折的句子。"
        "直接说观点，多写产品、动作和结论，让句子像自然中文日报短句。"
    )

    provider_configs = helper.choose_ai_providers("auto")
    summaries: dict[str, str] = {}
    provider_meta = {"name": "", "model": ""}

    def request_batch(batch: list[dict]) -> dict[str, str]:
        batch_lines = []
        for idx, post in enumerate(batch, start=1):
            batch_lines.extend(
                [
                    f"[{idx}] tweet_id={post.get('tweet_id', '')}",
                    f"总结目标: {post.get('summarize_target', '原帖')}",
                    f"作者: {post.get('author_name', '')} (@{post.get('author_screen_name', '')})",
                    f"内容: {str(post.get('content', ''))[:1200]}",
                    "",
                ]
            )

        user_prompt = f"""
请总结以下帖子内容。

要求：
1. 每条都输出一句中文总结，适合直接放进日报列表。
2. 不要机械截断原文，不要保留英文长句片段，要压缩成自然中文总结。
3. 不要复述社交礼貌开场，要总结真正的信息点。
4. 如果内容核心是配置、方法、工具更新、实测结论，就直接点明。
5. 如果是转帖且已给出原帖内容，优先总结原帖，不要总结“RT/转发”动作本身。
6. 如果某条实在没有信息量，可以输出空字符串。

风格：
- 尽量像中文日报里的单条摘要
- 能明确产品、功能、动作、结论就明确
- 避免“某人在说”“作者提到”这种空话
- 去掉 AI 味：不要使用“不是...但是...”“应该...而非...”“在于...而非...”“不在于...而在于...”“不...而...”“不...而是...”“不...而在于...”“更多是...而非...”“不是...而是...”“不只有...还有...”“之所以...是因为...”“既是...也是...”等模板句；不要写先否定再转折的句子，直接说观点，用具体场景、动作、产品和结论表达。

输出严格 JSON：
{{
  "items": [
    {{"tweet_id": "123", "summary": "......"}}
  ]
}}

帖子列表：
{chr(10).join(batch_lines)}
"""

        raw = ""
        last_error = ""
        for provider_name, base_url, model, api_key in provider_configs:
            try:
                raw = helper.call_chat_completion(
                    base_url, api_key, model, system_prompt, user_prompt
                )
                if not provider_meta["name"]:
                    provider_meta["name"] = provider_name
                    provider_meta["model"] = model
                break
            except RuntimeError as exc:
                last_error = str(exc)
                continue

        if not raw:
            return {}

        try:
            parsed = json.loads(helper.extract_json_from_response(raw))
        except Exception:
            if last_error:
                print(f"  Warning: daily-summary post AI summary failed: {last_error}")
            return {}

        batch_summaries: dict[str, str] = {}
        for item in parsed.get("items", []):
            if not isinstance(item, dict):
                continue
            tweet_id = str(item.get("tweet_id", "")).strip()
            summary = _normalize_whitespace(str(item.get("summary", "")))
            if tweet_id and summary:
                batch_summaries[tweet_id] = summary
        return batch_summaries

    for start in range(0, len(targets), batch_size):
        batch = targets[start : start + batch_size]
        summaries.update(request_batch(batch))

    missing_targets = [
        post for post in targets if str(post.get("tweet_id", "")).strip() not in summaries
    ]
    for post in missing_targets:
        summaries.update(request_batch([post]))

    return summaries, provider_meta["name"], provider_meta["model"]


def _x_post_cache_key(tweet_id: str) -> str:
    return f"x_{str(tweet_id or '').strip()}"


def _assemble_post_summary(
    kept_posts: list[dict],
    ai_summaries: dict[str, str],
    source_count: int,
) -> dict:
    category_order = [
        "模型 / 产品发布",
        "Agent / 编程 / 开发者工具",
        "视频 / 图像 / 多模态",
        "公司 / 融资 / 行业动态",
        "研究 / 论文 / 方法",
        "使用技巧 / 实战经验 / 观点",
    ]
    grouped: dict[str, list[dict[str, str]]] = {name: [] for name in category_order}

    for post in kept_posts:
        category = _classify_daily_summary_post(post)
        tweet_id = str(post.get("tweet_id", "")).strip()
        grouped.setdefault(category, []).append(
            {
                "author_name": str(post.get("author_name", "")).strip(),
                "author_screen_name": str(post.get("author_screen_name", "")).strip(),
                "link": str(post.get("link", "")).strip(),
                "summary": ai_summaries.get(tweet_id, "") or _build_post_display_summary(post),
            }
        )

    non_empty_categories = [
        {"name": name, "summary": f"共 {len(items)} 条，按帖子质量排序。", "items": items}
        for name in category_order
        for items in [grouped.get(name, [])]
        if items
    ]

    top_categories = sorted(
        ((category["name"], len(category["items"])) for category in non_empty_categories),
        key=lambda item: item[1],
        reverse=True,
    )
    theme_names = [name for name, _ in top_categories[:6]]
    overview = ""
    if top_categories:
        main_categories = "、".join(name for name, _ in top_categories[:3])
        overview = (
            f"今天帖子主要集中在 {main_categories}。"
            f"整体上以有信息增量的更新、工具、研究和行业动态为主，"
            f"已尽量保留非吐槽、非吃瓜内容，并按分类整理。"
        )

    return {
        "overview": overview,
        "themes": theme_names,
        "categories": non_empty_categories,
        "highlights": [],
        "secondary_highlights": [],
        "source_count": source_count,
        "candidate_count": len(kept_posts),
    }


def _classify_and_cache_new_x_posts(
    helper,
    posts: list[dict],
    cache_manager: CacheManager,
    target_date: str,
) -> list[dict]:
    """Classify only uncached X posts and persist keep/drop decisions."""
    if not posts:
        return []

    trivial_dropped = [post for post in posts if _is_trivially_low_signal(post)]
    to_classify = [post for post in posts if not _is_trivially_low_signal(post)]
    for post in trivial_dropped:
        tweet_id = str(post.get("tweet_id", "")).strip()
        if tweet_id:
            cache_manager.mark_x_post_summary(
                _x_post_cache_key(tweet_id),
                keep=False,
                summary="",
                date=target_date,
            )

    kept = _classify_post_signal_batch(helper, to_classify) if to_classify else []
    kept_ids = {str(post.get("tweet_id", "")).strip() for post in kept}
    for post in to_classify:
        tweet_id = str(post.get("tweet_id", "")).strip()
        if tweet_id and tweet_id not in kept_ids:
            cache_manager.mark_x_post_summary(
                _x_post_cache_key(tweet_id),
                keep=False,
                summary="",
                date=target_date,
            )
    return kept


def _build_local_post_summary(
    helper,
    candidates: list[dict],
    source_count: int,
) -> dict:
    kept_posts = _classify_post_signal_batch(helper, candidates)
    ai_summaries, _, _ = _summarize_daily_summary_posts_with_ai(helper, kept_posts)
    return _assemble_post_summary(kept_posts, ai_summaries, source_count)


def _summarize_posts_for_daily_summary(
    helper,
    post_source_path: Path,
    cache_manager: CacheManager,
    target_date: str,
    candidate_limit: int = 0,
) -> tuple[dict, str, str]:
    """Broader post summarization for daily summaries."""
    watchlist_terms = [str(term).lower() for term in getattr(helper, "WATCHLIST_TERMS", [])]
    priority_authors = {
        str(author).strip().lower() for author in getattr(helper, "PRIORITY_POST_AUTHORS", set())
    }

    all_posts, source_count = _build_daily_summary_post_candidates(
        post_source_path=post_source_path,
        watchlist_terms=watchlist_terms,
        priority_authors=priority_authors,
    )
    candidates = _select_daily_summary_post_candidates(
        posts=all_posts,
        watchlist_terms=watchlist_terms,
        priority_authors=priority_authors,
        limit=candidate_limit,
    )
    if not candidates:
        return {
            "overview": "",
            "themes": [],
            "categories": [],
            "highlights": [],
            "source_count": source_count,
            "candidate_count": 0,
        }, "", ""

    # 按 tweet_id 粒度缓存：新帖才分类/总结，旧帖复用；不再用整天快照短路。
    new_candidates: list[dict] = []
    cached_kept = 0
    for post in candidates:
        tweet_id = str(post.get("tweet_id", "")).strip()
        if not tweet_id:
            new_candidates.append(post)
            continue
        cached = cache_manager.get_x_post_summary(_x_post_cache_key(tweet_id))
        if cached is None:
            new_candidates.append(post)
        elif cached.get("keep"):
            cached_kept += 1

    print(
        f"  X posts: {len(candidates)} total, "
        f"{cached_kept} cached kept, {len(new_candidates)} new"
    )

    provider_name = ""
    model = ""
    if new_candidates:
        newly_kept = _classify_and_cache_new_x_posts(
            helper,
            new_candidates,
            cache_manager,
            target_date,
        )
        if newly_kept:
            new_ai_summaries, provider_name, model = _summarize_daily_summary_posts_with_ai(
                helper,
                newly_kept,
            )
            for post in newly_kept:
                tweet_id = str(post.get("tweet_id", "")).strip()
                if not tweet_id:
                    continue
                summary = new_ai_summaries.get(tweet_id, "") or _build_post_display_summary(post)
                cache_manager.mark_x_post_summary(
                    _x_post_cache_key(tweet_id),
                    keep=True,
                    summary=summary,
                    date=target_date,
                )
            cache_manager.flush()
            print(f"  ✓ X post summary: {provider_name}/{model} ({len(newly_kept)} new kept posts)")
        else:
            cache_manager.flush()
            print("  ✓ X post summary: new posts classified, none kept")
    else:
        print(f"  ✓ X post summary: reused {cached_kept} kept posts from per-post cache")

    kept_posts: list[dict] = []
    ai_summaries: dict[str, str] = {}
    for post in candidates:
        tweet_id = str(post.get("tweet_id", "")).strip()
        if not tweet_id:
            continue
        cached = cache_manager.get_x_post_summary(_x_post_cache_key(tweet_id))
        if not cached or not cached.get("keep"):
            continue
        kept_posts.append(post)
        ai_summaries[tweet_id] = str(cached.get("summary", "") or "").strip() or _build_post_display_summary(post)

    post_summary = _assemble_post_summary(kept_posts, ai_summaries, source_count)
    if not provider_name:
        provider_name = "local+ai"
    if not model:
        model = "incremental-x-post-summaries"
    return post_summary, provider_name, model


def _summarize_reddit_posts_with_ai(
    helper,
    posts: list,
    batch_size: int = 30,
) -> tuple[dict, str, str]:
    """Batch-summarize Reddit posts into short one-liners, grouped by subreddit."""
    if not posts:
        return {}, "", ""

    system_prompt = (
        "你是中文科技编辑，负责把 Reddit 帖子总结成日报短句。"
        "必须严格基于给定帖子内容，不得编造。"
        "每条总结控制在 18-40 个中文字符，尽量具体，适合直接放进每日总结列表。"
        "不要使用“不是...但是...”“应该...而非...”“在于...而非...”“不在于...而在于...”“不...而...”“不...而是...”“不...而在于...”“不是...而是...”“不只有...还有...”“之所以...是因为...”“既是...也是...”等模板化句式，不要写先否定再转折的句子。"
        "直接说观点，多写产品、动作和结论，让句子像自然中文日报短句。"
    )

    provider_configs = helper.choose_ai_providers("auto")
    summaries: dict[str, str] = {}
    provider_meta = {"name": "", "model": ""}

    def request_batch(batch: list) -> dict[str, str]:
        batch_lines = []
        for idx, post in enumerate(batch, start=1):
            title = str(post.get("title", "")).strip()
            subreddit = str(post.get("subreddit", "")).strip()
            content = str(post.get("content", ""))[:500]
            post_id = str(post.get("post_id", "")).strip()
            batch_lines.extend([
                f"[{idx}] post_id={post_id}",
                f"板块: r/{subreddit}",
                f"标题: {title}",
                f"内容: {content}",
                "",
            ])

        user_prompt = f"""
请总结以下 Reddit 帖子内容。

要求：
1. 每条都输出一句中文总结，适合直接放进日报列表。
2. 不要机械截断原文，不要保留英文长句片段，要压缩成自然中文总结。
3. 如果内容核心是配置、方法、工具更新、实测结论，就直接点明。
4. 如果某条实在没有信息量，可以输出空字符串。

风格：
- 尽量像中文日报里的单条摘要
- 能明确产品、功能、动作、结论就明确
- 避免"某人在说""作者提到"这种空话
- 去掉 AI 味：不要使用“不是...但是...”“应该...而非...”“在于...而非...”“不在于...而在于...”“不...而...”“不...而是...”“不...而在于...”“更多是...而非...”“不是...而是...”“不只有...还有...”“之所以...是因为...”“既是...也是...”等模板句；不要写先否定再转折的句子，直接说观点，用具体场景、动作、产品和结论表达。

输出严格 JSON：
{{
  "items": [
    {{"post_id": "xxx", "summary": "......"}}
  ]
}}

帖子列表：
{chr(10).join(batch_lines)}
"""

        raw = ""
        last_error = ""
        for provider_name, base_url, model, api_key in provider_configs:
            try:
                raw = helper.call_chat_completion(
                    base_url, api_key, model, system_prompt, user_prompt
                )
                if not provider_meta["name"]:
                    provider_meta["name"] = provider_name
                    provider_meta["model"] = model
                break
            except RuntimeError as exc:
                last_error = str(exc)
                continue

        if not raw:
            return {}

        try:
            parsed = json.loads(helper.extract_json_from_response(raw))
        except Exception:
            if last_error:
                print(f"  Warning: Reddit short-summary AI failed: {last_error}")
            return {}

        batch_summaries: dict[str, str] = {}
        for item in parsed.get("items", []):
            if not isinstance(item, dict):
                continue
            post_id = str(item.get("post_id", "")).strip()
            summary = _normalize_whitespace(str(item.get("summary", "")))
            if post_id and summary:
                batch_summaries[post_id] = summary
        return batch_summaries

    for start in range(0, len(posts), batch_size):
        batch = posts[start : start + batch_size]
        summaries.update(request_batch(batch))

    missing = [p for p in posts if str(p.get("post_id", "")).strip() not in summaries]
    for post in missing:
        summaries.update(request_batch([post]))

    return summaries, provider_meta["name"], provider_meta["model"]


def _generate_reddit_summary(
    target_date: str,
    project_root: str,
    cache_manager: CacheManager,
) -> tuple[dict, str | None]:
    """Generate short Reddit summaries grouped by subreddit."""
    reddit_dir = os.path.join(str(project_root), "reddit", target_date)
    if not os.path.exists(reddit_dir):
        return {}, None

    # 按帖粒度缓存（reddit_{sub}_{post_id}）：新帖才总结，旧帖复用。
    # 不再用整天快照短路——fetcher 后续补抓的板块会被漏掉（曾导致全天只剩 1 个板块）。
    scanner = ArticleScanner([os.path.join(str(project_root), "reddit")])
    reddit_posts = scanner.get_reddit_posts_by_date(target_date)
    if not reddit_posts:
        return {}, None

    helper = _load_post_summary_module()
    if helper is None:
        print("  Reddit summary helper is not bundled; leaving Reddit summarization to Workflow.")
        return {}, None

    subreddit_pattern = re.compile(r"^\[r/(.+?)\]\s*(.+)$")

    flat_posts = []
    for post in reddit_posts:
        title = str(post.title).strip()
        m = subreddit_pattern.match(title)
        subreddit = m.group(1) if m else "unknown"
        clean_title = m.group(2) if m else title

        post_id_match = re.search(r"/comments/([a-z0-9]+)/", str(post.link or ""))
        post_id = post_id_match.group(1) if post_id_match else post.cache_key

        flat_posts.append({
            "post_id": post_id,
            "subreddit": subreddit,
            "title": clean_title,
            "content": str(post.content or "")[:500],
            "link": str(post.link or ""),
            "author": str(post.author or ""),
            "cache_key": post.cache_key,
        })

    # 命中 per-post 缓存的直接复用，剩下的才发去 AI
    summaries: dict[str, str] = {}
    new_posts: list[dict] = []
    for post in flat_posts:
        cached_summary = cache_manager.get_reddit_post_summary(post["cache_key"])
        if cached_summary:
            summaries[post["post_id"]] = cached_summary
        else:
            new_posts.append(post)

    print(
        f"  Reddit posts: {len(flat_posts)} total, "
        f"{len(flat_posts) - len(new_posts)} cached, {len(new_posts)} new"
    )

    provider_name = ""
    model = ""
    if new_posts:
        new_summaries, provider_name, model = _summarize_reddit_posts_with_ai(helper, new_posts)
        summaries.update(new_summaries)
        for post in new_posts:
            summary = new_summaries.get(post["post_id"], "")
            if summary:
                cache_manager.mark_reddit_post_summary(
                    post["cache_key"], summary, date=target_date
                )
        cache_manager.flush()
        print(f"  ✓ Reddit summary: {provider_name}/{model} ({len(new_posts)} new posts)")
    else:
        print(f"  ✓ Reddit summary: reused {len(flat_posts)} posts from per-post cache")

    subreddits: dict[str, list[dict]] = {}
    for post in flat_posts:
        sub = post["subreddit"]
        ai_summary = summaries.get(post["post_id"], "")
        subreddits.setdefault(sub, []).append({
            "title": post["title"],
            "summary": ai_summary or post["title"],
            "link": post["link"],
            "author": post["author"],
        })

    reddit_summary = {
        "subreddits": subreddits,
        "total_posts": len(flat_posts),
    }

    # 整天快照仅作「最近一次渲染」记录，不再用于短路
    cache_manager.mark_reddit_summary(
        target_date,
        reddit_summary,
        source_path=reddit_dir,
        provider_name=provider_name,
        model=model,
    )

    return reddit_summary, reddit_dir


def _render_reddit_section(
    reddit_summary: dict | None,
    reddit_source_path: str | None,
    project_root: str | None = None,
) -> list[str]:
    """Render compact Reddit section for the daily summary markdown."""
    if not reddit_summary:
        return []

    out: list[str] = ["## 💬 Reddit 热帖", ""]

    source_display = reddit_source_path or ""
    if project_root and source_display:
        try:
            rel_path = os.path.relpath(source_display, project_root)
            if not rel_path.startswith(".."):
                source_display = rel_path
        except Exception:
            pass
    if source_display:
        out.append(f"**来源**: {source_display}")

    total = reddit_summary.get("total_posts", 0)
    subreddits = reddit_summary.get("subreddits", {})
    subreddit_count = len(subreddits)
    if total:
        out.append(f"**统计**: {subreddit_count} 个板块，{total} 条帖子")
    out.append("")

    for sub_name in sorted(subreddits.keys()):
        items = subreddits[sub_name]
        out.append(f"### r/{sub_name}")
        out.append("")
        for item in items:
            title = str(item.get("title", "")).strip()
            summary = str(item.get("summary", "")).strip()
            link = str(item.get("link", "")).strip()
            display = summary if summary else title
            if link:
                out.append(f"- {display} [原帖]({link})")
            else:
                out.append(f"- {display}")
        out.append("")

    return out


def _generate_post_summary(
    target_date: str,
    project_root: str,
    cache_manager: CacheManager,
) -> tuple[dict, Path | None]:
    """Summarize same-day posts.json using incremental per-tweet cache."""
    post_source_path = Path(project_root) / "post" / target_date / "posts.json"
    if not post_source_path.exists():
        return {}, None

    helper = _load_post_summary_module()
    if helper is None:
        print("  Post summary helper is not bundled; leaving post summarization to Workflow.")
        return {}, post_source_path

    try:
        post_summary, provider_name, model = _summarize_posts_for_daily_summary(
            helper=helper,
            post_source_path=post_source_path,
            cache_manager=cache_manager,
            target_date=target_date,
            candidate_limit=0,
        )
        print(f"  ✓ Post summary assembled: {provider_name}/{model}")
        if isinstance(post_summary, dict) and not post_summary.get("error"):
            cache_manager.mark_post_summary(
                target_date,
                post_summary,
                source_path=str(post_source_path),
                provider_name=provider_name,
                model=model,
            )
        return post_summary, post_source_path
    except Exception as e:
        print(f"  Warning: Failed to summarize posts for {target_date}: {e}")
        return {"error": str(e)}, post_source_path


def main() -> int:
    """Main execution pipeline"""
    try:
        args = parse_args()
        print("=" * 50)
        print("Daily Article Summarizer")
        print("=" * 50)

        # Load configuration
        mymind_root = require_mymind_root(args.mymind_root)
        skill_data_dir = require_skill_data_dir(args.skill_data_dir)
        optional_run_dir(args.run_dir)
        config = load_config(args.config)
        # Resolve platform-specific directories
        _platform = "windows" if sys.platform == "win32" else "linux"
        project_root = mymind_root
        _raw_dirs = config.get("article_directory", "mymind/article")
        article_dir = _resolve_source_directories(_raw_dirs, _platform, project_root)
        _raw_reddit = config.get("reddit_directory", ["mymind/reddit"])
        reddit_dir = _resolve_source_directories(_raw_reddit, _platform, project_root)
        ai_config = config.get("ai", {})
        cache_file = str(resolve_data_path(str(config.get("cache_file", "summary_cache.json")), skill_data_dir, "cache_file"))

        # Resolve target date
        target_date = args.date or datetime.now().strftime("%Y%m%d")
        print(f"\n📅 Date: {target_date}")

        # Prepare summary output path
        summary_dir = str(project_root / "daily-summary")
        os.makedirs(summary_dir, exist_ok=True)
        summary_file = os.path.join(summary_dir, f"{target_date}_daily_summary.md")

        # Step 1: Scan sources
        raw_articles = []
        if args.reddit_only:
            print("\n📂 Reddit-only mode enabled")
        else:
            print(f"\n📂 Scanning articles from {article_dir}/{target_date}...")
            scanner = ArticleScanner(article_dir)
            raw_articles = scanner.get_articles_by_date(target_date)

        articles, duplicate_articles = _dedupe_items(
            raw_articles,
            key_fn=_article_identity_key,
            prefer_fn=_prefer_article,
        )
        print(f"  Found {len(raw_articles)} articles")
        if duplicate_articles:
            print(f"  Deduped duplicate source articles: {duplicate_articles}")

        # Step 2: Check cache
        print(f"\n💾 Checking cache...")
        cache_manager = CacheManager(cache_file)
        raw_cached_summaries = cache_manager.get_all_summaries_for_date(target_date)
        deduped_cached_summary_list, duplicate_cached_summaries = _dedupe_items(
            list(raw_cached_summaries.values()),
            key_fn=_summary_identity_key,
            prefer_fn=_prefer_summary,
        )
        cached_summaries = {
            summary.cache_key: summary
            for summary in deduped_cached_summary_list
        }
        refreshed_paths = _refresh_cached_summary_paths(
            target_date,
            cache_manager,
            articles,
            deduped_cached_summary_list,
        )
        if refreshed_paths:
            print("  Refreshed cached summary source paths")
        cached_summary_keys = {
            _summary_identity_key(summary) for summary in deduped_cached_summary_list
        }
        new_articles = [
            a
            for a in articles
            if not cache_manager.is_summarized(target_date, a.cache_key)
            and _article_identity_key(a) not in cached_summary_keys
        ]

        print(f"  New articles: {len(new_articles)}")
        print(f"  Cached summaries: {len(cached_summaries)}")
        if duplicate_cached_summaries:
            print(f"  Deduped cached duplicate summaries: {duplicate_cached_summaries}")

        # Step 2.5: Summarize same-day posts
        post_summary = {}
        post_source_path = None
        if args.reddit_only:
            print("\n🧵 Skipping same-day post summary in reddit-only mode")
        else:
            print(f"\n🧵 Summarizing same-day posts...")
            post_summary, post_source_path = _generate_post_summary(
                target_date, project_root, cache_manager
            )

        # Step 2.6: Summarize Reddit posts (short format)
        reddit_summary = {}
        reddit_source_path = None
        print(f"\n💬 Summarizing Reddit posts...")
        reddit_summary, reddit_source_path = _generate_reddit_summary(
            target_date, project_root, cache_manager
        )

        if not articles and not post_source_path and not reddit_source_path:
            print("\n✅ No articles, posts, or Reddit content to process")
            return

        existing_count = 0
        last_category = None
        total_written = 0

        if new_articles or cached_summaries:
            _ensure_summary_file(summary_file, target_date)
            existing_count, last_category = _parse_summary_header(summary_file)
            total_written = existing_count

        # If file is new but we already have cached summaries, append them once
        if existing_count == 0 and cached_summaries:
            print(f"\n📄 Writing cached summaries to local Markdown...")
            for summary in sorted(
                cached_summaries.values(), key=lambda s: (s.category, s.title)
            ):
                last_category, written = _append_summary(
                    summary_file, summary, last_category, project_root
                )
                if written:
                    total_written += 1

        if not new_articles:
            print("\n✅ All articles already summarized")
            final_summaries = list(cache_manager.get_all_summaries_for_date(target_date).values())
            total_written = _write_full_summary_file(
                summary_file=summary_file,
                date=target_date,
                summaries=final_summaries,
                project_root=project_root,
                post_summary=post_summary,
                post_source_path=post_source_path,
                reddit_summary=reddit_summary,
                reddit_source_path=reddit_source_path,
            )
            print(f"  ✅ Summary available at: {summary_file}")
            print(f"  ✅ Final article count: {total_written}")
            return

        # Step 3: Summarize new articles and incrementally write results
        print(f"\n🤖 Summarizing {len(new_articles)} new articles...")
        summarizer = ArticleSummarizer()
        batch_size = ai_config.get("batch_size", 5)
        new_summaries = []

        # 常驻线程池（并发度不再受 batch_size 限制），流式提交全部文章。
        # mark_as_summarized 进内存 dirty，每完成 batch_size 篇 flush 一次（DAS-1）。
        worker_count = max(1, min(8, len(new_articles))) if new_articles else 1
        print(f"\n  Summarizing {len(new_articles)} articles with {worker_count} workers...")
        completed_since_flush = 0

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_to_article = {
                executor.submit(summarizer.summarize_article, article, target_date): article
                for article in new_articles
            }
            for future in as_completed(future_to_article):
                article = future_to_article[future]
                try:
                    summary = future.result()
                except Exception as e:
                    print(f"  Warning: Failed to summarize {article.filename}: {e}")
                    continue
                if not summary:
                    continue

                new_summaries.append(summary)
                cache_manager.mark_as_summarized(target_date, summary.cache_key, summary)
                last_category, written = _append_summary(
                    summary_file, summary, last_category, project_root
                )
                if written:
                    total_written += 1

                completed_since_flush += 1
                if completed_since_flush >= batch_size:
                    cache_manager.flush()
                    completed_since_flush = 0

        # 末尾 flush 兜底（最后一批可能不足 batch_size）
        cache_manager.flush()

        print(f"\n  Successfully summarized: {len(new_summaries)}/{len(new_articles)}")
        final_summaries = list(cache_manager.get_all_summaries_for_date(target_date).values())
        total_written = _write_full_summary_file(
            summary_file=summary_file,
            date=target_date,
            summaries=final_summaries,
            project_root=project_root,
            post_summary=post_summary,
            post_source_path=post_source_path,
            reddit_summary=reddit_summary,
            reddit_source_path=reddit_source_path,
        )
        print(f"  ✅ Saved to: {summary_file}")
        print(f"  ✅ Final article count: {total_written}")

        print(f"\n✅ Done! Processed {len(new_summaries)} new articles")
        return 0

    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user")
        return 130
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback

        traceback.print_exc()
        return 1
    finally:
        print(f"\n⏰ Finished at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    raise SystemExit(main())
