"""
Django settings. Состав переменных — см. .env.example и docs/DEPLOYMENT.md.

Для локального запуска/тестов без Postgres и Redis: если DATABASE_URL/REDIS_URL не заданы,
используются безопасные локальные дефолты (SQLite-файл, локальный Redis) — не для продакшена,
там оба обязаны быть заданы явно через .env.
"""

import os
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


SECRET_KEY = os.environ.get("SECRET_KEY", "change-me")
DEBUG = _env_bool("DEBUG", False)
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]

# Базовый URL для абсолютных ссылок в письмах модерации (news/moderation/emails.py) — свой
# домен/поддомен бэкенда, не skidiscoverer.ru (лендинг на Тильде).
SITE_BASE_URL = os.environ.get("SITE_BASE_URL", "http://localhost:8000")

# Caddy — реверс-прокси, сам добавляет X-Forwarded-Proto (стандартное поведение Caddy
# reverse_proxy). Без этой настройки Django считает каждый запрос HTTP (не видит, что снаружи
# HTTPS) и CSRF-проверка origin для POST-форм отбивает реальные HTTPS-запросы браузера как
# "чужой origin" — найдено 13.09.2026 на форме срочной ручной публикации (первая публичная
# POST-форма в проекте; moderation_action — GET, поэтому баг раньше не проявлялся).
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
CSRF_TRUSTED_ORIGINS = [
    o.strip() for o in os.environ.get("CSRF_TRUSTED_ORIGINS", SITE_BASE_URL).split(",") if o.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_celery_beat",
    "anymail",
    "sources",
    "news",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": dj_database_url.config(
        env="DATABASE_URL",
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=600,
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "ru"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
# Картинка из формы ручной публикации (news/moderation/manual_publish.py, 13.09.2026) —
# единственное место в проекте, где мы сами храним загруженный файл (везде ещё image_url —
# внешняя ссылка на источник). Обслуживается Caddy напрямую с общего volume, см. Caddyfile.
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Celery / Redis ---
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", REDIS_URL)
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", REDIS_URL)
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_ALWAYS_EAGER = _env_bool("CELERY_TASK_ALWAYS_EAGER", False)

# --- Telegram: публикация новостей (Этап 5, docs/CODER_INSTRUCTIONS.md) ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID", "")
# Прокси для запросов к Telegram Bot API — найдено 11.09.2026: с реального сервера прямой
# доступ к IP Telegram блокирован на сетевом уровне выше хостинг-провайдера (см.
# news/publishing/telegram.py). Пусто — без прокси (напр. для локальной разработки).
TELEGRAM_PROXY_URL = os.environ.get("TELEGRAM_PROXY_URL", "")

# --- ЛК: push новостей в publish-news (Supabase Edge Function), см. docs/LK_INTEGRATION_TASK.md ---
LK_PUBLISH_NEWS_URL = os.environ.get("LK_PUBLISH_NEWS_URL", "")
LK_NEWS_TOKEN = os.environ.get("LK_NEWS_TOKEN", "")

# --- GigaChat: LLM-редактор (Этап 3, docs/TECH_STACK.md п.5а) ---
GIGACHAT_CREDENTIALS = os.environ.get("GIGACHAT_CREDENTIALS", "")
GIGACHAT_SCOPE = os.environ.get("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
GIGACHAT_MODEL = os.environ.get("GIGACHAT_MODEL", "GigaChat-2-Pro")

# Квота одобренных редактором новостей за один прогон (06:00/18:00 UTC) — 6/день = 3/батч,
# решение кодера от 07.09.2026, см. docs/DATA_MODEL.md, раздел «От редактора — к очереди на
# модерацию».
EDITOR_QUOTA_PER_BATCH = int(os.environ.get("EDITOR_QUOTA_PER_BATCH", "3"))
# Глубина контекста «недавно опубликованное», который передаётся редактору для смыслового
# дедупа (решение кодера от 07.09.2026, см. parser/EDITOR_PROMPT.md).
EDITOR_RECENT_CONTEXT_DAYS = int(os.environ.get("EDITOR_RECENT_CONTEXT_DAYS", "5"))

# --- Email-модерация (Этап 4, docs/DATA_MODEL.md «Модерация по email и очередь публикации») ---
MODERATION_SIGNING_KEY = os.environ.get("MODERATION_SIGNING_KEY", SECRET_KEY)
MODERATION_EMAIL_TO = os.environ.get("MODERATION_EMAIL_TO", "info@skidiscoverer.ru")
MODERATION_TOKEN_MAX_AGE_SECONDS = int(os.environ.get("MODERATION_TOKEN_MAX_AGE_SECONDS", str(30 * 24 * 3600)))

DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "info@skidiscoverer.ru")

# Провайдер — UniSender Go через django-anymail (решено 07.09.2026, см. docs/TECH_STACK.md п.5б).
# Нужны ОБА параметра — ключ и URL датацентра (go1/go2, зависит от аккаунта, см. .env.example) —
# без UNISENDER_GO_API_URL anymail не отправит письмо (найдено 09.09.2026 в доке anymail.dev,
# не было очевидно из первоначального решения про SMTP/API). Для локальной разработки/тестов без
# ключа — падаем на консольный backend, чтобы не требовать реальный API-ключ для manage.py
# check/test.
if os.environ.get("ANYMAIL_UNISENDER_GO_API_KEY"):
    EMAIL_BACKEND = "anymail.backends.unisender_go.EmailBackend"
    ANYMAIL = {
        "UNISENDER_GO_API_KEY": os.environ["ANYMAIL_UNISENDER_GO_API_KEY"],
        "UNISENDER_GO_API_URL": os.environ.get(
            "ANYMAIL_UNISENDER_GO_API_URL", "https://go1.unisender.ru/ru/transactional/api/v1/"
        ),
    }
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
