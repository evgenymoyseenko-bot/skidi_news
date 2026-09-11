# Модуль `knowledge_base` — база знаний по технике катания

Материалы (видео-примеры техники + документы) для участников клуба, без полнотекстового
поиска — участники находят нужное через категории, а не через строку поиска. Это сознательное
упрощение по решению Бро — не проектируем поиск, которого не просили.

## Модели

### KBCategory (Категория/раздел базы знаний)

| Поле | Тип | Комментарий |
|---|---|---|
| name | CharField | Например, «Базовая техника», «Карвинг», «Внетрассовое катание» |
| slug | SlugField, unique | Для API |
| order | PositiveIntegerField, default=0 | Порядок отображения — раз нет поиска, порядок разделов и материалов внутри них — основной способ навигации |

### KBMaterial (Материал базы знаний)

| Поле | Тип | Комментарий |
|---|---|---|
| category | FK → KBCategory | |
| title | CharField | |
| description | TextField, blank=True | |
| material_type | CharField (choices) | `video` \| `document` |
| video_provider | CharField (choices), blank=True | `kinescope` (см. `docs/TECH_STACK.md`, п.7) — заполняется, если `material_type=video` |
| video_id | CharField, blank=True | ID/embed-код видео на площадке провайдера. Сам видеофайл через наш бэкенд никогда не проходит |
| file | FileField, blank=True | Для `material_type=document` (PDF и т.п.) — хранение см. `docs/TECH_STACK.md`, п.8 |
| order | PositiveIntegerField, default=0 | Порядок внутри категории |
| is_published | BooleanField, default=False | Публикуется вручную через admin — черновики не должны попасть в API раньше времени |
| created_at, updated_at | DateTimeField | |

Никакого поля «уровень доступа/тариф» здесь нет осознанно: кто из участников что может
смотреть — решает ЛК на основе своей модели подписки. Наш бэкенд отдаёт весь опубликованный
каталог, ЛК фильтрует по своим правилам (то же решение, что и для новостей — см.
`docs/INTEGRATION_LK.md`, «Что не входит в ответственность парсера»). Если позже понадобится
сегментация на нашей стороне — добавить `visibility`/`tier`, как и для `Article`.

## Django Admin

- `KBMaterialAdmin`: `list_display` — название, категория, тип, статус публикации; `list_filter`
  по категории и типу; drag-and-drop или числовое поле для `order` (на выбор разработчика).
- `KBCategoryAdmin`: обычный CRUD.
- Загружает материалы администратор/контент-менеджер клуба, участники — только читают (только
  этот сценарий заложен, без пользовательской загрузки).

## API для ЛК (расширение `docs/INTEGRATION_LK.md`)

### `GET /api/v1/knowledge-base/categories/`

Список категорий: `slug`, `name`, `order`.

### `GET /api/v1/knowledge-base/materials/`

Список опубликованных материалов (`is_published=True`), с фильтром `?category=<slug>`.

```json
{
  "count": 12,
  "results": [
    {
      "id": 5,
      "category": "karving",
      "title": "Постановка канта на карвинге",
      "description": "...",
      "material_type": "video",
      "video_provider": "kinescope",
      "video_id": "abcd1234",
      "order": 1
    }
  ]
}
```

Для `material_type=document` вместо `video_provider`/`video_id` отдаётся `file_url` (прямая
ссылка на файл в объектном хранилище).

Аутентификация — тот же `Bearer <LK_API_TOKEN>`, что и для новостей (единая точка
интеграции с ЛК, не два разных токена на разные разделы одного бэкенда).

## Открытый вопрос

Как именно встраивать Kinescope-плеер с проверкой подписки конкретного участника (доменные
ограничения на стороне Kinescope vs подписанные временные ссылки, которые генерирует ЛК или наш
бэкенд по запросу) — уточнить в документации Kinescope при реализации, см. `docs/TECH_STACK.md`,
п.7.
