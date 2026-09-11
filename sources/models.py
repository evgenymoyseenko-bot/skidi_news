"""Модели Category/Source — см. docs/DATA_MODEL.md."""

from django.db import models


class Category(models.Model):
    name = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)

    class Meta:
        verbose_name = "Категория"
        verbose_name_plural = "Категории"

    def __str__(self) -> str:
        return self.name


class Source(models.Model):
    class SourceType(models.TextChoices):
        RSS = "rss", "RSS"
        HTML = "html", "HTML"

    name = models.CharField(max_length=200)
    source_type = models.CharField(max_length=10, choices=SourceType.choices)
    url = models.URLField(max_length=500)
    default_category = models.ForeignKey(
        Category, null=True, blank=True, on_delete=models.SET_NULL, related_name="sources"
    )
    # Для html-источников: item_selector/title_selector/link_selector/date_selector/image_selector.
    parser_config = models.JSONField(default=dict, blank=True)
    parse_interval_minutes = models.PositiveIntegerField(default=60)
    is_active = models.BooleanField(default=True)
    last_parsed_at = models.DateTimeField(null=True, blank=True)
    last_parse_status = models.CharField(max_length=10, null=True, blank=True)
    last_parse_error = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Источник"
        verbose_name_plural = "Источники"

    def __str__(self) -> str:
        return self.name

    def is_due(self, now) -> bool:
        """Пора ли опрашивать источник — см. sources/tasks.py, run_all_active_sources."""
        if not self.is_active:
            return False
        if self.last_parsed_at is None:
            return True
        elapsed_minutes = (now - self.last_parsed_at).total_seconds() / 60
        return elapsed_minutes >= self.parse_interval_minutes
