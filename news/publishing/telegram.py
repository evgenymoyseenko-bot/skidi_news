"""
Клиент публикации в Telegram — aiogram.Bot БЕЗ Dispatcher/polling (Этап 5, docs/TECH_STACK.md
п.5): только разовые sendMessage/sendPhoto из Celery-таски. sendPhoto, если у новости есть
image_url, иначе sendMessage — картинка необязательна (см. docs/ARCHITECTURE.md, п.6).

Прокси (TELEGRAM_PROXY_URL) — найдено 11.09.2026 на реальном сервере (news.skidiscoverer.ru):
прямое TCP-соединение к IP Telegram блокируется на уровне выше хостинг-провайдера (не сам
провайдер — подтверждено: общий интернет и GitHub с сервера работают нормально, блокированы
именно IP Telegram, и по IPv4, и IPv6 недоступен). Без прокси sendPhoto/sendMessage зависают на
таймауте и уходят в retry-цикл, ничего не публикуя.

Трафик к Telegram через прокси в норме крошечный — для фото источников-парсера в sendPhoto
передаётся URL картинки, не байты (Telegram сам скачивает её со стороны источника), через прокси
идёт только сам API-запрос (~2-4 КБ на публикацию). Исключение — фото формы ручной публикации
(news/moderation/manual_publish.py): они на нашем собственном домене, и Telegram не может их
скачать сам (см. _local_media_photo ниже, найдено 13.09.2026) — для них через прокси едут байты
самого файла (после сжатия обычно ~100-300 КБ, до ~1 МБ). Заметно на фоне обычных 2-4 КБ, но
форма используется редко (срочные разовые объявления, не постоянный поток) — на общую нагрузку
прокси не влияет.
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

    def _local_media_photo(self, image_url: str):
        """Если image_url — наша собственная ссылка /media/... (фото, загруженное через форму
        срочной публикации, news/moderation/manual_publish.py), а не внешняя ссылка от
        источника-парсера — возвращает aiogram BufferedInputFile с байтами файла, иначе None.

        Найдено 13.09.2026: sendPhoto по URL нашего домена стабильно падает с "failed to get
        HTTP URL content" (проверено на файле 229 КБ — не вопрос размера), при этом фото с
        внешних сайтов-источников через тот же sendPhoto доходят нормально. Похоже на тот же
        сетевой блок, что и с исходящими соединениями (см. docstring класса) — только в обратную
        сторону: сервера Telegram не могут достучаться до нашего VPS, чтобы скачать картинку.
        Раз наше собственное исходящее соединение до Telegram уже работает (через
        TELEGRAM_PROXY_URL), для локальных фото загружаем байты сами и передаём их напрямую,
        а не просим Telegram скачать файл со своей стороны."""
        prefix = settings.SITE_BASE_URL.rstrip("/") + settings.MEDIA_URL
        if not image_url.startswith(prefix):
            return None

        from aiogram.types import BufferedInputFile
        from django.core.files.storage import default_storage

        relative_path = image_url[len(prefix) :]
        with default_storage.open(relative_path, "rb") as f:
            return BufferedInputFile(f.read(), filename=relative_path.rsplit("/", 1)[-1])

    async def _send_async(self, chat_id: str, text: str, image_url: str | None, parse_mode: str | None) -> str:
        from aiogram.exceptions import TelegramBadRequest

        bot = self._get_bot()
        if image_url:
            photo = self._local_media_photo(image_url) or image_url
            try:
                message = await bot.send_photo(chat_id=chat_id, photo=photo, caption=text, parse_mode=parse_mode)
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
