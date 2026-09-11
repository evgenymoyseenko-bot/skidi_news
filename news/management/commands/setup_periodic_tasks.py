"""
Сидирует расписание django-celery-beat (Этап 5, docs/DEPLOYMENT.md) — дальше редактируется
прямо в Django admin, как и задумано (см. docs/TECH_STACK.md, п.2). Идемпотентно —
get_or_create, повторный запуск не создаёт дублей.

Расписание:
- run_all_active_sources: 06:00 и 18:00 UTC (парсинг).
- run_editor_batch: 06:15 и 18:15 UTC (сдвиг на 15 минут, чтобы парсинг успел завершиться —
  решение кодера).
- publish_from_queue: 06:00 и 18:00 UTC, на утреннем прогоне ещё и проверка "мало материала"
  (check_low_stock=True).
- weekly_requeue_digest: понедельник 06:00 UTC.
"""

import json

from django.core.management.base import BaseCommand
from django_celery_beat.models import CrontabSchedule, PeriodicTask


class Command(BaseCommand):
    help = "Создаёт периодические задачи новостного пайплайна (idempotent)."

    def handle(self, *args, **options):
        morning_evening = self._crontab(hour="6,18", minute="0")
        morning_evening_plus_15 = self._crontab(hour="6,18", minute="15")
        monday_morning = self._crontab(hour="6", minute="0", day_of_week="1")

        self._task(
            name="Парсинг источников (06:00/18:00 UTC)",
            task="sources.tasks.run_all_active_sources",
            crontab=morning_evening,
        )
        self._task(
            name="LLM-редактор (06:15/18:15 UTC)",
            task="news.editor.tasks.run_editor_batch",
            crontab=morning_evening_plus_15,
        )
        self._task(
            name="Публикация из очереди, утро (06:00 UTC, с проверкой остатка)",
            task="news.publishing.tasks.publish_from_queue",
            crontab=self._crontab(hour="6", minute="0"),
            kwargs={"check_low_stock": True},
        )
        self._task(
            name="Публикация из очереди, вечер (18:00 UTC)",
            task="news.publishing.tasks.publish_from_queue",
            crontab=self._crontab(hour="18", minute="0"),
            kwargs={"check_low_stock": False},
        )
        self._task(
            name="Еженедельный дайджест повторной модерации (пн 06:00 UTC)",
            task="news.moderation.tasks.weekly_requeue_digest",
            crontab=monday_morning,
        )

        self.stdout.write(self.style.SUCCESS("Периодические задачи созданы/актуализированы."))

    @staticmethod
    def _crontab(*, hour: str, minute: str, day_of_week: str = "*") -> CrontabSchedule:
        schedule, _ = CrontabSchedule.objects.get_or_create(
            minute=minute,
            hour=hour,
            day_of_week=day_of_week,
            day_of_month="*",
            month_of_year="*",
            timezone="UTC",
        )
        return schedule

    @staticmethod
    def _task(*, name: str, task: str, crontab: CrontabSchedule, kwargs: dict | None = None) -> None:
        PeriodicTask.objects.update_or_create(
            name=name,
            defaults={
                "task": task,
                "crontab": crontab,
                "kwargs": json.dumps(kwargs or {}),
                "enabled": True,
            },
        )
