"""Тест отката sendPhoto → sendMessage — найдено 10.09.2026 на реальной публикации:
image_url иногда ведёт на мёртвую ссылку или HTML антибот-страницу вместо картинки, картинка
необязательна (docs/ARCHITECTURE.md, п.6), публикация текста не должна падать из-за этого."""

from unittest.mock import AsyncMock, patch

from django.test import SimpleTestCase

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
