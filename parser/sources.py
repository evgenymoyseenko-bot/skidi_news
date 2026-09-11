"""
Минимальный скелет: один источник (RSS), без дедупликации.

Намеренные упрощения на этом шаге (см. открытые вопросы в архитектурном обсуждении):
- Текст берётся из RSS-summary (короткая выжимка, очищенная от HTML), а не из полного тела
  статьи — это дешевле по токенам для редактора. НАХОДКА при тестировании на реальном фиде:
  у этого источника (WordPress) в самом RSS уже есть полный текст статьи —
  entry.content[0]["value"] (тег <content:encoded>), без похода на страницу и без
  trafilatura. Это, вероятно, характерно для WordPress-источников, но не факт, что для
  остальных — если понадобится полный текст везде, для источников без content:encoded
  всё равно потребуется отдельный запрос страницы + trafilatura (уже выбран в проекте).
- Извлечение картинки — best-effort по нескольким типичным местам в RSS-записи. Не
  тестировалось на разных типах фидов, только на источнике ниже — если картинки не
  находятся на другом источнике, это стоит доработать под конкретный формат его фида.
- Никакой проверки "уже видели ли мы эту новость" здесь нет — дедуп сознательно вынесен
  из этого скелета, будет отдельным шагом (см. docs / архитектурное обсуждение).

Уточнение от 07.09.2026: картинка снова НЕОБЯЗАТЕЛЬНАЯ (отменяет решение от 03.09.2026 ниже,
оставленное в докстринге как история). Пользователь явно попросил не отбрасывать новости без
картинки: если она нашлась — публикуем с ней, если нет — без неё. _entry_to_news_item больше не
возвращает None из-за отсутствия картинки, image_url в схеме стал Optional (см. schema.py).

Уточнения от 03.09.2026 (по итогам осмотра результата на реальных данных, история решения выше):
- Новости с категорией "обучение" (и её вариациями типа "советы по обучению") исключаются —
  см. EXCLUDED_CATEGORY_SUBSTRINGS и _is_excluded_by_category. Правило деterministic
  (по тегам источника), не суждение о качестве — поэтому это законно делать в парсере, а не
  в редакторе (в отличие от рекламы/релевантности — то по-прежнему зона редактора, см. ниже).
- Реклама намеренно НЕ фильтруется здесь: на реальных данных нашёлся пример скрытой рекламы
  (см. README.md, "Известные примеры для промта редактора") — ловить такое эвристиками в
  парсере ненадёжно, это явно тот случай субъективного суждения, который по архитектуре
  отдан редактору.

Уточнение от 03.09.2026 (второй раунд): новости старше MAX_ARTICLE_AGE_DAYS дней отбрасываются
(см. _is_too_old). Это тоже детерминированное правило (сравнение дат), не суждение о качестве.
ВАЖНОЕ РЕШЕНИЕ, принятое здесь по умолчанию (не было явного указания пользователя на этот
конкретный случай): если у записи вообще нет распознанной даты публикации — она считается
"слишком старой" и отбрасывается, т.к. свежесть невозможно подтвердить. Если нужно наоборот
пропускать такие записи — это одна строка в _is_too_old (см. комментарий там).

Уточнение от 04.09.2026: тестовый источник заменён с rider-skill.ru на RSS ТАСС
(tass.ru/rss/v2.xml) по прямому запросу пользователя ("выбери что-то из крупных СМИ"). Важная
ОСОБЕННОСТЬ этого источника, найденная при подборе: у него ЕСТЬ <description> с текстом
(в отличие от, например, RIA Новости, чей публичный RSS вообще не содержит текста в записях —
проверено 04.09.2026, альтернативный кандидат отклонён именно поэтому). У ТАСС при этом нет
отдельной рубрики/RSS именно под "горные лыжи" — это общая лента (проверялось: без явной
категории "Горные лыжи" в архиве на момент проверки). Для скелета это не проблема (парсер не
судит о релевантности, это зона редактора), но означает, что зафиксированная тестовая фикстура
(tests/fixtures/tass_sample.xml) — это ВРУЧНУЮ отобранные 12 записей из живой ленты (нейтральные
по теме — спорт/экономика/культура/наука), а не "как есть" топ ленты: полный архивный фид ТАСС
на момент фиксации содержал много новостей о военном конфликте и происшествиях, что не
годится ни тематически, ни по чувствительности для постоянного тестового файла в этом
репозитории. Из 12 отобранных записей 5 содержат <enclosure type="image/..."> (картинку), 7 —
нет; ни у одной нет категории "обучение"; все свежие (в пределах MAX_ARTICLE_AGE_DAYS на момент
фиксации) — то есть в отличие от прежней фикстуры (rider-skill.ru) эта НЕ демонстрирует правило
"обучение" и правило "не старше 30 дней" на реальных данных — эти два правила по-прежнему
проверены отдельно синтетическими фрагментами (см. tests/test_sources.py), сквозной тест на
фикстуре ТАСС проверяет маппинг полей и то, что и записи с картинкой, и без неё одинаково
остаются в батче (картинка перестала быть обязательной 07.09.2026, см. уточнение выше).
"""

import hashlib
import re
import html as html_module
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from time import struct_time
from typing import Optional
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import feedparser

from schema import NewsItem

# Сколько дней "назад" ещё считается свежей новостью — правило от 03.09.2026 ("не берём
# слишком старые новости"). Считается от момента запуска парсера (datetime.now в _is_too_old).
MAX_ARTICLE_AGE_DAYS = 30

# Трекинг-параметры, которые не должны влиять на "уникальность" URL при последующей
# дедупликации (сейчас не используется скелетом напрямую, но canonicalize_url готовим
# заранее, чтобы internal_id уже сегодня был стабильным).
_TRACKING_PARAMS_PREFIXES = ("utm_", "yclid", "fbclid", "gclid")


@dataclass
class SourceConfig:
    name: str
    feed_url: str
    language: str = "ru"


# Подстроки (в нижнем регистре) для исключения по категории источника (entry.tags/<category>).
# Матчим подстрокой, а не точным словом — чтобы ловить и "Обучение", и "Советы по обучению"
# и т.п. родственные теги одним правилом. Список сделан множеством специально, чтобы позже
# добавить сюда что-то ещё одной строкой, а не переписывать логику.
EXCLUDED_CATEGORY_SUBSTRINGS = {"обучен"}  # покрывает "обучение", "обучению", "обучающий" и т.п.


# Один тестовый источник — реальный, проверенный вручную RSS-фид ТАСС (общая лента, а не
# горнолыжная — см. докстринг модуля, "уточнение от 04.09.2026"). Это ЗАГЛУШКА для скелета,
# а не утверждённый список источников — финальный список источников отдельная задача
# (см. docs/SOURCES.md в проекте, если это тот же skidiscoverer).
SOURCES = [
    SourceConfig(
        name="ТАСС",
        feed_url="https://tass.ru/rss/v2.xml",
        language="ru",
    ),
]


def canonicalize_url(url: str) -> str:
    """Приводит URL к каноническому виду для будущей дедупликации по точному совпадению:
    нижний регистр хоста, без трекинг-параметров, без trailing slash."""
    parts = urlsplit(url)
    netloc = parts.netloc.lower()
    path = parts.path.rstrip("/") or "/"
    query_pairs = [
        (k, v) for k, v in parse_qsl(parts.query)
        if not k.lower().startswith(_TRACKING_PARAMS_PREFIXES)
    ]
    query = urlencode(query_pairs)
    return urlunsplit((parts.scheme.lower(), netloc, path, query, ""))


def _strip_html(raw_html: str) -> str:
    """Грубая очистка HTML до текста — достаточно для RSS-summary. Для полноценного
    извлечения текста статьи со страницы в проекте уже выбран trafilatura (см.
    TECH_STACK.md), здесь он не используется намеренно (см. докстринг модуля)."""
    if not raw_html:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw_html)
    text = html_module.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_image(entry) -> Optional[str]:
    """Best-effort поиск картинки в записи RSS. Проверено на реальном фиде (WordPress,
    rider-skill.ru): картинки там есть, но НЕ в summary — они в <content:encoded>
    (entry.content в feedparser), поэтому его тоже нужно проверять, иначе image_url всегда
    будет пустым на WordPress-источниках. На других типах фидов не проверялось."""
    media_content = entry.get("media_content")
    if media_content:
        url = media_content[0].get("url")
        if url:
            return url

    media_thumbnail = entry.get("media_thumbnail")
    if media_thumbnail:
        url = media_thumbnail[0].get("url")
        if url:
            return url

    for link in entry.get("links", []):
        if str(link.get("type", "")).startswith("image/"):
            return link.get("href")

    html_blobs = [entry.get("summary", "")]
    html_blobs.extend(c.get("value", "") for c in entry.get("content", []))
    for blob in html_blobs:
        match = re.search(r'<img[^>]+src="([^"]+)"', blob)
        if match:
            return match.group(1)

    return None


def _is_excluded_by_category(entry) -> bool:
    """True, если у записи есть тег категории (<category> в RSS, entry.tags в feedparser),
    подпадающий под EXCLUDED_CATEGORY_SUBSTRINGS. Проверено на реальном фиде: записи там
    размечены тегами вроде "Обучение", "Советы по обучению" — это и ловим."""
    for tag in entry.get("tags", []):
        term = (tag.get("term") or "").lower()
        if any(substr in term for substr in EXCLUDED_CATEGORY_SUBSTRINGS):
            return True
    return False


def _is_too_old(entry) -> bool:
    """True, если запись старше MAX_ARTICLE_AGE_DAYS дней — либо у неё вообще нет
    распознанной даты публикации (published_parsed/updated_parsed). Дефолт "нет даты —
    считаем устаревшей" осознанный (нельзя подтвердить свежесть без даты), см. докстринг
    модуля. Если нужно наоборот пропускать записи без даты — замени `return True` в ветке
    ниже на `return False`."""
    parsed_time: Optional[struct_time] = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed_time:
        return True  # нет даты — не можем подтвердить свежесть

    published_dt = datetime(*parsed_time[:6], tzinfo=timezone.utc)
    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_ARTICLE_AGE_DAYS)
    return published_dt < cutoff


def _parse_published_at(entry) -> str:
    parsed_time: Optional[struct_time] = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed_time:
        return ""
    dt = datetime(*parsed_time[:6], tzinfo=timezone.utc)
    return dt.isoformat()


def _entry_to_news_item(entry, source: SourceConfig) -> NewsItem:
    """Картинка необязательна (уточнение от 07.09.2026): если _extract_image ничего не
    нашёл — image_url остаётся None, запись всё равно возвращается. Раньше (до 07.09.2026)
    отсутствие картинки было поводом отбросить запись целиком (return None) — это правило
    отменено по прямому запросу пользователя."""
    image_url = _extract_image(entry)  # может быть None — это теперь нормально

    canonical_url = canonicalize_url(entry.link)
    internal_id = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:16]

    return NewsItem(
        internal_id=internal_id,
        title=_strip_html(entry.get("title", "")),
        text=_strip_html(entry.get("summary", "")),
        source_url=entry.link,
        source_name=source.name,
        published_at=_parse_published_at(entry),
        image_url=image_url,
        language=source.language,
    )


def fetch_source(source: SourceConfig) -> list[NewsItem]:
    parsed = feedparser.parse(source.feed_url)
    if parsed.bozo:
        # feedparser не упал, но пометил фид как проблемный (например, невалидный XML) —
        # для скелета просто пробуем работать с тем, что распарсилось, но это стоит
        # логировать нормально в реальной версии, а не молчать.
        print(f"[warn] источник {source.name!r} помечен как bozo: {parsed.bozo_exception}")

    items: list[NewsItem] = []
    skipped_too_old = 0
    skipped_category = 0
    skipped_no_image = 0  # больше не влияет на попадание в батч — считаем только для статистики

    for entry in parsed.entries:
        if _is_too_old(entry):
            skipped_too_old += 1
            continue

        if _is_excluded_by_category(entry):
            skipped_category += 1
            continue

        item = _entry_to_news_item(entry, source)
        if not item.image_url:
            skipped_no_image += 1  # не отбрасываем, просто считаем для лога/статистики

        items.append(item)

    if skipped_too_old or skipped_category or skipped_no_image:
        print(
            f"[info] источник {source.name!r}: пропущено по возрасту (>{MAX_ARTICLE_AGE_DAYS} "
            f"дней или без даты)={skipped_too_old}, по категории (обучение)={skipped_category}, "
            f"без картинки (оставлены в батче, но без image_url)={skipped_no_image}"
        )

    return items
