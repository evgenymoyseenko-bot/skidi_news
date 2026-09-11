"""Тест отката sendPhoto → sendMessage — найдено 10.09.2026 на реальной публикации:
image_url иногда ведёт на мёртвую ссылку или HTML антибот-страницу вместо картинки, картинка
необязательна (docs/ARCHITECTURE.md, п.6), публикация текста не должна падать из-за этого."""

from unittest.mock import AsyncMock, patch

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
