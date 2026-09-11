"""Celery-таска LLM-редактора — запускается со сдвигом после парсинга (см.
news/management/commands/setup_periodic_tasks.py: 06:15/18:15 UTC, чтобы парсинг успел
завершиться — решение кодера, docs/CODER_INSTRUCTIONS.md, Этап 3)."""

from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from news.models import Article

from .client import GigaChatEditorClient
from .pipeline import apply_editor_verdicts, build_prompt, parse_editor_response


@shared_task
def run_editor_batch() -> dict:
    batch = list(
        Article.objects.filter(status=Article.Status.NEW, editor_processed_at__isnull=True)
    )
    if not batch:
        return {"batch_size": 0}

    recent_cutoff = timezone.now() - timedelta(days=settings.EDITOR_RECENT_CONTEXT_DAYS)
    recent_titles = list(
        Article.objects.filter(status=Article.Status.PUBLISHED, published_at__gte=recent_cutoff).values_list(
            "edited_title", flat=True
        )
    )

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
