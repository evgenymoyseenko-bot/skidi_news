"""
Push новости в Личный кабинет (ЛК) — Supabase Edge Function `publish-news`, см.
docs/LK_INTEGRATION_TASK.md, Задача 1. Независимый канал публикации, как Telegram
(news/publishing/telegram.py) — ошибка здесь не должна блокировать публикацию в другие каналы
и наоборот (см. news/publishing/tasks.py, publish_from_queue).
"""

import logging

import requests
from django.conf import settings

from news.models import Article

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 15


class LkPushError(Exception):
    """Не удалось отправить новость в ЛК (сетевая ошибка, таймаут, не-2xx ответ)."""


def push_to_lk(article: Article) -> None:
    payload = {
        "title": article.edited_title,
        "body": format_body_for_lk(article),
        "is_urgent": article.is_urgent,
        "category": article.source.lk_category,
        "source_name": article.source.name,
        "source_url": article.external_url,
    }

    try:
        response = requests.post(
            settings.LK_PUBLISH_NEWS_URL,
            json=payload,
            headers={"Authorization": f"Bearer {settings.LK_NEWS_TOKEN}"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise LkPushError(str(exc)) from exc

    if not response.ok:
        raise LkPushError(f"HTTP {response.status_code}: {response.text[:500]}")


def format_body_for_lk(article: Article) -> str:
    """`edited_post_text` собран для Telegram (заголовок первой строкой, хэштеги в конце,
    строка "Источник: <url>" для сопоставления с картинкой — см. news/editor/pipeline.py). Для
    ЛК нужен только сам текст поста: заголовок и источник уже уходят отдельными полями
    (title/source_name/source_url), хэштеги — telegram-специфика, не нужны на карточке сайта."""
    from news.editor.pipeline import SOURCE_LINE_RE

    text = article.edited_post_text or ""
    lines = text.splitlines()

    if lines:
        lines = lines[1:]  # заголовок — уже в title, здесь не нужен

    body_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if SOURCE_LINE_RE.match(stripped):
            continue
        if stripped.startswith("#"):
            continue
        body_lines.append(stripped.strip("*"))

    return "\n".join(body_lines).strip()
