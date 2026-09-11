"""
Celery-таски парсинга (Этап 2, docs/CODER_INSTRUCTIONS.md) — одна периодическая задача
`run_all_active_sources`, которая сама решает, кого из источников пора опрашивать по
`parse_interval_minutes` (см. docs/DATA_MODEL.md, «Расписание парсинга» — проще поддерживать,
чем задача на каждый источник).
"""

import logging
from datetime import timedelta

from celery import shared_task
from django.db import IntegrityError
from django.utils import timezone

from news.models import Article

from .models import Source
from .parsing.common import ParsedItem
from .parsing.dedup import RECENT_WINDOW_DAYS, find_duplicate
from .parsing.html import fetch_article_text, fetch_html
from .parsing.rss import fetch_rss

logger = logging.getLogger(__name__)


def _save_parsed_item(source: Source, item: ParsedItem) -> Article | None:
    """Сохраняет ParsedItem как Article. Дедуп: сначала точное совпадение external_url (unique
    констрейнт на уровне БД — самый дешёвый фильтр), затем rapidfuzz по заголовкам за последние
    RECENT_WINDOW_DAYS дней (docs/TECH_STACK.md, п.4). Возвращает None, если это дубликат по
    external_url (запись уже существует, ничего нового не создано)."""
    try:
        article = Article.objects.create(
            source=source,
            external_url=item.external_url,
            external_id=item.external_id,
            title=item.title,
            summary=item.summary,
            content=item.content,
            image_url=item.image_url or "",
            category=source.default_category,
            source_published_at=item.source_published_at,
            raw_payload=item.raw_payload,
            status=Article.Status.NEW,
        )
    except IntegrityError:
        # external_url уже есть в БД — тот же URL, ничего не делаем повторно.
        return None

    recent_cutoff = timezone.now() - timedelta(days=RECENT_WINDOW_DAYS)
    recent_titles = list(
        Article.objects.filter(fetched_at__gte=recent_cutoff)
        .exclude(pk=article.pk)
        .values_list("id", "title")
    )
    duplicate_id, score = find_duplicate(article.title, recent_titles)
    if duplicate_id is not None:
        article.status = Article.Status.DUPLICATE
        article.duplicate_of_id = duplicate_id
        article.similarity_score = score
        article.save(update_fields=["status", "duplicate_of", "similarity_score"])

    return article


@shared_task
def parse_source(source_id: int) -> dict:
    source = Source.objects.get(pk=source_id)
    now = timezone.now()

    try:
        if source.source_type == Source.SourceType.RSS:
            items = fetch_rss(source.url)
        else:
            items = fetch_html(source.url, source.parser_config)
    except Exception as exc:  # сетевые/парсинг-ошибки — не молчим, фиксируем в Source
        logger.exception("Ошибка парсинга источника %s (%s)", source.name, source.url)
        source.last_parsed_at = now
        source.last_parse_status = "error"
        source.last_parse_error = str(exc)
        source.save(update_fields=["last_parsed_at", "last_parse_status", "last_parse_error"])
        return {"source_id": source_id, "status": "error", "error": str(exc)}

    created = 0
    for item in items:
        if source.source_type == Source.SourceType.HTML and not item.content:
            # Страница списка обычно даёт только заголовок/анонс — без текста редактору
            # (GigaChat) нечем писать пост, кроме заголовка, что на практике приводит к
            # выдумыванию деталей (найдено 09.09.2026 на реальном батче: спутал курорт при
            # переписывании новости без текста). Полный текст — отдельным запросом на
            # страницу самой статьи через trafilatura (см. docs/TECH_STACK.md, п.3).
            try:
                item.content = fetch_article_text(item.external_url)
            except Exception:
                logger.exception("Не удалось получить полный текст статьи %s", item.external_url)

        article = _save_parsed_item(source, item)
        if article is not None:
            created += 1

    source.last_parsed_at = now
    source.last_parse_status = "ok"
    source.last_parse_error = None
    source.save(update_fields=["last_parsed_at", "last_parse_status", "last_parse_error"])

    return {"source_id": source_id, "status": "ok", "fetched": len(items), "created": created}


@shared_task
def run_all_active_sources() -> dict:
    now = timezone.now()
    due_source_ids = [s.id for s in Source.objects.filter(is_active=True) if s.is_due(now)]
    for source_id in due_source_ids:
        parse_source.delay(source_id)
    return {"triggered": due_source_ids}
