"""Celery-таски email-модерации — см. docs/DATA_MODEL.md, «Модерация по email и очередь
публикации», пп.2 и 6."""

from celery import shared_task
from django.utils import timezone

from news.models import Article

from .emails import send_moderation_email, send_requeue_digest


@shared_task
def send_moderation_emails() -> dict:
    """Одно письмо на одну новость, сразу как редактор её обработал — не батчем (см. docs/DATA_MODEL.md, п.2).
    `moderation_email_sent_at IS NULL` — защита от повторной отправки."""
    pending = Article.objects.filter(
        status=Article.Status.PENDING_MODERATION, moderation_email_sent_at__isnull=True
    )
    sent = 0
    for article in pending:
        send_moderation_email(article)
        article.moderation_email_sent_at = timezone.now()
        article.save(update_fields=["moderation_email_sent_at"])
        sent += 1
    return {"sent": sent}


@shared_task
def weekly_requeue_digest() -> dict:
    """Понедельник 06:00 UTC — все зависшие approved+неопубликованные уходят одним письмом-
    дайджестом (см. docs/DATA_MODEL.md, п.6)."""
    stuck = list(Article.objects.filter(status=Article.Status.APPROVED, published_at__isnull=True))
    if not stuck:
        return {"digest_size": 0}

    send_requeue_digest(stuck)
    now = timezone.now()
    Article.objects.filter(id__in=[a.id for a in stuck]).update(last_requeue_email_sent_at=now)
    return {"digest_size": len(stuck)}
