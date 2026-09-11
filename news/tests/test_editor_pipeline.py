"""
Тесты разбора ответа LLM-редактора (разделитель "===" + сопоставление по "Источник: <url>") и
применения квоты (3/батч, решение кодера 07.09.2026) — см. news/editor/pipeline.py и
docs/DATA_MODEL.md, «От редактора — к очереди на модерацию».
"""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from news.editor.pipeline import apply_editor_verdicts, parse_editor_response
from news.models import Article
from sources.models import Source


def _make_article(**kwargs):
    source = Source.objects.create(name="Test", source_type=Source.SourceType.RSS, url="https://example.com/rss")
    defaults = {
        "source": source,
        "external_url": kwargs.pop("external_url", "https://example.com/a"),
        "title": kwargs.pop("title", "Заголовок"),
        "status": Article.Status.NEW,
    }
    defaults.update(kwargs)
    return Article.objects.create(**defaults)


class ParseEditorResponseTests(TestCase):
    def test_splits_on_separator_and_matches_by_source_url(self):
        a1 = _make_article(external_url="https://example.com/news/1", title="Новость 1")
        a2 = _make_article(external_url="https://example.com/news/2", title="Новость 2")

        response = (
            "*Новость 1*\nТекст первой новости.\nИсточник: https://example.com/news/1\n"
            "===\n"
            "*Новость 2*\nТекст второй новости.\nИсточник: https://example.com/news/2\n"
        )
        posts = parse_editor_response(response, [a1, a2])

        self.assertEqual(len(posts), 2)
        self.assertEqual({p.article.id for p in posts}, {a1.id, a2.id})

    def test_post_with_unknown_source_url_is_dropped(self):
        a1 = _make_article(external_url="https://example.com/news/1")
        response = "*Пост*\nТекст.\nИсточник: https://example.com/unknown\n"
        posts = parse_editor_response(response, [a1])
        self.assertEqual(posts, [])

    def test_post_without_source_line_is_dropped(self):
        a1 = _make_article(external_url="https://example.com/news/1")
        response = "*Пост без источника*\nТекст без ссылки.\n"
        posts = parse_editor_response(response, [a1])
        self.assertEqual(posts, [])

    def test_empty_response_yields_no_posts(self):
        a1 = _make_article(external_url="https://example.com/news/1")
        self.assertEqual(parse_editor_response("", [a1]), [])


class ApplyEditorVerdictsTests(TestCase):
    def test_over_quota_posts_stay_unprocessed_for_next_run(self):
        now = timezone.now()
        articles = [
            _make_article(
                external_url=f"https://example.com/news/{i}",
                title=f"Новость {i}",
                source_published_at=now - timedelta(hours=i),
            )
            for i in range(4)
        ]
        posts = [
            _make_post(a) for a in articles
        ]

        apply_editor_verdicts(articles, posts, quota=3)

        processed = Article.objects.filter(editor_processed_at__isnull=False)
        self.assertEqual(processed.count(), 3)
        for article in processed:
            self.assertEqual(article.status, Article.Status.PENDING_MODERATION)
            self.assertEqual(article.editor_verdict, "publish")

        untouched = Article.objects.get(editor_processed_at__isnull=True)
        self.assertEqual(untouched.status, Article.Status.NEW)
        self.assertIsNone(untouched.editor_verdict)

        # Старые (меньшие source_published_at) должны быть взяты в первую очередь.
        oldest_url = articles[3].external_url  # now - 3h -> самая старая
        self.assertTrue(
            Article.objects.get(external_url=oldest_url).editor_processed_at is not None
        )

    def test_rejected_articles_get_verdict_without_status_change(self):
        approved = _make_article(external_url="https://example.com/news/approved")
        rejected = _make_article(external_url="https://example.com/news/rejected")

        posts = [_make_post(approved)]
        apply_editor_verdicts([approved, rejected], posts, quota=3)

        rejected.refresh_from_db()
        self.assertEqual(rejected.editor_verdict, "reject")
        self.assertIsNotNone(rejected.editor_processed_at)
        self.assertEqual(rejected.status, Article.Status.NEW)


def _make_post(article: Article):
    from news.editor.pipeline import EditorPost

    return EditorPost(post_text=f"*{article.title}*\nТекст.\nИсточник: {article.external_url}\n", article=article)
