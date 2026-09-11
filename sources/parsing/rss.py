"""
RSS-парсер — портирование parser/sources.py (эталонный скелет, покрытый тестами на реальной
фикстуре ТАСС, см. docs/CODER_INSTRUCTIONS.md, Этап 2) под Django `Source`/`ParsedItem`.

Картинка необязательна (уточнение 07.09.2026, parser/sources.py): если найдена — сохраняем,
если нет — image_url=None, запись всё равно попадает в результат.
"""

import hashlib
from datetime import datetime, timezone
from time import struct_time
from typing import Optional

import feedparser

from .common import ParsedItem, canonicalize_url, is_excluded_category, is_too_old, strip_html


def _extract_image(entry) -> Optional[str]:
    """Best-effort поиск картинки — см. parser/sources.py, _extract_image, за находками по
    типам фидов (WordPress кладёт картинку в content:encoded, не в summary)."""
    media_content = entry.get("media_content")
    if media_content:
        url = media_content[0].get("url")
        if url:
            return url

    media_thumbnail = entry.get("media_thumbnail")
    if media_thumbnail:
        url = media_thumbnail[0].get("url")
        if url:
            return url

    for link in entry.get("links", []):
        if str(link.get("type", "")).startswith("image/"):
            return link.get("href")

    import re

    html_blobs = [entry.get("summary", "")]
    html_blobs.extend(c.get("value", "") for c in entry.get("content", []))
    for blob in html_blobs:
        match = re.search(r'<img[^>]+src="([^"]+)"', blob)
        if match:
            return match.group(1)

    return None


def _parse_published_at(entry) -> Optional[datetime]:
    parsed_time: Optional[struct_time] = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed_time:
        return None
    return datetime(*parsed_time[:6], tzinfo=timezone.utc)


def _full_text(entry) -> str:
    """WordPress-источники кладут полный текст в content:encoded (entry.content) — если есть,
    предпочитаем его summary (дешевле по токенам для редактора при этом полнее), см.
    parser/sources.py докстринг за деталями находки."""
    for c in entry.get("content", []):
        value = c.get("value")
        if value:
            return strip_html(value)
    return strip_html(entry.get("summary", ""))


def entry_to_parsed_item(entry) -> ParsedItem:
    canonical_url = canonicalize_url(entry.link)
    return ParsedItem(
        external_url=canonical_url,
        external_id=entry.get("guid") or entry.get("id") or "",
        title=strip_html(entry.get("title", "")),
        summary=strip_html(entry.get("summary", "")),
        content=_full_text(entry),
        image_url=_extract_image(entry),
        source_published_at=_parse_published_at(entry),
        raw_payload={"link": entry.link, "title": entry.get("title", "")},
    )


def fetch_rss(feed_url: str) -> list[ParsedItem]:
    """Тянет RSS, применяет детерминированные правила фильтрации (возраст, категория
    "обучение"), возвращает нормализованные ParsedItem. Не дедуплицирует — это отдельный шаг
    (sources/parsing/dedup.py), выполняется на уровне Article в sources/tasks.py."""
    parsed = feedparser.parse(feed_url)
    items: list[ParsedItem] = []

    for entry in parsed.entries:
        published_at = _parse_published_at(entry)
        if is_too_old(published_at):
            continue

        category_terms = [tag.get("term") for tag in entry.get("tags", [])]
        if is_excluded_category(category_terms):
            continue

        items.append(entry_to_parsed_item(entry))

    return items


__all__ = ["fetch_rss", "entry_to_parsed_item"]
