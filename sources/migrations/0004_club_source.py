"""Data-миграция: источник для новостей Клуба, заводимых вручную модератором через форму
срочной публикации (news/moderation/manual_publish.py) — см. запрос пользователя 13.09.2026.
is_active=False — источник никогда не должен участвовать в run_all_active_sources (нет ни
парсера, ни реального URL для регулярного опроса)."""

from django.db import migrations


def create_club_source(apps, schema_editor):
    Source = apps.get_model("sources", "Source")
    Source.objects.get_or_create(
        name="SkiDiscoverer Club",
        defaults={
            "source_type": "manual",
            "url": "https://skidiscoverer.ru/club-news/",
            "lk_category": "club",
            "is_active": False,
        },
    )


def remove_club_source(apps, schema_editor):
    Source = apps.get_model("sources", "Source")
    Source.objects.filter(name="SkiDiscoverer Club", source_type="manual").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("sources", "0003_alter_source_source_type"),
    ]

    operations = [
        migrations.RunPython(create_club_source, remove_club_source),
    ]
