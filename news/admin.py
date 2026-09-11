"""
ArticleAdmin — только видимость/отладка, БЕЗ admin-actions для одобрения/отклонения.
Решение о публикации принимается кликом по email-ссылке (news/moderation), не в admin — см.
docs/CODER_INSTRUCTIONS.md, Этап 1, и исправленный раздел docs/DATA_MODEL.md (07.09.2026).
"""

from django.contrib import admin

from .models import Article, PublicationLog, PublishChannel


@admin.register(Article)
class ArticleAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "source",
        "category",
        "status",
        "is_urgent",
        "source_published_at",
        "image_thumbnail",
    )
    list_filter = ("status", "category", "source")
    search_fields = ("title", "summary")
    readonly_fields = (
        "editor_verdict",
        "editor_processed_at",
        "moderation_email_sent_at",
        "moderated_by",
        "moderated_at",
        "published_at",
        "telegram_message_id",
        "raw_payload",
    )

    @admin.display(description="Картинка")
    def image_thumbnail(self, obj: Article):
        if not obj.image_url:
            return "—"
        from django.utils.html import format_html

        return format_html('<img src="{}" style="max-height:40px" />', obj.image_url)


@admin.register(PublishChannel)
class PublishChannelAdmin(admin.ModelAdmin):
    list_display = ("name", "channel_type", "is_active")
    list_filter = ("channel_type", "is_active")


@admin.register(PublicationLog)
class PublicationLogAdmin(admin.ModelAdmin):
    list_display = ("article", "channel", "status", "attempt_number", "attempted_at")
    list_filter = ("status", "channel")
    readonly_fields = [f.name for f in PublicationLog._meta.fields]

    def has_add_permission(self, request):
        return False
