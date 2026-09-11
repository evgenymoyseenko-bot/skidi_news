"""
Клиент публикации в Telegram — aiogram.Bot БЕЗ Dispatcher/polling (Этап 5, docs/TECH_STACK.md
п.5): только разовые sendMessage/sendPhoto из Celery-таски. sendPhoto, если у новости есть
image_url, иначе sendMessage — картинка необязательна (см. docs/ARCHITECTURE.md, п.6).

Прокси (TELEGRAM_PROXY_URL) — найдено 11.09.2026 на реальном сервере (news.skidiscoverer.ru):
прямое TCP-соединение к IP Telegram блокируется на уровне выше хостинг-провайдера (не сам
провайдер — подтверждено: общий интернет и GitHub с сервера работают нормально, блокированы
именно IP Telegram, и по IPv4, и IPv6 недоступен). Без прокси sendPhoto/sendMessage зависают на
таймауте и уходят в retry-цикл, ничего не публикуя. Наш трафик к Telegram крошечный — мы
передаём в sendPhoto URL картинки, не байты (Telegram сам её скачивает со стороны источника),
так что через прокси идёт только сам API-запрос (~2-4 КБ на публикацию).
"""

import asyncio
import logging

from django.conf import settings

logger = logging.getLogger(__name__)


class TelegramPublisher:
    def __init__(self, bot_token: str | None = None):
        self._bot_token = bot_token or settings.TELEGRAM_BOT_TOKEN
        self._bot = None

    def _get_bot(self):
        if self._bot is None:
            from aiogram import Bot
            from aiogram.client.session.aiohttp import AiohttpSession

            proxy_url = getattr(settings, "TELEGRAM_PROXY_URL", "") or None
            session = AiohttpSession(proxy=proxy_url) if proxy_url else None
            self._bot = Bot(token=self._bot_token, session=session)
        return self._bot

    async def _send_async(self, chat_id: str, text: str, image_url: str | None, parse_mode: str | None) -> str:
        from aiogram.exceptions import TelegramBadRequest

        bot = self._get_bot()
        if image_url:
            try:
                message = await bot.send_photo(chat_id=chat_id, photo=image_url, caption=text, parse_mode=parse_mode)
                return str(message.message_id)
            except TelegramBadRequest as exc:
                # Найдено 10.09.2026 на реальной публикации: image_url из парсера иногда
                # оказывается мёртвым (410 у самого сайта) или ведёт на HTML антибот-челлендж
                # вместо картинки (сайт отдаёт text/html вместо image/*, обычный запрос —
                # включая запрос самого Telegram — это не проходит). Картинка необязательна
                # (см. docs/ARCHITECTURE.md, п.6) — не блокируем публикацию текста из-за
                # нерабочей картинки, откатываемся на sendMessage вместо падения всей таски.
                logger.warning("sendPhoto не удался (%s), публикую без картинки: chat_id=%s", exc, chat_id)

        message = await bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode)
        return str(message.message_id)

    def send(self, chat_id: str, text: str, image_url: str | None = None, parse_mode: str | None = "HTML") -> str:
        """Возвращает telegram_message_id. Синхронная обёртка — вызывается из синхронной
        Celery-таски (news/publishing/tasks.py). parse_mode по умолчанию HTML — заголовок поста
        приходит уже обёрнутым в <b> (news/publishing/tasks.py, _format_for_telegram), остальной
        текст экранирован и без разметки."""
        return asyncio.run(self._send_async(chat_id, text, image_url, parse_mode))
