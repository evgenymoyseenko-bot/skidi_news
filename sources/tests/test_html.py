"""
Тесты html-парсера — в т.ч. русский текстовый формат даты и картинка в
style="background-image:url(...)" (оба найдены на реальном сайте rosakhutor.ru при разведке
источников для Этапа A, 08.09.2026 — см. docs/SOURCES.md).
"""

from datetime import datetime, timedelta, timezone

from django.test import SimpleTestCase

from sources.parsing.html import (
    _parse_iso_date,
    _parse_numeric_date,
    _parse_russian_date,
    _parse_russian_date_no_year,
    _parse_short_date,
    fetch_html,
)

_RU_MONTH_NAMES = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


def _format_russian_date(dt: datetime) -> str:
    return f"{dt.day} {_RU_MONTH_NAMES[dt.month - 1]} {dt.year}"


class ParseRussianDateTests(SimpleTestCase):
    def test_date_without_time(self):
        dt = _parse_russian_date("8 Сентября 2026")
        self.assertEqual((dt.year, dt.month, dt.day), (2026, 9, 8))
        self.assertEqual(dt.tzinfo, timezone.utc)

    def test_date_with_time(self):
        dt = _parse_russian_date("15 марта 2026, 14:30")
        self.assertEqual((dt.year, dt.month, dt.day, dt.hour, dt.minute), (2026, 3, 15, 14, 30))

    def test_may_is_not_confused_with_march(self):
        # "мая" (короткий стем) не должен склеиваться с "март"/"марта" — регрессия на substring-матч.
        dt = _parse_russian_date("3 мая 2026")
        self.assertEqual(dt.month, 5)

    def test_unrecognized_text_returns_none(self):
        self.assertIsNone(_parse_russian_date("не дата вообще"))


class ParseShortDateTests(SimpleTestCase):
    """"10.06" без года (sheregesh.ru, разведка 08.09.2026) — год подразумевается текущий,
    с откатом на прошлый год, если получившаяся дата в будущем относительно now."""

    def test_date_earlier_this_year(self):
        now = datetime(2026, 9, 8, tzinfo=timezone.utc)
        dt = _parse_short_date("10.06", now=now)
        self.assertEqual((dt.year, dt.month, dt.day), (2026, 6, 10))

    def test_date_that_would_be_in_the_future_rolls_back_a_year(self):
        now = datetime(2026, 1, 15, tzinfo=timezone.utc)
        dt = _parse_short_date("28.12", now=now)
        self.assertEqual((dt.year, dt.month, dt.day), (2025, 12, 28))

    def test_invalid_format_returns_none(self):
        self.assertIsNone(_parse_short_date("10 июня"))
        self.assertIsNone(_parse_short_date("10.06.2026"))


class ParseNumericDateTests(SimpleTestCase):
    """"27.08.2026" — день.месяц.год числом (igora.ru, разведка 09.09.2026)."""

    def test_parses_numeric_date(self):
        dt = _parse_numeric_date("27.08.2026")
        self.assertEqual((dt.year, dt.month, dt.day), (2026, 8, 27))

    def test_invalid_format_returns_none(self):
        self.assertIsNone(_parse_numeric_date("10.06"))  # без года — другой формат
        self.assertIsNone(_parse_numeric_date("не дата"))


class ParseRussianDateNoYearTests(SimpleTestCase):
    """"19 марта" — день + русский месяц словом, без года (arsenyev.ski, разведка 09.09.2026)."""

    def test_date_earlier_this_year(self):
        now = datetime(2026, 9, 8, tzinfo=timezone.utc)
        dt = _parse_russian_date_no_year("19 марта", now=now)
        self.assertEqual((dt.year, dt.month, dt.day), (2026, 3, 19))

    def test_date_that_would_be_in_the_future_rolls_back_a_year(self):
        now = datetime(2026, 1, 15, tzinfo=timezone.utc)
        dt = _parse_russian_date_no_year("20 декабря", now=now)
        self.assertEqual((dt.year, dt.month, dt.day), (2025, 12, 20))

    def test_invalid_format_returns_none(self):
        self.assertIsNone(_parse_russian_date_no_year("19 марта 2026"))  # с годом — другой формат


class ParseIsoDateTests(SimpleTestCase):
    """<time datetime="2026-09-07"> — найдено на dolina.su, разведка Этапа A, 09.09.2026."""

    def test_date_only(self):
        dt = _parse_iso_date("2026-09-07")
        self.assertEqual((dt.year, dt.month, dt.day), (2026, 9, 7))
        self.assertEqual(dt.tzinfo, timezone.utc)

    def test_date_with_time_and_z(self):
        dt = _parse_iso_date("2026-09-07T14:30:00Z")
        self.assertEqual((dt.hour, dt.minute), (14, 30))

    def test_invalid_format_returns_none(self):
        self.assertIsNone(_parse_iso_date("7 сентября 2026"))


class FetchHtmlWithDateAttrTests(SimpleTestCase):
    """parser_config.date_attr — дата читается из атрибута (например <time datetime="...">),
    а не из текста элемента. Структура карточки — с dolina.su (разведка 09.09.2026)."""

    CONFIG = {
        "item_selector": "li.News__item",
        "link_selector": "a.News__item__card",
        "title_selector": ".News__item__card__dscr__main__title",
        "date_selector": "time",
        "date_attr": "datetime",
        "image_selector": ".News__item__card__imgContainer__img",
        "image_attr": "style",
    }

    def _page_html(self) -> str:
        now = datetime.now(timezone.utc)
        fresh_iso = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        old_iso = (now - timedelta(days=40)).strftime("%Y-%m-%d")
        return f"""
        <html><body><ul class="News">
          <li class="News__item">
            <a class="News__item__card" href="/sitemap/fresh-news">
              <div class="News__item__card__imgContainer__img" style="background-image: url(/photo1.jpg)"></div>
              <time datetime="{fresh_iso}">Опубликовано недавно</time>
              <h2 class="News__item__card__dscr__main__title">Свежая новость</h2>
            </a>
          </li>
          <li class="News__item">
            <a class="News__item__card" href="/sitemap/old-news">
              <div class="News__item__card__imgContainer__img" style="background-image: url(/photo2.jpg)"></div>
              <time datetime="{old_iso}">Опубликовано давно</time>
              <h2 class="News__item__card__dscr__main__title">Старая новость</h2>
            </a>
          </li>
        </ul></body></html>
        """

    def test_uses_datetime_attribute_not_visible_text(self):
        from unittest.mock import Mock, patch

        fake_response = Mock(text=self._page_html(), raise_for_status=Mock())
        with patch("sources.parsing.html.requests.get", return_value=fake_response):
            items = fetch_html("https://dolina.su/sitemap/news", self.CONFIG)

        self.assertEqual(len(items), 1, "старая новость (40 дней) должна быть отброшена")
        item = items[0]
        self.assertEqual(item.title, "Свежая новость")
        self.assertEqual(item.image_url, "https://dolina.su/photo1.jpg")


class FetchHtmlWithBackgroundImageTests(SimpleTestCase):
    """Структура карточки скопирована с реальной вёрстки rosakhutor.ru/news/ (разведка
    08.09.2026): .post > .post_image[style=background-image] + .post_content > .post_date/
    .post_title > a."""

    CONFIG = {
        "item_selector": ".post",
        "link_selector": ".post_title a",
        "title_selector": ".post_title",
        "date_selector": ".post_date",
        "image_selector": ".post_image",
        "image_attr": "style",
    }

    def _page_html(self) -> str:
        now = datetime.now(timezone.utc)
        fresh_date = _format_russian_date(now - timedelta(days=1))
        old_date = _format_russian_date(now - timedelta(days=40))
        return f"""
        <html><body>
          <article class="post">
            <a href="/news/first-article/">
              <div class="post_image" style="background-image: url(/upload/photo1.jpg);"></div>
            </a>
            <div class="post_content">
              <div class="post_date">{fresh_date}</div>
              <h2 class="post_title"><a href="/news/first-article/" class="link">Первая новость</a></h2>
            </div>
          </article>
          <article class="post">
            <a href="/news/old-article/">
              <div class="post_image" style="background-image: url(/upload/photo2.jpg);"></div>
            </a>
            <div class="post_content">
              <div class="post_date">{old_date}</div>
              <h2 class="post_title"><a href="/news/old-article/" class="link">Старая новость</a></h2>
            </div>
          </article>
        </body></html>
        """

    def test_fields_extracted_correctly(self):
        from unittest.mock import Mock, patch

        fake_response = Mock(text=self._page_html(), raise_for_status=Mock())
        with patch("sources.parsing.html.requests.get", return_value=fake_response):
            items = fetch_html("https://rosakhutor.ru/news/", self.CONFIG)

        # Старая новость (40 дней назад) отброшена правилом "не старше 30 дней".
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.title, "Первая новость")
        self.assertEqual(item.external_url, "https://rosakhutor.ru/news/first-article")
        self.assertEqual(item.image_url, "https://rosakhutor.ru/upload/photo1.jpg")
        expected_date = (datetime.now(timezone.utc) - timedelta(days=1)).date()
        self.assertEqual(item.source_published_at.date(), expected_date)
