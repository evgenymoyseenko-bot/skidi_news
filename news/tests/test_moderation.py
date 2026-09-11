"""Тесты подписанных ссылок модерации — токены и идемпотентность view (Этап 4,
docs/DATA_MODEL.md «Модерация по email и очередь публикации», пп.2-3)."""

from django.core import signing
from django.test import TestCase, override_settings
from django.urls import reverse

from news.models import Article
from news.moderation.tokens import make_token, read_token
from sources.models import Source


def _make_article(status=Article.Status.PENDING_MODERATION):
    source = Source.objects.create(name="Test", source_type=Source.SourceType.RSS, url="https://example.com/rss")
    return Article.objects.create(
        source=source,
        external_url="https://example.com/news/1",
        title="Заголовок",
        edited_title="Заголовок",
        edited_post_text="Текст поста.",
        status=status,
    )


class TokenTests(TestCase):
    def test_round_trip(self):
        token = make_token(42, "publish")
        payload = read_token(token)
        self.assertEqual(payload, {"article_id": 42, "action": "publish"})

    def test_tampered_token_is_rejected(self):
        token = make_token(42, "publish")
        with self.assertRaises(signing.BadSignature):
            read_token(token[:-1] + ("A" if token[-1] != "A" else "B"))

    @override_settings(MODERATION_TOKEN_MAX_AGE_SECONDS=-1)
    def test_expired_token_is_rejected(self):
        token = make_token(42, "publish")
        with self.assertRaises(signing.SignatureExpired):
            read_token(token)


class ModerationViewTests(TestCase):
    def test_publish_action_approves_article(self):
        article = _make_article()
        token = make_token(article.id, "publish")
        response = self.client.get(reverse("moderation:action", args=[token]))
        self.assertEqual(response.status_code, 200)

        article.refresh_from_db()
        self.assertEqual(article.status, Article.Status.APPROVED)
        self.assertFalse(article.is_urgent)

    def test_publish_urgent_sets_is_urgent(self):
        article = _make_article()
        token = make_token(article.id, "publish_urgent")
        self.client.get(reverse("moderation:action", args=[token]))

        article.refresh_from_db()
        self.assertEqual(article.status, Article.Status.APPROVED)
        self.assertTrue(article.is_urgent)

    def test_reject_action_rejects_article(self):
        article = _make_article()
        token = make_token(article.id, "reject")
        self.client.get(reverse("moderation:action", args=[token]))

        article.refresh_from_db()
        self.assertEqual(article.status, Article.Status.REJECTED)

    def test_second_click_on_already_processed_article_is_a_no_op(self):
        article = _make_article()
        publish_token = make_token(article.id, "publish")
        reject_token = make_token(article.id, "reject")

        self.client.get(reverse("moderation:action", args=[publish_token]))
        article.refresh_from_db()
        self.assertEqual(article.status, Article.Status.APPROVED)

        # Повторный клик по "Отклонить" из того же письма — новость уже approved, но это
        # действие ещё валидно (approved -> rejected разрешён на случай "передумали"/дайджеста).
        # А вот повторный клик по уже использованной ссылке "Опубликовать" не должен ничего
        # ломать, если article уже published — проверяем на published ниже.
        article.status = Article.Status.PUBLISHED
        article.save(update_fields=["status"])

        response = self.client.get(reverse("moderation:action", args=[publish_token]))
        self.assertEqual(response.status_code, 200)
        article.refresh_from_db()
        self.assertEqual(article.status, Article.Status.PUBLISHED, "клик по устаревшей ссылке не должен ничего менять")

    def test_digest_link_can_reapprove_already_approved_article(self):
        """Еженедельный дайджест переиспользует тот же токен-механизм на approved-записях
        (см. docs/DATA_MODEL.md, п.6) — клик должен сработать, а не считаться 'уже обработано'."""
        article = _make_article(status=Article.Status.APPROVED)
        token = make_token(article.id, "publish_urgent")
        response = self.client.get(reverse("moderation:action", args=[token]))
        self.assertEqual(response.status_code, 200)

        article.refresh_from_db()
        self.assertTrue(article.is_urgent)
