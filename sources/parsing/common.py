"""
Общие правила фильтрации/нормализации для RSS и HTML-парсеров — портировано из эталонного
скелета `parser/sources.py` (см. docs/CODER_INSTRUCTIONS.md, Этап 2). Только детерминированные
признаки (наличие поля, точный тег, сравнение дат) — субъективная оценка релевантности остаётся
за LLM-редактором (news/editor), парсер её не делает.
"""

import html as html_module
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Не старше этого числа дней — правило от 03.09.2026 (parser/sources.py). Запись без
# распознанной даты тоже считается устаревшей (нельзя подтвердить свежесть без даты).
MAX_ARTICLE_AGE_DAYS = 30

# Подстроки (нижний регистр) категорий, исключающие запись — см. parser/sources.py.
EXCLUDED_CATEGORY_SUBSTRINGS = {"обучен"}

_TRACKING_PARAMS_PREFIXES = ("utm_", "yclid", "fbclid", "gclid")


@dataclass
class ParsedItem:
    """Нормализованная запись перед сохранением в Article — единый формат для RSS и HTML."""

    external_url: str
    title: str
    summary: str = ""
    content: str = ""
    external_id: str = ""
    image_url: Optional[str] = None
    source_published_at: Optional[datetime] = None
    raw_payload: dict = field(default_factory=dict)


def canonicalize_url(url: str) -> str:
    """Канонический вид URL для дедупа по точному совпадению: нижний регистр хоста, без
    трекинг-параметров, без trailing slash."""
    parts = urlsplit(url)
    netloc = parts.netloc.lower()
    path = parts.path.rstrip("/") or "/"
    query_pairs = [
        (k, v)
        for k, v in parse_qsl(parts.query)
        if not k.lower().startswith(_TRACKING_PARAMS_PREFIXES)
    ]
    query = urlencode(query_pairs)
    return urlunsplit((parts.scheme.lower(), netloc, path, query, ""))


def strip_html(raw_html: str) -> str:
    if not raw_html:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw_html)
    text = html_module.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def is_too_old(published_at: Optional[datetime], *, now: Optional[datetime] = None) -> bool:
    """True, если запись старше MAX_ARTICLE_AGE_DAYS дней либо дата не распознана — см.
    parser/sources.py, _is_too_old, за обоснованием дефолта "нет даты — считаем устаревшей"."""
    if published_at is None:
        return True
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=MAX_ARTICLE_AGE_DAYS)
    return published_at < cutoff


def is_excluded_category(category_terms: list[str]) -> bool:
    for term in category_terms:
        term_lower = (term or "").lower()
        if any(substr in term_lower for substr in EXCLUDED_CATEGORY_SUBSTRINGS):
            return True
    return False
