"""
Портировано из parser/tests/test_sources.py (эталонный скелет, docs/CODER_INSTRUCTIONS.md,
Этап 2) под sources/parsing/rss.py + common.py. Сохраняет оба уровня покрытия: fixture-тест на
реальных данных (fixtures/tass_sample.xml, та же фикстура, что и в parser/) и синтетические
тесты правил фильтрации в изоляции друг от друга.
"""

import os
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import feedparser
from django.test import SimpleTestCase

from sources.parsing.common import (
    MAX_ARTICLE_AGE_DAYS,
    canonicalize_url,
    is_excluded_category,
    is_too_old,
)
from sources.parsing.rss import _parse_published_at, entry_to_parsed_item, fetch_rss

FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "tass_sample.xml")

# В фикстуре 12 записей, у 5 есть <enclosure type="image/...">, у 7 — нет (см. parser/README.md).
FIXTURE_ITEMS_WITH_IMAGE = 5
FIXTURE_TOTAL_ITEMS = 12


def _make_single_item_feed(
    title: str,
    category: str | None = None,
    with_image: bool = True,
    pub_date: str | None = None,
) -> str:
    category_xml = f"<category>{category}</category>" if category else ""
    image_html = '<img src="https://example.com/photo.jpg" alt=""/>' if with_image else ""
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


class CanonicalizeUrlTests(SimpleTestCase):
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


class OptionalImageRuleTests(SimpleTestCase):
    """Картинка необязательна (с 07.09.2026) — запись остаётся в батче в любом случае."""

    def test_entry_with_image_and_no_training_category_is_kept_with_image(self):
        xml = _make_single_item_feed("Новость с картинкой", category="Соревнования", with_image=True)
        items = fetch_rss(xml)
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0].image_url)

    def test_entry_without_image_is_kept_with_empty_image_url(self):
        xml = _make_single_item_feed("Новость без картинки", category="Соревнования", with_image=False)
        items = fetch_rss(xml)
        self.assertEqual(len(items), 1)
        self.assertIsNone(items[0].image_url)


class TrainingCategoryRuleTests(SimpleTestCase):
    def test_category_obuchenie_is_excluded(self):
        entry = feedparser.parse(
            _make_single_item_feed("Курс для новичков", category="Обучение", with_image=True)
        ).entries[0]
        terms = [tag.get("term") for tag in entry.get("tags", [])]
        self.assertTrue(is_excluded_category(terms))

    def test_related_category_sovety_po_obucheniyu_is_excluded(self):
        entry = feedparser.parse(
            _make_single_item_feed("Как правильно поворачивать", category="Советы по обучению", with_image=True)
        ).entries[0]
        terms = [tag.get("term") for tag in entry.get("tags", [])]
        self.assertTrue(is_excluded_category(terms))

    def test_unrelated_category_is_not_excluded(self):
        entry = feedparser.parse(
            _make_single_item_feed("Результаты этапа Кубка России", category="Соревнования", with_image=True)
        ).entries[0]
        terms = [tag.get("term") for tag in entry.get("tags", [])]
        self.assertFalse(is_excluded_category(terms))

    def test_entry_with_image_but_training_category_is_still_dropped_by_fetch_rss(self):
        xml = _make_single_item_feed("Школа инструкторов набирает группу", category="Обучение", with_image=True)
        items = fetch_rss(xml)
        self.assertEqual(items, [], "запись с категорией «обучение» не должна попасть в батч")


class TooOldRuleTests(SimpleTestCase):
    def test_fresh_entry_is_not_too_old(self):
        fresh_date = format_datetime(datetime.now(timezone.utc) - timedelta(days=1))
        entry = feedparser.parse(_make_single_item_feed("Свежая новость", pub_date=fresh_date)).entries[0]
        self.assertFalse(is_too_old(_parse_published_at(entry)))

    def test_entry_older_than_max_age_is_too_old(self):
        old_date = format_datetime(datetime.now(timezone.utc) - timedelta(days=MAX_ARTICLE_AGE_DAYS + 1))
        entry = feedparser.parse(_make_single_item_feed("Старая новость", pub_date=old_date)).entries[0]
        self.assertTrue(is_too_old(_parse_published_at(entry)))

    def test_entry_without_date_is_treated_as_too_old(self):
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
        self.assertTrue(is_too_old(_parse_published_at(entry)))

    def test_old_entry_dropped_by_fetch_rss_even_with_image_and_good_category(self):
        old_date = format_datetime(datetime.now(timezone.utc) - timedelta(days=MAX_ARTICLE_AGE_DAYS + 1))
        xml = _make_single_item_feed("Старая новость про соревнования", category="Соревнования", pub_date=old_date)
        self.assertEqual(fetch_rss(xml), [])


class FeedFixtureTests(SimpleTestCase):
    """Сквозной прогон на реальной зафиксированной фикстуре ТАСС — см. parser/README.md за
    происхождением фикстуры. Правило возраста здесь не проверяется (фикстура "протухнет" со
    временем) — оно уже покрыто TooOldRuleTests на синтетике; здесь проверяем маппинг полей,
    правило "картинка необязательна", стабильность external_url/canonicalize."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.raw_entries = feedparser.parse(FIXTURE_PATH).entries
        import sources.parsing.common as common_module

        original_max_age = common_module.MAX_ARTICLE_AGE_DAYS
        common_module.MAX_ARTICLE_AGE_DAYS = 3650
        try:
            cls.items = fetch_rss(FIXTURE_PATH)
        finally:
            common_module.MAX_ARTICLE_AGE_DAYS = original_max_age

    def test_fixture_has_entries(self):
        self.assertEqual(len(self.raw_entries), FIXTURE_TOTAL_ITEMS)

    def test_entries_kept_regardless_of_image(self):
        self.assertEqual(len(self.items), FIXTURE_TOTAL_ITEMS)

    def test_image_url_matches_presence_of_enclosure(self):
        entries_with_image = {
            canonicalize_url(e.link)
            for e in self.raw_entries
            if any(str(l.get("type", "")).startswith("image/") for l in e.get("links", []))
        }
        self.assertEqual(len(entries_with_image), FIXTURE_ITEMS_WITH_IMAGE)

        items_by_url = {item.external_url: item for item in self.items}
        for url, item in items_by_url.items():
            if url in entries_with_image:
                self.assertTrue(item.image_url, f"у {url} есть enclosure — image_url должен быть заполнен")
            else:
                self.assertIsNone(item.image_url, f"у {url} нет enclosure — image_url должен быть None")

    def test_all_required_fields_present(self):
        for item in self.items:
            self.assertTrue(item.title, f"пустой title у {item.external_url}")
            self.assertTrue(item.summary or item.content, f"пустой текст у {item.external_url}")
            self.assertTrue(item.external_url)
            self.assertIsNotNone(item.source_published_at, f"не распознана дата у {item.external_url}")

    def test_text_has_no_html_tags(self):
        for item in self.items:
            self.assertNotIn("<", item.summary)
            self.assertNotIn("<", item.title)

    def test_external_urls_are_unique(self):
        urls = [item.external_url for item in self.items]
        self.assertEqual(len(urls), len(set(urls)), "нашлись повторяющиеся external_url")

    def test_canonicalize_is_stable(self):
        first = self.items[0]
        matching_raw_entry = next(e for e in self.raw_entries if canonicalize_url(e.link) == first.external_url)
        again = entry_to_parsed_item(matching_raw_entry)
        self.assertEqual(first.external_url, again.external_url)
