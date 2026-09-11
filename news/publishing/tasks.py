"""
Публикация из очереди (Этап 5, docs/DATA_MODEL.md пп.4-5) — 2 записи за прогон, срочные
первыми, дальше по source_published_at (старые вперёд). Без верхнего лимита очереди — лишнее
просто ждёт следующего прогона.
"""

import html
import logging

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from news.editor.pipeline import SOURCE_LINE_RE
from news.models import Article, PublicationLog, PublishChannel

from .telegram import TelegramPublisher

logger = logging.getLogger(__name__)

BATCH_SIZE = 2
DAILY_TARGET = 4  # 2 записи x 2 прогона/день, см. docs/DATA_MODEL.md, п.5


def _publication_queue():
    return Article.objects.filter(
        status=Article.Status.APPROVED, published_at__isnull=True
    ).order_by("-is_urgent", "source_published_at")


def _format_for_telegram(article: Article) -> str:
    """В edited_post_text (см. news/editor/pipeline.py) строка "Источник: <url>" — рабочая
    ссылка, нужна модератору в письме для сверки (news/moderation/emails.py). В самой публикации
    в Telegram URL заменяется на голое название источника, без ссылки и без markup — требование
    пользователя (09.09.2026): антиспам-бот канала удаляет посты со ссылками и блокирует
    источник, кликабельность недопустима ни в каком виде (ни markdown-ссылка, ни голый URL,
    который Telegram сам превращает в кликабельный текст).

    Заголовок — реально жирным через Telegram HTML parse_mode, не звёздочками как текстом
    (найдено 10.09.2026 на реальной публикации: без parse_mode `*звёздочки*` от GigaChat
    печатаются буквально, не форматируются). HTML, не legacy Markdown — safer: тело поста не
    парсится на разметку вообще (просто экранируется), падает только заголовок, если в нём
    что-то пойдёт не так, а не всё сообщение целиком на случайном `_`/`*` в тексте статьи."""
    text = article.edited_post_text or article.title
    if not article.edited_post_text:
        return html.escape(text)

    text = SOURCE_LINE_RE.sub(f"Источник: {article.source.name}", text)

    title_line, _, rest = text.partition("\n")
    title_line = title_line.strip().strip("*").strip()
    return f"<b>{html.escape(title_line)}</b>\n{html.escape(rest)}"


@shared_task(autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def publish_from_queue(check_low_stock: bool = False) -> dict:
    channels = list(PublishChannel.objects.filter(channel_type=PublishChannel.ChannelType.TELEGRAM, is_active=True))

    if check_low_stock:
        # Проверка только на утреннем (06:00 UTC) прогоне — иначе за день напомнили бы дважды
        # про один и тот же дефицит (см. docs/DATA_MODEL.md, п.5).
        queue_count = _publication_queue().count()
        if queue_count < DAILY_TARGET:
            from news.moderation.emails import send_low_stock_reminder

            send_low_stock_reminder(queue_count, DAILY_TARGET)

    if not channels:
        # Нечего публиковать без активного канала — оставляем очередь как есть, а не помечаем
        # записи published без единой реальной отправки (нарушило бы аудит PublicationLog).
        logger.warning("Нет активных Telegram-каналов (PublishChannel) — публикация пропущена.")
        return {"published": [], "reason": "no_active_channels"}

    batch = list(_publication_queue()[:BATCH_SIZE])
    published_ids = []

    for article in batch:
        text = _format_for_telegram(article)
        for channel in channels:
            chat_id = channel.config.get("chat_id", settings.TELEGRAM_CHANNEL_ID)
            try:
                message_id = TelegramPublisher().send(chat_id, text, article.image_url or None)
            except Exception as exc:
                logger.exception("Публикация в Telegram не удалась: article=%s channel=%s", article.id, channel.id)
                PublicationLog.objects.create(
                    article=article,
                    channel=channel,
                    status=PublicationLog.LogStatus.FAILED,
                    response_snippet=str(exc),
                )
                raise
            else:
                article.telegram_message_id = message_id
                PublicationLog.objects.create(
                    article=article, channel=channel, status=PublicationLog.LogStatus.SUCCESS
                )

        article.status = Article.Status.PUBLISHED
        article.published_at = timezone.now()
        article.save(update_fields=["status", "published_at", "telegram_message_id"])
        published_ids.append(article.id)

    return {"published": published_ids}
