from django.core.management import call_command
from django.test import TestCase
from django_celery_beat.models import PeriodicTask


class SetupPeriodicTasksTests(TestCase):
    def test_creates_expected_tasks(self):
        call_command("setup_periodic_tasks")
        names = set(PeriodicTask.objects.values_list("task", flat=True))
        self.assertEqual(
            names,
            {
                "sources.tasks.run_all_active_sources",
                "news.editor.tasks.run_editor_batch",
                "news.publishing.tasks.publish_from_queue",
                "news.moderation.tasks.weekly_requeue_digest",
            },
        )

    def test_idempotent_on_second_run(self):
        call_command("setup_periodic_tasks")
        call_command("setup_periodic_tasks")
        self.assertEqual(PeriodicTask.objects.count(), 5)
