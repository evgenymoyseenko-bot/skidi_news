from django.contrib import admin, messages

from .models import Category, Source


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "source_type",
        "is_active",
        "parse_interval_minutes",
        "last_parsed_at",
        "last_parse_status",
    )
    list_filter = ("source_type", "is_active", "last_parse_status")
    readonly_fields = ("last_parsed_at", "last_parse_status", "last_parse_error")
    actions = ["run_parsing_now"]

    @admin.action(description="Запустить парсинг сейчас")
    def run_parsing_now(self, request, queryset):
        from .tasks import parse_source

        for source in queryset:
            parse_source.delay(source.id)
        self.message_user(
            request,
            f"Парсинг запущен для {queryset.count()} источник(ов).",
            level=messages.SUCCESS,
        )
