"""Celery-таска LLM-редактора — запускается со сдвигом после парсинга (см.
news/management/commands/setup_periodic_tasks.py: 06:15/18:15 UTC, чтобы парсинг успел
завершиться — решение кодера, docs/CODER_INSTRUCTIONS.md, Этап 3)."""

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from news.models import Article

from .client import GigaChatEditorClient
from .pipeline import _extract_title, apply_editor_verdicts, build_prompt, parse_editor_response

logger = logging.getLogger(__name__)


def _recent_published_titles() -> list[str]:
    recent_cutoff = timezone.now() - timedelta(days=settings.EDITOR_RECENT_CONTEXT_DAYS)
    return list(
        Article.objects.filter(status=Article.Status.PUBLISHED, published_at__gte=recent_cutoff).values_list(
            "edited_title", flat=True
        )
    )


@shared_task
def run_editor_batch() -> dict:
    batch = list(
        Article.objects.filter(status=Article.Status.NEW, editor_processed_at__isnull=True)
    )
    if not batch:
        return {"batch_size": 0}

    recent_titles = _recent_published_titles()

    prompt = build_prompt(batch, recent_titles)
    response_text = GigaChatEditorClient().complete(prompt)
    approved_posts = parse_editor_response(response_text, batch)
    apply_editor_verdicts(batch, approved_posts, quota=settings.EDITOR_QUOTA_PER_BATCH)

    # Письмо модератору уходит сразу по мере обработки редактором, не батчем отдельным
    # расписанием (см. docs/DATA_MODEL.md, п.2) — поэтому таска рассылки запускается прямо
    # отсюда, а не по отдельному cron.
    from news.moderation.tasks import send_moderation_emails

    send_moderation_emails.delay()

    return {
        "batch_size": len(batch),
        "approved_by_llm": len(approved_posts),
        "taken_this_run": min(len(approved_posts), settings.EDITOR_QUOTA_PER_BATCH),
    }


@shared_task
def run_editor_for_manual_article(article_id: int) -> dict:
    """Одноразовый прогон LLM-редактора на ОДНУ статью — форма срочной ручной публикации новости
    Клуба (news/moderation/manual_publish.py, 13.09.2026), в обход расписания/квоты обычного
    `run_editor_batch`. Намеренно НЕ вызывает `apply_editor_verdicts` (та переводит статью в
    `pending_moderation` и уходит на обычную модерацию по почте) — модератор уже принял решение
    публиковать, заполнив форму с `is_urgent=True`; здесь нужен текст поста, а не повторное
    решение "публиковать или нет".

    Критерии отбора в промте (`parser/EDITOR_PROMPT.md`) — про горнолыжные новости источников,
    не про новости Клуба произвольного содержания. Реальный риск: GigaChat может не найти
    "новость по критериям" в тексте модератора и не выдать пост вообще (`parse_editor_response`
    вернёт пустой список) — это НЕ повод потерять срочную новость. В этом случае — fallback на
    заголовок/текст ровно как ввёл модератор (тот же результат, что даёт чекбокс "без
    LLM-форматирования" в форме)."""
    article = Article.objects.get(pk=article_id)
    recent_titles = _recent_published_titles()

    prompt = build_prompt([article], recent_titles)
    response_text = GigaChatEditorClient().complete(prompt)
    posts = parse_editor_response(response_text, [article])

    if posts:
        post = posts[0]
        article.edited_title = _extract_title(post.post_text)
        article.edited_post_text = post.post_text
        article.editor_verdict = "publish"
    else:
        logger.warning(
            "LLM не отформатировал ручную новость Клуба (article=%s) — публикуем как ввёл модератор.",
            article.id,
        )
        article.edited_title = article.title
        article.edited_post_text = article.content
        article.editor_verdict = "reject"  # для аудита: видно, что LLM не смог, но опубликовано

    article.editor_processed_at = timezone.now()
    article.status = Article.Status.APPROVED
    article.save(update_fields=["edited_title", "edited_post_text", "editor_verdict", "editor_processed_at", "status"])

    from news.publishing.tasks import _active_channels, publish_article_now

    telegram_channels, lk_channels = _active_channels()
    publish_article_now(article, telegram_channels, lk_channels)

    return {"article_id": article.id, "used_llm": bool(posts)}
