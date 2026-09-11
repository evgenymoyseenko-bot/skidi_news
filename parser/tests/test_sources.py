"""
Тест на реальных данных: используем зафиксированную копию настоящего RSS-фида ТАСС
(вручную отобранные 12 записей из живой ленты tass.ru/rss/v2.xml, получено 04.09.2026,
см. fixtures/tass_sample.xml и докстринг sources.py — там же объяснение, почему источник
сменился с rider-skill.ru на ТАСС и почему фикстура не "как есть" топ ленты). Для правила
"категория обучение исключена" используются маленькие синтетические RSS-фрагменты — фикстура
ТАСС не содержит записей с категорией "обучение" вообще, поэтому одной ей нельзя проверить это
правило; изоляция через синтетику остаётся нужна независимо от конкретного источника.
Картинка с 07.09.2026 необязательна (см. sources.py) — OptionalImageRuleTests ниже проверяет
именно это правило, а не отбраковку без картинки, как раньше.

Сетевой запрос сюда не входит — фикстура и синтетические фрагменты статичны. Живой
HTTP-запрос к источнику этим тестом не проверяется (см. README.md, "Что реально
проверено, а что нет").

Запуск: python3 -m unittest discover -s tests -v   (из папки parser/)
"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import feedparser

import sources as sources_module
from schema import NewsItem
from sources import (
    MAX_ARTICLE_AGE_DAYS,
    SourceConfig,
    _entry_to_news_item,
    _is_excluded_by_category,
    _is_too_old,
    canonicalize_url,
    fetch_source,
)

FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "tass_sample.xml")

# В фикстуре tass_sample.xml из 12 записей у 5 есть <enclosure type="image/...">, у 7 — нет
# (проверено вручную при отборе записей 04.09.2026). Категории "обучение" нет ни у одной.
FIXTURE_ITEMS_WITH_IMAGE = 5
FIXTURE_TOTAL_ITEMS = 12


def _make_single_item_feed(
    title: str,
    category: str | None = None,
    with_image: bool = True,
    pub_date: str | None = None,
) -> str:
    """Собирает минимальный валидный RSS с одной записью — для изоляции правил
    фильтрации друг от друга (в реальной фикстуре обе причины исключения совпадают).
    pub_date — готовая строка в формате RFC 2822 (как в настоящем RSS <pubDate>); по
    умолчанию берём "сейчас", чтобы тесты не протухали сами по себе со временем."""
    category_xml = f"<category>{category}</category>" if category else ""
    image_html = (
        '<img src="https://example.com/photo.jpg" alt=""/>' if with_image else ""
    )
    if pub_date is None:
        pub_date = format_datetime(datetime.now(timezone.utc))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel>
  <title>Test feed</title>
  <link>https://example.com</link>
  <description>Test</description>
  <item>
    <title>{title}</title>
    <link>https://example.com/news/{title.replace(" ", "-")}</link>
    <pubDate>{pub_date}</pubDate>
    {category_xml}
    <description>Краткое описание новости.</description>
    <content:encoded><![CDATA[<p>Текст новости.</p>{image_html}]]></content:encoded>
  </item>
</channel>
</rss>"""


class CanonicalizeUrlTests(unittest.TestCase):
    def test_strips_utm_params(self):
        url = "https://example.com/news/article?utm_source=telegram&utm_medium=cpc&id=5"
        self.assertEqual(canonicalize_url(url), "https://example.com/news/article?id=5")

    def test_lowercases_host_and_drops_trailing_slash(self):
        url = "https://EXAMPLE.com/News/Article/"
        self.assertEqual(canonicalize_url(url), "https://example.com/News/Article")

    def test_same_article_different_tracking_params_match(self):
        a = canonicalize_url("https://example.com/a?utm_source=vk")
        b = canonicalize_url("https://example.com/a?utm_source=telegram&utm_campaign=x")
        self.assertEqual(a, b)


class OptionalImageRuleTests(unittest.TestCase):
    """Изолированная проверка правила "картинка необязательна" (с 07.09.2026), без влияния
    категории. До 07.09.2026 этот класс назывался RequiredImageRuleTests и проверял обратное —
    что запись без картинки отбрасывается; пользователь явно попросил это изменить."""

    def setUp(self):
        self.source = SourceConfig(name="Test", feed_url="test")

    def test_entry_with_image_and_no_training_category_is_kept_with_image(self):
        xml = _make_single_item_feed("Новость с картинкой", category="Соревнования", with_image=True)
        entry = feedparser.parse(xml).entries[0]

        self.assertFalse(_is_excluded_by_category(entry))
        item = _entry_to_news_item(entry, self.source)
        self.assertIsNotNone(item, "запись с картинкой и без 'обучения' не должна отбрасываться")
        self.assertTrue(item.image_url, "картинка нашлась — image_url должен быть заполнен")

    def test_entry_without_image_is_kept_with_empty_image_url(self):
        xml = _make_single_item_feed("Новость без картинки", category="Соревнования", with_image=False)
        entry = feedparser.parse(xml).entries[0]

        self.assertFalse(_is_excluded_by_category(entry), "категория тут ни при чём — проверяем именно картинку")
        item = _entry_to_news_item(entry, self.source)
        self.assertIsNotNone(item, "с 07.09.2026 отсутствие картинки больше не повод отбрасывать запись")
        self.assertIsNone(item.image_url, "картинки не было в источнике — image_url должен остаться None")


class TrainingCategoryRuleTests(unittest.TestCase):
    """Изолированная проверка правила "исключить новости про обучение", без влияния картинки."""

    def test_category_obuchenie_is_excluded(self):
        xml = _make_single_item_feed("Курс для новичков", category="Обучение", with_image=True)
        entry = feedparser.parse(xml).entries[0]
        self.assertTrue(_is_excluded_by_category(entry))

    def test_related_category_sovety_po_obucheniyu_is_excluded(self):
        # Родственный тег, не точное слово "обучение" — проверяем, что подстроковый матч ловит и его.
        xml = _make_single_item_feed("Как правильно поворачивать", category="Советы по обучению", with_image=True)
        entry = feedparser.parse(xml).entries[0]
        self.assertTrue(_is_excluded_by_category(entry))

    def test_unrelated_category_is_not_excluded(self):
        xml = _make_single_item_feed("Результаты этапа Кубка России", category="Соревнования", with_image=True)
        entry = feedparser.parse(xml).entries[0]
        self.assertFalse(_is_excluded_by_category(entry))

    def test_entry_with_image_but_training_category_is_still_dropped_by_fetch_source(self):
        # Даже с картинкой — категория "обучение" всё равно исключает запись на уровне fetch_source.
        xml = _make_single_item_feed("Школа инструкторов набирает группу", category="Обучение", with_image=True)
        # fetch_source ожидает feed_url — feedparser.parse одинаково понимает и путь/URL, и
        # сырую XML-строку, так что можно передать её напрямую.
        source = SourceConfig(name="Test", feed_url=xml)
        items = fetch_source(source)
        self.assertEqual(items, [], "запись с категорией «обучение» не должна попасть в батч, даже с картинкой")


class TooOldRuleTests(unittest.TestCase):
    """Изолированная проверка правила "не старше MAX_ARTICLE_AGE_DAYS дней"."""

    def test_fresh_entry_is_not_too_old(self):
        fresh_date = format_datetime(datetime.now(timezone.utc) - timedelta(days=1))
        xml = _make_single_item_feed("Свежая новость", pub_date=fresh_date)
        entry = feedparser.parse(xml).entries[0]
        self.assertFalse(_is_too_old(entry))

    def test_entry_older_than_max_age_is_too_old(self):
        old_date = format_datetime(
            datetime.now(timezone.utc) - timedelta(days=MAX_ARTICLE_AGE_DAYS + 1)
        )
        xml = _make_single_item_feed("Старая новость", pub_date=old_date)
        entry = feedparser.parse(xml).entries[0]
        self.assertTrue(_is_too_old(entry))

    def test_entry_without_date_is_treated_as_too_old(self):
        # Убираем <pubDate> целиком — намеренный дефолт: без даты не можем подтвердить
        # свежесть, значит отбрасываем (см. докстринг _is_too_old).
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel>
  <title>Test feed</title>
  <link>https://example.com</link>
  <description>Test</description>
  <item>
    <title>Новость без даты</title>
    <link>https://example.com/news/no-date</link>
    <description>Краткое описание.</description>
    <content:encoded><![CDATA[<p>Текст.</p><img src="https://example.com/photo.jpg"/>]]></content:encoded>
  </item>
</channel>
</rss>"""
        entry = feedparser.parse(xml).entries[0]
        self.assertTrue(_is_too_old(entry))

    def test_old_entry_dropped_by_fetch_source_even_with_image_and_good_category(self):
        old_date = format_datetime(
            datetime.now(timezone.utc) - timedelta(days=MAX_ARTICLE_AGE_DAYS + 1)
        )
        xml = _make_single_item_feed("Старая новость про соревнования", category="Соревнования", pub_date=old_date)
        source = SourceConfig(name="Test", feed_url=xml)
        items = fetch_source(source)
        self.assertEqual(items, [], "запись старше MAX_ARTICLE_AGE_DAYS не должна попасть в батч")


class FeedToNewsItemTests(unittest.TestCase):
    """Сквозной прогон через fetch_source() на реальной зафиксированной фикстуре ТАСС
    (tests/fixtures/tass_sample.xml — 12 записей, вручную отобраны из живой ленты
    tass.ru/rss/v2.xml 04.09.2026, подробности и обоснование выбора источника — см.
    докстринг sources.py). На момент фиксации все записи свежие, но это не гарантировано
    навсегда — фикстура сама по себе не "живая" и через MAX_ARTICLE_AGE_DAYS дней после
    04.09.2026 формально "устареет" относительно даты запуска тестов (та же проблема,
    что уже случалась с прежней фикстурой rider-skill.ru — см. историю в git/README).
    Чтобы тест не зависел от того, сколько дней прошло между фиксацией фикстуры и
    запуском тестов, здесь ВРЕМЕННО отключаем правило возраста и проверяем именно то,
    для чего сделана эта фикстура — маппинг полей, правило "картинка обязательна",
    стабильность id. Правило возраста и правило "обучение" отдельно и независимо
    проверяются в TooOldRuleTests / TrainingCategoryRuleTests на синтетических данных
    (эта фикстура не содержит записей с категорией "обучение" вообще)."""

    @classmethod
    def setUpClass(cls):
        cls.source = SourceConfig(name="ТАСС — тест", feed_url=FIXTURE_PATH)
        cls.raw_entries = feedparser.parse(FIXTURE_PATH).entries
        original_max_age = sources_module.MAX_ARTICLE_AGE_DAYS
        sources_module.MAX_ARTICLE_AGE_DAYS = 3650
        try:
            cls.items: list[NewsItem] = fetch_source(cls.source)
        finally:
            sources_module.MAX_ARTICLE_AGE_DAYS = original_max_age

    def test_fixture_has_entries(self):
        self.assertEqual(len(self.raw_entries), FIXTURE_TOTAL_ITEMS, "фикстура не должна быть пустой")

    def test_entries_kept_regardless_of_image(self):
        # С 07.09.2026 картинка необязательна — все 12 записей фикстуры должны остаться в
        # батче независимо от наличия <enclosure type="image/...">. До 07.09.2026 этот тест
        # назывался test_no_image_entries_are_filtered_out и проверял обратное.
        self.assertEqual(len(self.items), FIXTURE_TOTAL_ITEMS)

    def test_image_url_matches_presence_of_enclosure(self):
        # Отдельно проверяем, что image_url корректно проставлен/пуст в соответствии с
        # реальным наличием <enclosure type="image/..."> в исходной записи — те же 5 из 12,
        # что и раньше, просто теперь остальные 7 тоже в батче, но с image_url=None.
        entries_with_image = {
            e.link for e in self.raw_entries
            if any(str(l.get("type", "")).startswith("image/") for l in e.get("links", []))
        }
        self.assertEqual(len(entries_with_image), FIXTURE_ITEMS_WITH_IMAGE)

        items_by_url = {item.source_url: item for item in self.items}
        for url, item in items_by_url.items():
            if url in entries_with_image:
                self.assertTrue(item.image_url, f"у {url} есть enclosure — image_url должен быть заполнен")
            else:
                self.assertIsNone(item.image_url, f"у {url} нет enclosure — image_url должен быть None")

    def test_all_required_fields_present(self):
        # image_url сюда намеренно не входит — с 07.09.2026 он необязателен (см. тест выше).
        for item in self.items:
            self.assertTrue(item.title, f"пустой title у {item.source_url}")
            self.assertTrue(item.text, f"пустой text у {item.source_url}")
            self.assertTrue(item.source_url)
            self.assertTrue(item.source_name)
            self.assertTrue(item.published_at, f"не распознана дата у {item.source_url}")

    def test_text_has_no_html_tags(self):
        for item in self.items:
            self.assertNotIn("<", item.text, f"похоже, в text остался HTML: {item.text[:80]!r}")
            self.assertNotIn("<", item.title)

    def test_internal_ids_are_unique_and_stable(self):
        ids = [item.internal_id for item in self.items]
        self.assertEqual(len(ids), len(set(ids)), "нашлись повторяющиеся internal_id")

        # Стабильность: пересчёт для того же URL должен давать тот же id.
        first = self.items[0]
        matching_raw_entry = next(e for e in self.raw_entries if e.link == first.source_url)
        again = _entry_to_news_item(matching_raw_entry, self.source)
        self.assertEqual(first.internal_id, again.internal_id)

    def test_published_at_is_iso_format(self):
        for item in self.items:
            datetime.fromisoformat(item.published_at)  # не должно бросать исключение


if __name__ == "__main__":
    unittest.main()
