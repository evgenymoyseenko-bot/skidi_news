"""Тесты формы срочной ручной публикации новости Клуба (13.09.2026) —
news/moderation/manual_publish.py, news/editor/tasks.py::run_editor_for_manual_article."""

from unittest.mock import patch

from django.core import signing
from django.test import TestCase, override_settings
from django.urls import reverse

from news.editor.pipeline import EditorPost
from news.editor.tasks import run_editor_for_manual_article
from news.models import Article, PublicationLog, PublishChannel
from news.moderation.manual_publish import CLUB_SOURCE_NAME
from news.moderation.tokens import make_manual_publish_token, read_manual_publish_token
from sources.models import Source


class ManualPublishTokenTests(TestCase):
    def test_round_trip(self):
        token = make_manual_publish_token()
        payload = read_manual_publish_token(token)
        self.assertEqual(payload, {"purpose": "manual_publish"})

    def test_not_interchangeable_with_moderation_action_token(self):
        """Разная соль — токен формы не должен валидироваться read_token и наоборот."""
        from news.moderation.tokens import read_token

        token = make_manual_publish_token()
        with self.assertRaises(signing.BadSignature):
            read_token(token)

    @override_settings(MODERATION_TOKEN_MAX_AGE_SECONDS=-1)
    def test_expired_token_rejected(self):
        token = make_manual_publish_token()
        with self.assertRaises(signing.SignatureExpired):
            read_manual_publish_token(token)


class ManualPublishFormViewTests(TestCase):
    def setUp(self):
        self.token = make_manual_publish_token()
        self.url = reverse("moderation:manual_publish", args=[self.token])
        PublishChannel.objects.create(
            name="Test Telegram", channel_type=PublishChannel.ChannelType.TELEGRAM, config={"chat_id": "-100"}
        )

    def test_get_renders_form(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Срочная новость Клуба")

    def test_bad_token_rejected(self):
        response = self.client.get(reverse("moderation:manual_publish", args=["garbage"]))
        self.assertEqual(response.status_code, 400)

    def test_missing_fields_rerenders_form_with_error(self):
        response = self.client.post(self.url, {"title": "", "content": ""})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "обязательны")
        self.assertEqual(Article.objects.count(), 0)

    @patch("news.publishing.tasks.TelegramPublisher")
    def test_skip_llm_publishes_immediately_as_typed(self, mock_publisher_cls):
        mock_publisher_cls.return_value.send.return_value = "42"

        response = self.client.post(
            self.url,
            {"title": "Собрание клуба", "content": "В субботу встречаемся в 10:00.", "skip_llm_formatting": "1"},
        )
        self.assertEqual(response.status_code, 200)

        article = Article.objects.get(title="Собрание клуба")
        self.assertEqual(article.edited_title, "Собрание клуба")
        self.assertEqual(article.edited_post_text, "В субботу встречаемся в 10:00.")
        self.assertEqual(article.editor_verdict, "publish")
        self.assertTrue(article.is_urgent)
        self.assertEqual(article.status, Article.Status.PUBLISHED)
        self.assertIsNotNone(article.published_at)
        self.assertEqual(article.source.name, CLUB_SOURCE_NAME)
        self.assertEqual(article.source.lk_category, Source.LkCategory.CLUB)
        mock_publisher_cls.return_value.send.assert_called_once()

    def test_skip_llm_unchecked_queues_editor_task_not_published_yet(self):
        with patch("news.editor.tasks.run_editor_for_manual_article.delay") as mock_delay:
            response = self.client.post(self.url, {"title": "Срочно", "content": "Текст новости."})
        self.assertEqual(response.status_code, 200)

        article = Article.objects.get(title="Срочно")
        self.assertEqual(article.status, Article.Status.NEW)
        self.assertTrue(article.is_urgent)
        self.assertEqual(article.edited_post_text, "")
        mock_delay.assert_called_once_with(article.id)

    def test_two_submissions_do_not_duplicate_club_source(self):
        """Source "SkiDiscoverer Club" уже заведён data-миграцией (0004_club_source.py) — здесь
        проверяем, что повторные сабмиты формы не плодят вторую запись с тем же именем."""
        with patch("news.editor.tasks.run_editor_for_manual_article.delay"):
            self.client.post(self.url, {"title": "Т1", "content": "С1"})
            self.client.post(self.url, {"title": "Т2", "content": "С2"})
        self.assertEqual(Source.objects.filter(name=CLUB_SOURCE_NAME).count(), 1)


class RunEditorForManualArticleTests(TestCase):
    def setUp(self):
        self.source = Source.objects.create(
            name=CLUB_SOURCE_NAME, source_type=Source.SourceType.MANUAL, url="https://skidiscoverer.ru/club-news/"
        )
        self.article = Article.objects.create(
            source=self.source,
            external_url="https://skidiscoverer.ru/club-news/abc",
            title="Заголовок клуба",
            content="Текст новости клуба.",
            is_urgent=True,
        )
        PublishChannel.objects.create(
            name="Test Telegram", channel_type=PublishChannel.ChannelType.TELEGRAM, config={"chat_id": "-100"}
        )

    @patch("news.publishing.tasks.TelegramPublisher")
    @patch("news.editor.tasks.GigaChatEditorClient")
    def test_llm_formats_and_publishes(self, mock_client_cls, mock_publisher_cls):
        mock_publisher_cls.return_value.send.return_value = "1"
        mock_client_cls.return_value.complete.return_value = (
            f"*Отформатированный заголовок*\nОтформатированный текст.\n"
            f"Источник: {self.article.external_url}\n"
        )

        result = run_editor_for_manual_article(self.article.id)

        self.article.refresh_from_db()
        self.assertTrue(result["used_llm"])
        self.assertEqual(self.article.edited_title, "Отформатированный заголовок")
        self.assertEqual(self.article.editor_verdict, "publish")
        self.assertEqual(self.article.status, Article.Status.PUBLISHED)

    @patch("news.publishing.tasks.TelegramPublisher")
    @patch("news.editor.tasks.GigaChatEditorClient")
    def test_llm_no_match_falls_back_to_moderator_text(self, mock_client_cls, mock_publisher_cls):
        """LLM не выдал пост, подходящий по формату/критериям — новость всё равно публикуется
        как ввёл модератор (см. docstring run_editor_for_manual_article)."""
        mock_publisher_cls.return_value.send.return_value = "1"
        mock_client_cls.return_value.complete.return_value = "Тема не подходит под критерии, ничего не публикую."

        result = run_editor_for_manual_article(self.article.id)

        self.article.refresh_from_db()
        self.assertFalse(result["used_llm"])
        self.assertEqual(self.article.edited_title, "Заголовок клуба")
        self.assertEqual(self.article.edited_post_text, "Текст новости клуба.")
        self.assertEqual(self.article.editor_verdict, "reject")
        self.assertEqual(self.article.status, Article.Status.PUBLISHED)
