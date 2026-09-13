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

from .lk import LkPushError, push_to_lk
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
    что-то пойдёт не так, а не всё сообщение целиком на случайном `_`/`*` в тексте статьи.

    Заголовок берётся из `edited_title`, а НЕ парсингом первой строки `edited_post_text`
    (баг найден 13.09.2026 на форме ручной публикации без LLM-форматирования — там
    `edited_post_text` не содержит отдельной строки-заголовка вообще, это просто текст
    модератора, и старый код `text.partition("\n")` превращал ВЕСЬ текст в жирный "заголовок"
    без тела). Первая строка `edited_post_text` отбрасывается, только если совпадает с
    `edited_title` (это тот случай, когда GigaChat/```_extract_title``` уже продублировал её
    туда как первую строку поста) — иначе она часть тела, не заголовок."""
    if not article.edited_post_text:
        return html.escape(article.title)

    text = SOURCE_LINE_RE.sub(f"Источник: {article.source.name}", article.edited_post_text)
    lines = text.splitlines()
    if lines and lines[0].strip().strip("*").strip() == (article.edited_title or "").strip():
        lines = lines[1:]
    rest = "\n".join(lines).strip()

    title = article.edited_title or article.title
    return f"<b>{html.escape(title)}</b>\n{html.escape(rest)}"


def _active_channels() -> tuple[list[PublishChannel], list[PublishChannel]]:
    telegram_channels = list(
        PublishChannel.objects.filter(channel_type=PublishChannel.ChannelType.TELEGRAM, is_active=True)
    )
    lk_channels = list(PublishChannel.objects.filter(channel_type=PublishChannel.ChannelType.LK_API, is_active=True))
    return telegram_channels, lk_channels


def publish_article_now(
    article: Article, telegram_channels: list[PublishChannel], lk_channels: list[PublishChannel]
) -> None:
    """Публикует ОДНУ статью во все переданные активные каналы немедленно — переиспользуется и
    батчем из `publish_from_queue` (см. ниже), и немедленной публикацией ручных новостей Клуба
    (news/moderation/manual_publish.py, 13.09.2026, минуя расписание/очередь целиком). Меняет
    статус на PUBLISHED и сохраняет `article` сама — вызывающий код это не делает повторно."""
    # ЛК — первым: независимый канал (docs/LK_INTEGRATION_TASK.md, Задача 1, п.5), ошибка здесь
    # не должна помешать Telegram и наоборот. Telegram-ветка ниже при неудаче делает `raise`
    # (уходит в retry — для batch-таски; при немедленной публикации из формы вызывающий код сам
    # решает, что делать с исключением) — если бы ЛК шёл после неё, при таком raise пуш в ЛК для
    # этой статьи вообще не случился бы в этом прогоне. Пробуем ЛК до Telegram, чтобы это не
    # зависело от порядка/исхода другого канала.
    for channel in lk_channels:
        try:
            push_to_lk(article)
        except LkPushError as exc:
            logger.exception("Публикация в ЛК не удалась: article=%s channel=%s", article.id, channel.id)
            PublicationLog.objects.create(
                article=article,
                channel=channel,
                status=PublicationLog.LogStatus.FAILED,
                response_snippet=str(exc),
            )
        else:
            PublicationLog.objects.create(article=article, channel=channel, status=PublicationLog.LogStatus.SUCCESS)

    text = _format_for_telegram(article)
    for channel in telegram_channels:
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
            PublicationLog.objects.create(article=article, channel=channel, status=PublicationLog.LogStatus.SUCCESS)

    article.status = Article.Status.PUBLISHED
    article.published_at = timezone.now()
    article.save(update_fields=["status", "published_at", "telegram_message_id"])


@shared_task(autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def publish_from_queue(check_low_stock: bool = False) -> dict:
    telegram_channels, lk_channels = _active_channels()

    if check_low_stock:
        # Проверка только на утреннем (06:00 UTC) прогоне — иначе за день напомнили бы дважды
        # про один и тот же дефицит (см. docs/DATA_MODEL.md, п.5).
        queue_count = _publication_queue().count()
        if queue_count < DAILY_TARGET:
            from news.moderation.emails import send_low_stock_reminder

            send_low_stock_reminder(queue_count, DAILY_TARGET)

    if not telegram_channels and not lk_channels:
        # Нечего публиковать без активного канала — оставляем очередь как есть, а не помечаем
        # записи published без единой реальной отправки (нарушило бы аудит PublicationLog).
        logger.warning("Нет активных каналов публикации (PublishChannel) — публикация пропущена.")
        return {"published": [], "reason": "no_active_channels"}

    batch = list(_publication_queue()[:BATCH_SIZE])
    published_ids = []

    for article in batch:
        publish_article_now(article, telegram_channels, lk_channels)
        published_ids.append(article.id)

    return {"published": published_ids}
