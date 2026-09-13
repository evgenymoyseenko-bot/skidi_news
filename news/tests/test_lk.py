"""Тесты push новости в ЛК — docs/LK_INTEGRATION_TASK.md, Задача 1."""

from unittest.mock import Mock, patch

import requests
from django.test import TestCase

from news.models import Article
from news.publishing.lk import LkPushError, format_body_for_lk, push_to_lk
from sources.models import Source


def _make_article(**kwargs):
    source = Source.objects.create(
        name="Роза Хутор", source_type=Source.SourceType.HTML, url="https://example.com",
        lk_category=Source.LkCategory.RESORT_CIS,
    )
    defaults = dict(
        source=source,
        external_url="https://rosakhutor.ru/news/1",
        title="Заголовок",
        edited_title="Фестиваль в горах",
        edited_post_text=(
            "*Фестиваль в горах*\n"
            "На курорте прошёл фестиваль с участием звёзд.\n"
            "Более 2000 гостей посетили мероприятие.\n"
            "Источник: https://rosakhutor.ru/news/1\n"
            "#РозаХутор #фестиваль"
        ),
        is_urgent=False,
    )
    defaults.update(kwargs)
    return Article.objects.create(**defaults)


class FormatBodyForLkTests(TestCase):
    def test_strips_title_source_line_and_hashtags(self):
        article = _make_article()
        body = format_body_for_lk(article)

        self.assertNotIn("Фестиваль в горах", body)  # заголовок — уже не здесь
        self.assertNotIn("Источник:", body)
        self.assertNotIn("#", body)
        self.assertNotIn("*", body)
        self.assertIn("На курорте прошёл фестиваль", body)
        self.assertIn("Более 2000 гостей", body)

    def test_empty_edited_post_text_gives_empty_body(self):
        article = _make_article(edited_post_text="")
        self.assertEqual(format_body_for_lk(article), "")

    def test_single_line_body_without_title_prefix_is_not_swallowed(self):
        """Реальный баг, найден 13.09.2026 на реальной публикации через форму ручного ввода без
        LLM-форматирования: edited_post_text там — просто текст модератора БЕЗ отдельной строки
        заголовка (в отличие от GigaChat, который всегда добавляет "*Заголовок*" первой строкой).
        Старое безусловное `lines[1:]` съедало единственную строку текста целиком → пустой body
        → publish-news отвечал 400 "title и body обязательны", новость реально не долетела до
        ЛК (Telegram при этом ушёл, PublicationLog это подтвердил)."""
        article = _make_article(
            edited_title="Собрание клуба",
            edited_post_text="В субботу встречаемся в 10:00 у подъёмника.",
        )
        body = format_body_for_lk(article)
        self.assertEqual(body, "В субботу встречаемся в 10:00 у подъёмника.")


class PushToLkTests(TestCase):
    @patch("news.publishing.lk.requests.post")
    def test_success_posts_expected_payload(self, mock_post):
        mock_post.return_value = Mock(ok=True, status_code=200)
        article = _make_article(is_urgent=True)

        with self.settings(LK_PUBLISH_NEWS_URL="https://x.supabase.co/functions/v1/publish-news", LK_NEWS_TOKEN="secret"):
            push_to_lk(article)

        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], "https://x.supabase.co/functions/v1/publish-news")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer secret")
        payload = kwargs["json"]
        self.assertEqual(payload["title"], "Фестиваль в горах")
        self.assertEqual(payload["is_urgent"], True)
        self.assertEqual(payload["category"], "resort_cis")
        self.assertEqual(payload["source_name"], "Роза Хутор")
        self.assertEqual(payload["source_url"], "https://rosakhutor.ru/news/1")
        self.assertNotIn("Источник:", payload["body"])

    @patch("news.publishing.lk.requests.post")
    def test_non_2xx_response_raises(self, mock_post):
        mock_post.return_value = Mock(ok=False, status_code=401, text="Unauthorized")
        article = _make_article()
        with self.assertRaises(LkPushError):
            push_to_lk(article)

    @patch("news.publishing.lk.requests.post")
    def test_network_error_raises(self, mock_post):
        mock_post.side_effect = requests.ConnectionError("boom")
        article = _make_article()
        with self.assertRaises(LkPushError):
            push_to_lk(article)
