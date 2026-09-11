import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("gl_news")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
# autodiscover_tasks() без аргументов ищет только <app_label>/tasks.py на верхнем уровне
# каждого приложения из INSTALLED_APPS — таски news.editor/moderation/publishing лежат в
# подпакетах (news/editor/tasks.py и т.д.), поэтому их нужно указать явно. Найдено 11.09.2026
# при первом реальном запуске воркера на сервере: worker "видел" только sources.tasks.*, задачи
# редактора/модерации/публикации не регистрировались вообще — раньше не всплывало, потому что
# локально таски вызывались напрямую как функции (или через CELERY_TASK_ALWAYS_EAGER), не через
# настоящий celery worker.
app.autodiscover_tasks(packages=["news.editor", "news.moderation", "news.publishing"], related_name="tasks")
