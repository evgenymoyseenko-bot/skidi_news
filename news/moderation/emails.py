"""
Письма модератору — одно на новость + еженедельный дайджест зависших в очереди (Этап 4,
docs/DATA_MODEL.md «Модерация по email и очередь публикации», пп.2 и 6). Отправка — через
django.core.mail (backend настроен в config/settings.py на UniSender Go / django-anymail).
"""

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse

from news.models import Article

from .tokens import make_manual_publish_token, make_token


def _action_urls(article: Article) -> dict:
    base = settings.SITE_BASE_URL.rstrip("/")
    return {
        "publish": base + reverse("moderation:action", args=[make_token(article.id, "publish")]),
        "publish_urgent": base + reverse("moderation:action", args=[make_token(article.id, "publish_urgent")]),
        "reject": base + reverse("moderation:action", args=[make_token(article.id, "reject")]),
    }


def _manual_publish_url() -> str:
    """Ссылка на форму срочной ручной публикации новости Клуба (13.09.2026) — не привязана к
    конкретной статье, добавляется в каждое письмо модератору, см. news/moderation/manual_publish.py."""
    base = settings.SITE_BASE_URL.rstrip("/")
    return base + reverse("moderation:manual_publish", args=[make_manual_publish_token()])


def send_moderation_email(article: Article) -> None:
    context = {"article": article, "urls": _action_urls(article), "manual_publish_url": _manual_publish_url()}
    html_body = render_to_string("emails/moderation_single.html", context)
    text_body = render_to_string("emails/moderation_single.txt", context)

    message = EmailMultiAlternatives(
        subject=f"[Модерация] {article.edited_title or article.title}",
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[settings.MODERATION_EMAIL_TO],
    )
    message.attach_alternative(html_body, "text/html")
    message.send()


def send_requeue_digest(articles: list[Article]) -> None:
    if not articles:
        return
    items = [{"article": a, "urls": _action_urls(a)} for a in articles]
    context = {"items": items, "manual_publish_url": _manual_publish_url()}
    html_body = render_to_string("emails/moderation_digest.html", context)
    text_body = render_to_string("emails/moderation_digest.txt", context)

    message = EmailMultiAlternatives(
        subject=f"[Модерация] Еженедельный дайджест — {len(articles)} новостей ждут решения",
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[settings.MODERATION_EMAIL_TO],
    )
    message.attach_alternative(html_body, "text/html")
    message.send()


def send_low_stock_reminder(queue_count: int, needed: int) -> None:
    """Очередь публикации меньше дневной нормы — см. docs/DATA_MODEL.md, п.5. Проверяется
    только на прогоне 06:00 UTC (news/publishing/tasks.py), чтобы не дублировать за день."""
    message = EmailMultiAlternatives(
        subject="[Публикация] Не хватает материала в очереди",
        body=(
            f"В очереди публикации {queue_count} новостей, а на сегодня нужно {needed}. "
            f"Можно опубликовать что-то своё вручную: {_manual_publish_url()}"
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[settings.MODERATION_EMAIL_TO],
    )
    message.send()
