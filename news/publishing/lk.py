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
    (title/source_name/source_url), хэштеги — telegram-специфика, не нужны на карточке сайта.

    Первая строка отбрасывается, только если совпадает с `edited_title` — баг найден
    13.09.2026 на форме ручной публикации без LLM-форматирования: там `edited_post_text` НЕ
    содержит отдельной строки-заголовка (это просто текст модератора), безусловное отбрасывание
    первой строки съедало единственную строку текста целиком → пустой `body` → `publish-news`
    отвечал 400 "title и body обязательны" (см. news/publishing/tasks.py, тот же фикс для
    _format_for_telegram, там же подробное объяснение)."""
    from news.editor.pipeline import SOURCE_LINE_RE

    text = article.edited_post_text or ""
    lines = text.splitlines()

    if lines and lines[0].strip().strip("*").strip() == (article.edited_title or "").strip():
        lines = lines[1:]

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

    body = "\n".join(body_lines).strip()
    if not body:
        # Защита от пустого body → publish-news отвечает 400 (см. докстринг выше про
        # найденный баг) — если фильтрация выше всё-таки съела всё (неучтённый формат
        # edited_post_text), лучше отправить как есть, чем не отправить новость вовсе.
        body = (article.edited_post_text or "").strip()
    return body
