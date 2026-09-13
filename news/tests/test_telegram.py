"""Тест отката sendPhoto → sendMessage — найдено 10.09.2026 на реальной публикации:
image_url иногда ведёт на мёртвую ссылку или HTML антибот-страницу вместо картинки, картинка
необязательна (docs/ARCHITECTURE.md, п.6), публикация текста не должна падать из-за этого."""

from unittest.mock import AsyncMock, patch

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import SimpleTestCase, override_settings

from news.publishing.telegram import TelegramPublisher


class TelegramPublisherFallbackTests(SimpleTestCase):
    @patch("aiogram.Bot")
    def test_falls_back_to_send_message_when_photo_fails(self, mock_bot_cls):
        from aiogram.exceptions import TelegramBadRequest

        mock_bot = mock_bot_cls.return_value
        mock_bot.send_photo = AsyncMock(
            side_effect=TelegramBadRequest(method=None, message="failed to get HTTP URL content")
        )
        mock_bot.send_message = AsyncMock(return_value=type("M", (), {"message_id": 42})())

        publisher = TelegramPublisher(bot_token="test-token")
        message_id = publisher.send("-100", "Текст поста", "https://example.com/dead.jpg")

        self.assertEqual(message_id, "42")
        mock_bot.send_photo.assert_awaited_once()
        mock_bot.send_message.assert_awaited_once_with(chat_id="-100", text="Текст поста", parse_mode="HTML")

    @patch("aiogram.Bot")
    def test_uses_send_photo_when_it_succeeds(self, mock_bot_cls):
        mock_bot = mock_bot_cls.return_value
        mock_bot.send_photo = AsyncMock(return_value=type("M", (), {"message_id": 7})())

        publisher = TelegramPublisher(bot_token="test-token")
        message_id = publisher.send("-100", "Текст поста", "https://example.com/ok.jpg")

        self.assertEqual(message_id, "7")
        mock_bot.send_photo.assert_awaited_once()
        mock_bot.send_message.assert_not_called()

    @patch("aiogram.Bot")
    def test_no_image_url_uses_send_message_directly(self, mock_bot_cls):
        mock_bot = mock_bot_cls.return_value
        mock_bot.send_message = AsyncMock(return_value=type("M", (), {"message_id": 1})())

        publisher = TelegramPublisher(bot_token="test-token")
        message_id = publisher.send("-100", "Текст поста", None)

        self.assertEqual(message_id, "1")
        mock_bot.send_photo.assert_not_called()


class TelegramLocalMediaPhotoTests(SimpleTestCase):
    """Регрессия 13.09.2026: sendPhoto по ссылке на НАШ СОБСТВЕННЫЙ /media/... (фото из формы
    ручной публикации) стабильно падает у Telegram ("failed to get HTTP URL content") — сервера
    Telegram не могут достучаться до нашего VPS, хотя фото с внешних сайтов-источников через
    тот же sendPhoto доходят нормально. Для локальных ссылок теперь читаем байты файла сами и
    передаём их напрямую (BufferedInputFile), а не просим Telegram скачать файл со своей стороны."""

    def setUp(self):
        self.saved_path = default_storage.save("club-news/test-photo.jpg", ContentFile(b"fake-jpeg-bytes"))
        self.addCleanup(default_storage.delete, self.saved_path)
        self.local_url = settings.SITE_BASE_URL.rstrip("/") + settings.MEDIA_URL + self.saved_path

    @patch("aiogram.Bot")
    def test_local_media_url_sends_bytes_directly(self, mock_bot_cls):
        from aiogram.types import BufferedInputFile

        mock_bot = mock_bot_cls.return_value
        mock_bot.send_photo = AsyncMock(return_value=type("M", (), {"message_id": 9})())

        publisher = TelegramPublisher(bot_token="test-token")
        message_id = publisher.send("-100", "Текст поста", self.local_url)

        self.assertEqual(message_id, "9")
        mock_bot.send_photo.assert_awaited_once()
        sent_photo = mock_bot.send_photo.call_args.kwargs["photo"]
        self.assertIsInstance(sent_photo, BufferedInputFile)
        self.assertEqual(sent_photo.data, b"fake-jpeg-bytes")

    @patch("aiogram.Bot")
    def test_external_source_url_still_passed_as_url(self, mock_bot_cls):
        """Фото источников-статей из автопарсера (не наш домен) — Telegram скачивает их сам,
        поведение не должно меняться."""
        mock_bot = mock_bot_cls.return_value
        mock_bot.send_photo = AsyncMock(return_value=type("M", (), {"message_id": 3})())

        publisher = TelegramPublisher(bot_token="test-token")
        publisher.send("-100", "Текст поста", "https://rosakhutor.ru/photo.jpg")

        sent_photo = mock_bot.send_photo.call_args.kwargs["photo"]
        self.assertEqual(sent_photo, "https://rosakhutor.ru/photo.jpg")


class TelegramProxyTests(SimpleTestCase):
    """Найдено 11.09.2026: прямой доступ к Telegram с реального сервера заблокирован на
    сетевом уровне — TELEGRAM_PROXY_URL должен прокидываться в aiogram.Bot через AiohttpSession."""

    @override_settings(TELEGRAM_PROXY_URL="http://proxy.example.com:23")
    @patch("aiogram.client.session.aiohttp.AiohttpSession")
    @patch("aiogram.Bot")
    def test_proxy_url_passed_to_session_when_set(self, mock_bot_cls, mock_session_cls):
        publisher = TelegramPublisher(bot_token="test-token")
        publisher._get_bot()

        mock_session_cls.assert_called_once_with(proxy="http://proxy.example.com:23")
        mock_bot_cls.assert_called_once_with(token="test-token", session=mock_session_cls.return_value)

    @override_settings(TELEGRAM_PROXY_URL="")
    @patch("aiogram.Bot")
    def test_no_proxy_when_not_configured(self, mock_bot_cls):
        publisher = TelegramPublisher(bot_token="test-token")
        publisher._get_bot()

        mock_bot_cls.assert_called_once_with(token="test-token", session=None)
