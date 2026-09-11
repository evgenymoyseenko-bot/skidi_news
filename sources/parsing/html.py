"""
HTML-парсер для source_type='html' (Этап 2, docs/CODER_INSTRUCTIONS.md) — requests + trafilatura
+ lxml-селекторы из Source.parser_config. В отличие от RSS-ветки, здесь нет готового
эталонного кода в parser/ — контракт (ParsedItem) тот же, реализация с нуля.

Ожидаемый формат `Source.parser_config` (см. docs/DATA_MODEL.md):
{
    "item_selector": "...",     # CSS или XPath (см. _select) — один элемент на карточку/ссылку
    "title_selector": "...",    # относительно найденного item, опционально (иначе — текст ссылки)
    "link_selector": "...",     # относительно item, опционально (иначе сам item — это <a>)
    "date_selector": "...",     # относительно item, опционально
    "image_selector": "...",    # относительно item, опционально
    "image_attr": "src",        # опционально: "src" (по умолчанию) или "style" — картинка часто
                                 # лежит не в <img src>, а в style="background-image:url(...)"
                                 # (найдено на rosakhutor.ru при разведке Этапа A, 08.09.2026)
    "date_attr": "...",         # опционально: если задано, дата читается из атрибута элемента
                                 # date_selector (например "datetime" у <time datetime="2026-09-07">
                                 # — ISO, самый надёжный вариант), а не из его текста. Найдено на
                                 # dolina.su при разведке Этапа A, 09.09.2026.
    "date_selector_detail": "...",  # опционально: если date_selector на листинге ничего не дал —
                                 # запрос страницы самой статьи, селектор применяется уже там
                                 # (например ".news-single__date" — найдено на polyanaski.ru,
                                 # Газпром Поляна, разведка 11.09.2026: без этого источник давал
                                 # 0 новостей — все карточки отбрасывались как "без даты").
    "date_detail_attr": "...",  # опционально, пара к date_selector_detail — атрибут вместо текста,
                                 # как date_attr, но для страницы статьи.
}

Даты на русскоязычных сайтах курортов обычно текстовые ("8 сентября 2026", без ISO/RFC-формата)
— dateutil их не понимает без включённой русской локали, поэтому здесь свой лёгкий парсер
(_parse_russian_date) вместо новой зависимости.

Полный текст статьи (Article.content) извлекается отдельным запросом на страницу конкретной
новости через trafilatura — сама страница списка обычно даёт только заголовок/ссылку/анонс.
"""

import re
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urljoin

import requests
import trafilatura
from lxml import html as lxml_html

from .common import ParsedItem, canonicalize_url, is_too_old, strip_html

REQUEST_TIMEOUT_SECONDS = 15
# Браузероподобный UA + Accept/Accept-Language — найдено на разведке 08.09.2026: наш собственный
# идентифицирующий UA ("skidiscoverer-news-parser/1.0") получал 410 Gone на resort-arkhyz.ru
# (Bitrix-антибот блокирует по паттерну UA, не по robots.txt — /news/ там разрешён явно). С
# браузерным UA + этими двумя заголовками сайт отдаёт обычный 200. Решение — не пытаться
# полностью притвориться конкретным браузером/ОС, а взять минимальный набор заголовков, который
# реальные браузеры всегда посылают и который явно требуют некоторые сайты.
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
REQUEST_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9",
}

# Стем месяца (родительный падеж, "8 СЕНТЯБРЯ 2026") -> номер месяца. Матчим по подстроке
# (стему), чтобы не зависеть от точного окончания — найдено на реальных сайтах курортов
# (rosakhutor.ru, разведка 08.09.2026), формат без времени: "8 Сентября 2026".
_RU_MONTH_STEMS = {
    "январ": 1,
    "феврал": 2,
    "март": 3,
    "апрел": 4,
    "ма": 5,  # "мая" — короткий стем, матчим последним после более длинных, чтобы не перебить их
    "июн": 6,
    "июл": 7,
    "август": 8,
    "сентябр": 9,
    "октябр": 10,
    "ноябр": 11,
    "декабр": 12,
}
_RU_DATE_RE = re.compile(
    r"(?P<day>\d{1,2})\s+(?P<month>[а-яА-ЯёЁ]+)\s+(?P<year>\d{4})"
    r"(?:[,\s]+(?P<hour>\d{1,2}):(?P<minute>\d{2}))?"
)
# "10.06" — день.месяц БЕЗ года (найдено на sheregesh.ru, разведка 08.09.2026): год не указан,
# подразумевается текущий год относительно момента парсинга.
_SHORT_DATE_RE = re.compile(r"^(?P<day>\d{1,2})\.(?P<month>\d{1,2})$")
# "27.08.2026" — день.месяц.год числом, опционально с временем и/или окружающим текстом вроде
# "Опубликовано 08.07.2026 10:04" (найдено на igora.ru и krasnoeozero.ru, разведка 09.09.2026) —
# search(), а не match(), т.к. дата не всегда занимает всю строку целиком.
_NUMERIC_DATE_RE = re.compile(
    r"(?P<day>\d{1,2})\.(?P<month>\d{1,2})\.(?P<year>\d{4})"
    r"(?:[,\s]+(?P<hour>\d{1,2}):(?P<minute>\d{2}))?"
)
# "19 марта" — день + русский месяц словом, БЕЗ года (найдено на arsenyev.ski, разведка
# 09.09.2026) — отдельно от _RU_DATE_RE, где год обязателен.
_RU_DATE_NO_YEAR_RE = re.compile(r"^(?P<day>\d{1,2})\s+(?P<month>[а-яА-ЯёЁ]+)$")


def _select(tree, selector: str):
    """CSS или XPath — если строка начинается с '/', считаем её XPath, иначе CSS
    (cssselect поверх lxml)."""
    if not selector:
        return []
    if selector.startswith("/"):
        return tree.xpath(selector)
    return tree.cssselect(selector)


def _select_one_text(item, selector: Optional[str]) -> str:
    if not selector:
        return ""
    found = _select(item, selector)
    if not found:
        return ""
    node = found[0]
    text = node.text_content() if hasattr(node, "text_content") else str(node)
    return strip_html(text)


def _select_one_attr(item, selector: Optional[str], attr: str) -> Optional[str]:
    if not selector:
        return None
    found = _select(item, selector)
    if not found:
        return None
    node = found[0]
    return node.get(attr) if hasattr(node, "get") else None


def _extract_image_url(item, selector: Optional[str], attr: str) -> Optional[str]:
    """attr='src' — обычный <img src=...>. attr='style' — картинка в
    style="background-image:url(...)" (см. parser_config.image_attr в докстринге модуля)."""
    if not selector:
        return None
    found = _select(item, selector)
    if not found:
        return None
    node = found[0]
    if attr == "style":
        style = node.get("style") or ""
        match = re.search(r"url\((['\"]?)(.*?)\1\)", style)
        return match.group(2) if match else None
    return node.get(attr)


def _month_number(word: str) -> Optional[int]:
    word_lower = word.lower()
    for stem, number in sorted(_RU_MONTH_STEMS.items(), key=lambda kv: -len(kv[0])):
        if stem in word_lower:
            return number
    return None


def _parse_russian_date(raw: str) -> Optional[datetime]:
    """"8 сентября 2026" / "8 сентября 2026, 14:30" — типичный формат на сайтах курортов (найдено
    на rosakhutor.ru, разведка Этапа A, 08.09.2026). Без ISO/RFC-формата dateutil без включённой
    русской локали такое не понимает, поэтому свой лёгкий парсер вместо новой зависимости."""
    match = _RU_DATE_RE.search(raw)
    if not match:
        return None
    month = _month_number(match.group("month"))
    if month is None:
        return None
    hour = int(match.group("hour") or 0)
    minute = int(match.group("minute") or 0)
    try:
        return datetime(
            int(match.group("year")), month, int(match.group("day")), hour, minute, tzinfo=timezone.utc
        )
    except ValueError:
        return None


def _resolve_ambiguous_year(day: int, month: int, *, now: Optional[datetime] = None) -> Optional[datetime]:
    """Общий хелпер для форматов без года: берём текущий год, а если получившаяся дата в
    будущем относительно now (типичный случай в январе-феврале для записи вроде "28.12") —
    значит это прошлый год, а не следующий."""
    now = now or datetime.now(timezone.utc)
    try:
        candidate = datetime(now.year, month, day, tzinfo=timezone.utc)
    except ValueError:
        return None
    if candidate > now:
        candidate = candidate.replace(year=now.year - 1)
    return candidate


def _parse_short_date(raw: str, *, now: Optional[datetime] = None) -> Optional[datetime]:
    """"10.06" (день.месяц, без года — sheregesh.ru)."""
    match = _SHORT_DATE_RE.match(raw.strip())
    if not match:
        return None
    return _resolve_ambiguous_year(int(match.group("day")), int(match.group("month")), now=now)


def _parse_numeric_date(raw: str) -> Optional[datetime]:
    """"27.08.2026" / "Опубликовано 08.07.2026 10:04" — день.месяц.год числом, время опционально
    (igora.ru, krasnoeozero.ru, разведка 09.09.2026)."""
    match = _NUMERIC_DATE_RE.search(raw.strip())
    if not match:
        return None
    hour = int(match.group("hour") or 0)
    minute = int(match.group("minute") or 0)
    try:
        return datetime(
            int(match.group("year")), int(match.group("month")), int(match.group("day")), hour, minute, tzinfo=timezone.utc
        )
    except ValueError:
        return None


def _parse_iso_date(raw: str) -> Optional[datetime]:
    """Атрибут datetime у <time> ("2026-09-07", иногда с временем/зоной) — самый надёжный
    формат, когда он есть (найдено на dolina.su, разведка Этапа A, 09.09.2026). Пробуется
    первым в _parse_date, но в отдельной функции, т.к. fetch_html может подать его напрямую
    через parser_config.date_attr, минуя текст элемента."""
    try:
        dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_russian_date_no_year(raw: str, *, now: Optional[datetime] = None) -> Optional[datetime]:
    """"19 марта" — день + русский месяц словом, без года (arsenyev.ski, разведка 09.09.2026)."""
    match = _RU_DATE_NO_YEAR_RE.match(raw.strip())
    if not match:
        return None
    month = _month_number(match.group("month"))
    if month is None:
        return None
    return _resolve_ambiguous_year(int(match.group("day")), month, now=now)


def _parse_date(raw: str) -> Optional[datetime]:
    """Пробуем по очереди известные форматы, встреченные на реальных сайтах курортов (разведка
    Этапа A, 08-09.09.2026), от самого частого к самому редкому, затем dateutil как более общий
    fallback для источников с другим форматом даты (ISO/RFC и т.п.) — конкретный формат
    конкретного источника всё равно нужно свериться при подключении (работа кодера)."""
    if not raw:
        return None

    for parser in (
        _parse_iso_date,
        _parse_russian_date,
        _parse_numeric_date,
        _parse_short_date,
        _parse_russian_date_no_year,
    ):
        parsed = parser(raw)
        if parsed is not None:
            return parsed

    try:
        from dateutil import parser as dateutil_parser  # опциональная зависимость

        dt = dateutil_parser.parse(raw, fuzzy=True)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _fetch_detail_page_date(url: str, selector: str, attr: Optional[str] = None) -> Optional[datetime]:
    """Дата есть только на странице самой статьи, не в карточке на листинге (найдено на
    polyanaski.ru — Газпром Поляна, разведка 11.09.2026: без этого добора ВСЕ записи источника
    отбрасывались как "без даты = устаревшие" ещё на этапе парсинга списка, до какого-либо
    дальнейшего обогащения). Отдельный запрос на страницу статьи — параметр `date_selector_detail`
    в parser_config. Дороже по времени (доп. HTTP-запрос на каждую карточку без даты в листинге),
    но иначе источник просто не даёт новостей."""
    try:
        resp = requests.get(url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
    except Exception:
        return None
    tree = lxml_html.fromstring(resp.text)
    text = _select_one_attr(tree, selector, attr) if attr else _select_one_text(tree, selector)
    return _parse_date(text or "")


def fetch_html(source_url: str, parser_config: dict) -> list[ParsedItem]:
    response = requests.get(source_url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    tree = lxml_html.fromstring(response.text)

    item_selector = parser_config.get("item_selector")
    if not item_selector:
        return []

    items: list[ParsedItem] = []
    for item in _select(tree, item_selector):
        link_selector = parser_config.get("link_selector")
        href = _select_one_attr(item, link_selector, "href") if link_selector else item.get("href")
        if not href:
            continue
        absolute_url = urljoin(source_url, href)

        title = _select_one_text(item, parser_config.get("title_selector")) or strip_html(
            item.text_content() if hasattr(item, "text_content") else ""
        )
        date_selector = parser_config.get("date_selector")
        date_attr = parser_config.get("date_attr")
        date_text = (
            _select_one_attr(item, date_selector, date_attr) or ""
            if date_attr
            else _select_one_text(item, date_selector)
        )
        published_at = _parse_date(date_text)

        date_selector_detail = parser_config.get("date_selector_detail")
        if published_at is None and date_selector_detail:
            published_at = _fetch_detail_page_date(
                absolute_url, date_selector_detail, parser_config.get("date_detail_attr")
            )

        if is_too_old(published_at):
            continue

        image_attr = parser_config.get("image_attr", "src")
        raw_image = _extract_image_url(item, parser_config.get("image_selector"), image_attr)
        image_url = urljoin(source_url, raw_image) if raw_image else None

        items.append(
            ParsedItem(
                external_url=canonicalize_url(absolute_url),
                title=title,
                image_url=image_url,
                source_published_at=published_at,
                raw_payload={"link": absolute_url, "title": title},
            )
        )

    return items


def fetch_article_text(article_url: str) -> str:
    """Полный текст статьи со страницы — trafilatura (см. docs/TECH_STACK.md, п.3)."""
    downloaded = trafilatura.fetch_url(article_url)
    if not downloaded:
        return ""
    extracted = trafilatura.extract(downloaded) or ""
    return re.sub(r"\s+", " ", extracted).strip()


__all__ = ["fetch_html", "fetch_article_text"]
