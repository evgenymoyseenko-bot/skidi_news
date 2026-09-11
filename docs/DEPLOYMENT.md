# Инфраструктура и деплой

## Статус (11.09.2026): развёрнуто на реальном сервере

`news.skidiscoverer.ru` — VPS `vps.skidi.club` (Debian 13, 4 vCPU/8GB/40GB), Docker + Caddy
(автоматический HTTPS через Let's Encrypt — закрывает пробел «HTTPS отсутствует», описанный
ниже 09.09.2026, актуален был до этого деплоя). Код — приватный репозиторий
`github.com/evgenymoyseenko-bot/skidi_news` (деплой через `git pull`, не rsync — см. п.
«Обновление» ниже).

## Требования к серверу (описано 09.09.2026, сервер ещё не арендован и не поднят)

Нагрузка на старте крошечная (11 источников, 3 запроса к GigaChat/день, 4 публикации в
Telegram/день, несколько писем через UniSender Go) — тяжёлого железа не нужно.

- **Тип**: VPS/VDS с KVM-виртуализацией и root-доступом (не shared-хостинг — нужен Docker).
  Одна машина, без кластера.
- **ОС**: Ubuntu 22.04/24.04 LTS или Debian 12.
- **Ресурсы**: 2 vCPU / 4 GB RAM / 20-40 GB SSD — с запасом под Postgres+Redis+Django+Celery
  одновременно. Меньше (1 vCPU/2GB), вероятно, тоже хватит на старте.
- **Хостинг — платить рублями напрямую**, без прокси-карт (та же логика, что и выбор GigaChat
  вместо OpenAI, см. `TECH_STACK.md` п.5а). Российские провайдеры с VPS+Docker: Timeweb Cloud,
  Selectel, VK Cloud, Yandex Cloud, REG.RU. Ориентир по цене (Timeweb Cloud, проверено
  09.09.2026): 2 vCPU/4GB ≈ 1500₽/мес + IPv4 ≈ 180-200₽/мес ≈ **~1700₽/мес** итого.
- **Домен**: поддомен под `SITE_BASE_URL` (например `news.skidiscoverer.ru`) — DNS A-запись на
  nic.ru, управление доменом уже там.
- **HTTPS — закрыто 11.09.2026.** Добавлен сервис `caddy` (Caddy 2, автоматический HTTPS через
  Let's Encrypt по одному домену в `Caddyfile`) — `web` больше не публикует порт наружу
  напрямую, только через Caddy на 80/443. См. таблицу сервисов ниже.

## Сервисы

| Сервис | Назначение | Образ/основа |
|---|---|---|
| `web` | Django (admin + DRF API), запускается под Gunicorn | Python 3.12-slim + приложение |
| `worker` | Celery worker — парсинг источников, публикация, уведомления | тот же образ, другая команда |
| `beat` | Celery beat — расписание (django-celery-beat) | тот же образ, другая команда |
| `caddy` | Reverse-proxy + автоматический HTTPS (Let's Encrypt) перед `web` | `caddy:2-alpine` |
| `db` | PostgreSQL | официальный образ `postgres` |
| `redis` | Брокер задач для Celery | официальный образ `redis` |

`bot` (Telegram-бот участников, `chatbot`, Этап 11 `docs/CODER_INSTRUCTIONS.md`) — закомментирован
в `docker-compose.yml`, модуль ещё не реализован (`manage.py run_bot` не существует).
Раскомментировать и включить, когда `chatbot` будет готов.

`docker-compose.yml` в корне проекта — **проверен реальным запуском 11.09.2026** на
`news.skidiscoverer.ru`. Порт `web` (8000) больше не публикуется наружу напрямую — только через
`caddy`. Версии образов зафиксированы (`postgres:16`, `redis:7`, `caddy:2-alpine`).

## Обновление (redeploy)

Код деплоится через git, не rsync — репозиторий `github.com/evgenymoyseenko-bot/skidi_news`
(приватный), на сервере клонирован в `~/skidi_news`, доступ — SSH deploy key
(`~/.ssh/skidi_news_deploy_key`, read-only к репозиторию). Обновление:

```bash
cd ~/skidi_news && git pull && docker compose up -d --build
```

После деплоя с новыми моделями — не забыть `docker compose exec web python manage.py migrate`.

## Переменные окружения

Шаблон — `.env.example` в корне (канонический список, этот раздел — только ориентир по
группам). Ключевые группы:

- **Django**: `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `DATABASE_URL`, `SITE_BASE_URL`
  (домен для абсолютных ссылок в письмах модерации, добавлено при реализации Этапа 4).
- **Celery/Redis**: `REDIS_URL`.
- **GigaChat (редактор, Этап 3)**: `GIGACHAT_CREDENTIALS`, `GIGACHAT_SCOPE`, `GIGACHAT_MODEL`,
  `EDITOR_QUOTA_PER_BATCH` (=3, см. `DATA_MODEL.md`), `EDITOR_RECENT_CONTEXT_DAYS` (=5). Ключ
  получен и проверен реальным вызовом 09.09.2026 (developers.sber.ru → тариф для физлица →
  Authorization key, показывается один раз при создании — не хранится в кабинете повторно).
- **Email-модерация (Этап 4)**: `MODERATION_SIGNING_KEY`, `MODERATION_EMAIL_TO`,
  `MODERATION_TOKEN_MAX_AGE_SECONDS`, `ANYMAIL_UNISENDER_GO_API_KEY` **и**
  `ANYMAIL_UNISENDER_GO_API_URL` (оба обязательны, см. `TECH_STACK.md` п.5б — легко забыть
  второй, `django-anymail` без него не отправит письмо). Ключ получен и проверена реальная
  доставка 09.09.2026.
- **Telegram**: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHANNEL_ID`, `MANAGER_TELEGRAM_CHAT_ID`
  (см. `CONSULTATIONS.md`) — один бот-токен на публикацию новостей и на модуль `chatbot`. Ещё
  не получен, реальная публикация не проверялась (см. «Определение готовности MVP» в
  `CODER_INSTRUCTIONS.md`).
- **Интеграция с ЛК**: `LK_API_TOKEN` (общий секрет для аутентификации входящих запросов от
  ЛК к нашему API — покрывает news, knowledge_base, consultations), `LK_WEBHOOK_URL`
  (опционально, для push — см. `INTEGRATION_LK.md`).
- **Форма Тильды**: `FORMS_API_TOKEN` — см. `CONSULTATIONS.md`, «Открытый вопрос» насчёт
  реальных возможностей Тильды передавать заголовки.
- **Файловое хранилище** (продакшен): `S3_STORAGE_*` — см. `TECH_STACK.md`, п.8; пусто —
  локальный диск для разработки.

## Порядок разворачивания (локально/на сервере)

1. Поднять `db` и `redis`.
2. Применить миграции Django (`migrate`), создать суперпользователя для admin.
3. Поднять `web`.
4. Поднять `worker` и `beat`.
5. Выполнить `python manage.py setup_periodic_tasks` — сидирует все периодические задачи
   пайплайна (парсинг 06:00/18:00 UTC, редактор +15 мин, публикация 06:00/18:00 UTC,
   еженедельный дайджест пн 06:00 UTC) идемпотентно, дальше редактируется в Django admin
   (`django-celery-beat`) как обычно (реализовано в рамках Этапов 0-5, заменяет более раннюю
   версию этого шага про ручное создание задачи через admin).
6. Добавить хотя бы один `Source` в admin, запустить его вручную через action «Запустить
   парсинг сейчас», убедиться, что новости появляются со статусом `new` (не
   `pending_moderation` — тот выставляется редактором на следующем шаге, см. `DATA_MODEL.md`).
7. Настроить `PublishChannel` для Telegram (chat_id канала, бот должен быть добавлен в канал
   администратором канала) — **не проверялась реальной публикацией**, только код/тесты, см.
   `CODER_INSTRUCTIONS.md`, «Определение готовности MVP».

## Мониторинг (рекомендация, не обязательно для MVP)

**Flower** — готовая веб-панель мониторинга Celery (кто в очереди, что упало, сколько тасков
выполнено). Разворачивается как ещё один сервис поверх того же Redis-брокера, отдельного кода
не требует. Полезно для диагностики парсинга на старте, можно добавить не сразу, а когда
появятся первые проблемы с задачами.

## Логи и ошибки парсинга

`Source.last_parse_status` / `last_parse_error` (см. `DATA_MODEL.md`) дают быструю диагностику
прямо в admin без похода в логи контейнера — это должно быть в MVP, а не «добавим потом».
