"""Модели Article/PublishChannel/PublicationLog — см. docs/DATA_MODEL.md."""

from django.conf import settings
from django.db import models


class Article(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "Новая"
        DUPLICATE = "duplicate", "Дубликат"
        PENDING_MODERATION = "pending_moderation", "На модерации"
        APPROVED = "approved", "Одобрена"
        REJECTED = "rejected", "Отклонена"
        PUBLISHED = "published", "Опубликована"

    source = models.ForeignKey("sources.Source", on_delete=models.CASCADE, related_name="articles")
    external_url = models.URLField(max_length=1000, unique=True)
    external_id = models.CharField(max_length=300, blank=True)
    title = models.CharField(max_length=500)
    summary = models.TextField(blank=True)
    content = models.TextField(blank=True)
    image_url = models.URLField(max_length=1000, blank=True)
    category = models.ForeignKey(
        "sources.Category", null=True, blank=True, on_delete=models.SET_NULL, related_name="articles"
    )
    source_published_at = models.DateTimeField(null=True, blank=True)
    fetched_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW)
    duplicate_of = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="duplicates"
    )
    similarity_score = models.FloatField(null=True, blank=True)
    moderated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="moderated_articles"
    )
    moderated_at = models.DateTimeField(null=True, blank=True)
    moderation_comment = models.TextField(blank=True)

    # LLM-редактор (Этап 3) — см. docs/DATA_MODEL.md, «Модерация по email и очередь публикации».
    edited_title = models.CharField(max_length=500, blank=True)
    edited_post_text = models.TextField(blank=True)
    editor_verdict = models.CharField(max_length=10, null=True, blank=True, choices=[("publish", "publish"), ("reject", "reject")])
    editor_processed_at = models.DateTimeField(null=True, blank=True)

    # Email-модерация (Этап 4).
    moderation_email_sent_at = models.DateTimeField(null=True, blank=True)
    is_urgent = models.BooleanField(default=False)
    published_at = models.DateTimeField(null=True, blank=True)
    last_requeue_email_sent_at = models.DateTimeField(null=True, blank=True)

    telegram_message_id = models.CharField(max_length=100, null=True, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Новость"
        verbose_name_plural = "Новости"
        indexes = [
            models.Index(fields=["status", "source_published_at"]),
            models.Index(fields=["status", "is_urgent", "source_published_at"]),
        ]

    def __str__(self) -> str:
        return self.title


class PublishChannel(models.Model):
    class ChannelType(models.TextChoices):
        TELEGRAM = "telegram", "Telegram"
        LK_WEBHOOK = "lk_webhook", "ЛК (вебхук)"
        LK_API = "lk_api", "ЛК (API)"

    name = models.CharField(max_length=200)
    channel_type = models.CharField(max_length=20, choices=ChannelType.choices)
    config = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Канал публикации"
        verbose_name_plural = "Каналы публикации"

    def __str__(self) -> str:
        return self.name


class PublicationLog(models.Model):
    class LogStatus(models.TextChoices):
        SUCCESS = "success", "success"
        FAILED = "failed", "failed"

    article = models.ForeignKey(Article, on_delete=models.CASCADE, related_name="publication_logs")
    channel = models.ForeignKey(PublishChannel, on_delete=models.CASCADE, related_name="publication_logs")
    status = models.CharField(max_length=10, choices=LogStatus.choices)
    response_snippet = models.TextField(blank=True)
    attempted_at = models.DateTimeField(auto_now_add=True)
    attempt_number = models.PositiveSmallIntegerField(default=1)

    class Meta:
        verbose_name = "Лог публикации"
        verbose_name_plural = "Логи публикаций"

    def __str__(self) -> str:
        return f"{self.article_id} -> {self.channel_id} ({self.status})"
