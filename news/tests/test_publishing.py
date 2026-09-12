"""Тесты очереди публикации — сортировка срочных первыми, лимит 2 записи за прогон (Этап 5,
docs/DATA_MODEL.md, пп.4-5). TelegramPublisher замокан — сетевых вызовов нет."""

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from news.models import Article, PublicationLog, PublishChannel
from news.publishing.tasks import _format_for_telegram, publish_from_queue
from sources.models import Source


def _make_approved_article(*, is_urgent=False, hours_ago=0, external_url):
    source = Source.objects.create(name="Test", source_type=Source.SourceType.RSS, url="https://example.com/rss")
    return Article.objects.create(
        source=source,
        external_url=external_url,
        title="Заголовок",
        edited_title="Заголовок",
        edited_post_text="Текст поста.",
        status=Article.Status.APPROVED,
        is_urgent=is_urgent,
        source_published_at=timezone.now() - timedelta(hours=hours_ago),
    )


class FormatForTelegramTests(TestCase):
    """Источник в edited_post_text — URL (нужен модератору в письме), но в публикации в
    Telegram заменяется на голое название источника без ссылки — антиспам-бот канала удаляет
    посты со ссылками (требование пользователя, 09.09.2026)."""

    def test_source_url_replaced_with_source_name(self):
        source = Source.objects.create(name="Роза Хутор", source_type=Source.SourceType.HTML, url="https://example.com")
        article = Article.objects.create(
            source=source,
            external_url="https://rosakhutor.ru/news/1",
            title="Заголовок",
            edited_post_text="*Пост*\nТекст поста.\nИсточник: https://rosakhutor.ru/news/1",
        )
        result = _format_for_telegram(article)
        self.assertIn("Источник: Роза Хутор", result)
        self.assertNotIn("https://rosakhutor.ru", result)

    def test_title_is_html_bold_without_literal_asterisks(self):
        """Найдено 10.09.2026: без parse_mode заголовок в *звёздочках* печатался буквально,
        не жирным. Теперь заголовок оборачивается в <b>, звёздочки убираются из текста."""
        source = Source.objects.create(name="Роза Хутор", source_type=Source.SourceType.HTML, url="https://example.com")
        article = Article.objects.create(
            source=source,
            external_url="https://rosakhutor.ru/news/2",
            title="Заголовок",
            edited_post_text="*Крупный трейловый фестиваль*\nТекст поста.\nИсточник: https://rosakhutor.ru/news/2",
        )
        result = _format_for_telegram(article)
        self.assertEqual(result.splitlines()[0], "<b>Крупный трейловый фестиваль</b>")
        self.assertNotIn("*", result)

    def test_html_special_chars_in_body_are_escaped(self):
        source = Source.objects.create(name="Роза Хутор", source_type=Source.SourceType.HTML, url="https://example.com")
        article = Article.objects.create(
            source=source,
            external_url="https://rosakhutor.ru/news/3",
            title="Заголовок",
            edited_post_text="*Заголовок*\nЦены < 5% & > 10%.\nИсточник: https://rosakhutor.ru/news/3",
        )
        result = _format_for_telegram(article)
        self.assertIn("&lt; 5% &amp; &gt; 10%", result)

    def test_falls_back_to_title_when_no_edited_post_text(self):
        source = Source.objects.create(name="Роза Хутор", source_type=Source.SourceType.HTML, url="https://example.com")
        article = Article.objects.create(source=source, external_url="https://example.com/x", title="Заголовок")
        self.assertEqual(_format_for_telegram(article), "Заголовок")


class PublishFromQueueTests(TestCase):
    def setUp(self):
        self.channel = PublishChannel.objects.create(
            name="Telegram-канал клуба", channel_type=PublishChannel.ChannelType.TELEGRAM, config={"chat_id": "-100"}
        )

    @patch("news.publishing.tasks.TelegramPublisher")
    def test_urgent_article_is_published_before_older_non_urgent(self, mock_publisher_cls):
        mock_publisher_cls.return_value.send.return_value = "123"

        old = _make_approved_article(hours_ago=48, external_url="https://example.com/1")
        urgent = _make_approved_article(is_urgent=True, hours_ago=1, external_url="https://example.com/2")
        _make_approved_article(hours_ago=24, external_url="https://example.com/3")

        result = publish_from_queue()

        self.assertEqual(result["published"], [urgent.id, old.id])

    @patch("news.publishing.tasks.TelegramPublisher")
    def test_only_two_records_per_run(self, mock_publisher_cls):
        mock_publisher_cls.return_value.send.return_value = "123"
        for i in range(5):
            _make_approved_article(hours_ago=i, external_url=f"https://example.com/{i}")

        result = publish_from_queue()
        self.assertEqual(len(result["published"]), 2)
        self.assertEqual(Article.objects.filter(status=Article.Status.APPROVED).count(), 3)

    @patch("news.publishing.tasks.TelegramPublisher")
    def test_sends_source_name_not_url_to_telegram(self, mock_publisher_cls):
        mock_publisher_cls.return_value.send.return_value = "1"
        article = _make_approved_article(external_url="https://rosakhutor.ru/news/1")
        article.source.name = "Роза Хутор"
        article.source.save(update_fields=["name"])
        article.edited_post_text = "*Пост*\nТекст.\nИсточник: https://rosakhutor.ru/news/1"
        article.save(update_fields=["edited_post_text"])

        publish_from_queue()

        sent_text = mock_publisher_cls.return_value.send.call_args[0][1]
        self.assertIn("Источник: Роза Хутор", sent_text)
        self.assertNotIn("rosakhutor.ru", sent_text)

    @patch("news.publishing.tasks.TelegramPublisher")
    def test_published_article_gets_status_and_log(self, mock_publisher_cls):
        mock_publisher_cls.return_value.send.return_value = "999"
        article = _make_approved_article(external_url="https://example.com/1")

        publish_from_queue()

        article.refresh_from_db()
        self.assertEqual(article.status, Article.Status.PUBLISHED)
        self.assertEqual(article.telegram_message_id, "999")
        self.assertIsNotNone(article.published_at)
        self.assertEqual(
            PublicationLog.objects.filter(article=article, status=PublicationLog.LogStatus.SUCCESS).count(), 1
        )

    def test_no_active_channel_skips_publishing(self):
        self.channel.is_active = False
        self.channel.save()
        article = _make_approved_article(external_url="https://example.com/1")

        result = publish_from_queue()

        self.assertEqual(result["published"], [])
        article.refresh_from_db()
        self.assertEqual(article.status, Article.Status.APPROVED)

    @patch("news.moderation.emails.send_low_stock_reminder")
    @patch("news.publishing.tasks.TelegramPublisher")
    def test_low_stock_reminder_sent_only_when_checked(self, mock_publisher_cls, mock_reminder):
        mock_publisher_cls.return_value.send.return_value = "1"
        _make_approved_article(external_url="https://example.com/1")

        publish_from_queue(check_low_stock=True)
        self.assertTrue(mock_reminder.called)

        mock_reminder.reset_mock()
        publish_from_queue(check_low_stock=False)
        self.assertFalse(mock_reminder.called)


class PublishToLkChannelTests(TestCase):
    """ЛК — независимый канал (docs/LK_INTEGRATION_TASK.md, Задача 1): ошибка в одном канале
    не должна мешать другому, ни в одну, ни в другую сторону."""

    def setUp(self):
        self.telegram_channel = PublishChannel.objects.create(
            name="Telegram-канал клуба", channel_type=PublishChannel.ChannelType.TELEGRAM, config={"chat_id": "-100"}
        )
        self.lk_channel = PublishChannel.objects.create(
            name="ЛК", channel_type=PublishChannel.ChannelType.LK_API, is_active=True
        )

    @patch("news.publishing.tasks.push_to_lk")
    @patch("news.publishing.tasks.TelegramPublisher")
    def test_lk_push_called_and_logged_on_success(self, mock_publisher_cls, mock_push_to_lk):
        mock_publisher_cls.return_value.send.return_value = "1"
        article = _make_approved_article(external_url="https://example.com/1")

        publish_from_queue()

        mock_push_to_lk.assert_called_once_with(article)
        self.assertEqual(
            PublicationLog.objects.filter(
                article=article, channel=self.lk_channel, status=PublicationLog.LogStatus.SUCCESS
            ).count(),
            1,
        )

    @patch("news.publishing.tasks.push_to_lk")
    @patch("news.publishing.tasks.TelegramPublisher")
    def test_lk_failure_does_not_block_telegram(self, mock_publisher_cls, mock_push_to_lk):
        from news.publishing.lk import LkPushError

        mock_push_to_lk.side_effect = LkPushError("boom")
        mock_publisher_cls.return_value.send.return_value = "999"
        article = _make_approved_article(external_url="https://example.com/1")

        result = publish_from_queue()

        self.assertEqual(result["published"], [article.id])
        article.refresh_from_db()
        self.assertEqual(article.status, Article.Status.PUBLISHED)
        self.assertEqual(article.telegram_message_id, "999")
        self.assertEqual(
            PublicationLog.objects.filter(
                article=article, channel=self.lk_channel, status=PublicationLog.LogStatus.FAILED
            ).count(),
            1,
        )
        self.assertEqual(
            PublicationLog.objects.filter(
                article=article, channel=self.telegram_channel, status=PublicationLog.LogStatus.SUCCESS
            ).count(),
            1,
        )

    @patch("news.publishing.tasks.push_to_lk")
    @patch("news.publishing.tasks.TelegramPublisher")
    def test_telegram_failure_does_not_prevent_lk_push(self, mock_publisher_cls, mock_push_to_lk):
        mock_publisher_cls.return_value.send.side_effect = Exception("telegram down")
        article = _make_approved_article(external_url="https://example.com/1")

        with self.assertRaises(Exception):
            publish_from_queue()

        mock_push_to_lk.assert_called_once_with(article)

    @patch("news.publishing.tasks.push_to_lk")
    def test_lk_only_active_channel_still_publishes(self, mock_push_to_lk):
        """Если активен только канал ЛК (Telegram выключен) — публикация всё равно идёт, не
        блокируется общим "нет активных каналов" (было раньше, до Задачи 1)."""
        self.telegram_channel.is_active = False
        self.telegram_channel.save()
        article = _make_approved_article(external_url="https://example.com/1")

        result = publish_from_queue()

        self.assertEqual(result["published"], [article.id])
        mock_push_to_lk.assert_called_once_with(article)
