"""
Форма срочной ручной публикации новости Клуба (13.09.2026, запрос пользователя) — для новостей,
которые не приходят из автопарсера. Доступна по подписанной ссылке из писем модератору (как и
остальные ссылки модерации), без логина. В отличие от обычного пайплайна: публикуется сразу,
минуя расписание/очередь `publish_from_queue`, и всегда `is_urgent=True` — форма для срочных
случаев, не для отложенной публикации (для неё используется автопарсер как обычно).

`moderated_by` НЕ заполняется — доступ к форме, как и ко всем остальным ссылкам модерации в
проекте, без логина (сама подпись токена — авторизация), поэтому нет Django-пользователя,
которого можно было бы сюда подставить. Если это важно отследить — нужен отдельный
непубличный вход с логином, не входит в этот запрос.
"""

import uuid

from django.conf import settings
from django.core import signing
from django.core.files.storage import default_storage
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from news.models import Article
from sources.models import Source

from .tokens import read_manual_publish_token

CLUB_SOURCE_NAME = "SkiDiscoverer Club"

_ERROR_PAGE_TEMPLATE = """<!doctype html><html><head><meta charset="utf-8">
<title>{title}</title></head><body style="font-family:sans-serif;max-width:640px;margin:40px auto;">
<h2>{title}</h2><p>{message}</p></body></html>"""


def _error(title: str, message: str, status: int) -> HttpResponse:
    return HttpResponse(_ERROR_PAGE_TEMPLATE.format(title=title, message=message), status=status)


def _get_or_create_club_source() -> Source:
    source, _ = Source.objects.get_or_create(
        name=CLUB_SOURCE_NAME,
        defaults={
            "source_type": Source.SourceType.MANUAL,
            "url": "https://skidiscoverer.ru/club-news/",
            "lk_category": Source.LkCategory.CLUB,
            "is_active": False,
        },
    )
    return source


@require_http_methods(["GET", "POST"])
def manual_publish_form(request, token: str) -> HttpResponse:
    try:
        read_manual_publish_token(token)
    except signing.SignatureExpired:
        return _error("Ссылка устарела", "Срок действия ссылки истёк.", 410)
    except signing.BadSignature:
        return _error("Некорректная ссылка", "Ссылка повреждена или недействительна.", 400)

    if request.method == "GET":
        return render(request, "moderation/manual_publish_form.html", {"token": token})

    title = (request.POST.get("title") or "").strip()
    content = (request.POST.get("content") or "").strip()
    skip_llm = bool(request.POST.get("skip_llm_formatting"))
    image = request.FILES.get("image")

    if not title or not content:
        return render(
            request,
            "moderation/manual_publish_form.html",
            {"token": token, "error": "Заголовок и текст обязательны.", "title": title, "content": content},
        )

    image_url = ""
    if image:
        saved_name = default_storage.save(f"club-news/{uuid.uuid4()}-{image.name}", image)
        image_url = settings.SITE_BASE_URL.rstrip("/") + settings.MEDIA_URL + saved_name

    article = Article.objects.create(
        source=_get_or_create_club_source(),
        external_url=f"https://skidiscoverer.ru/club-news/{uuid.uuid4()}",
        title=title,
        content=content,
        image_url=image_url,
        is_urgent=True,
        moderated_at=timezone.now(),
    )

    if skip_llm:
        article.edited_title = title
        article.edited_post_text = content
        article.editor_verdict = "publish"
        article.editor_processed_at = timezone.now()
        article.status = Article.Status.APPROVED
        article.save(
            update_fields=["edited_title", "edited_post_text", "editor_verdict", "editor_processed_at", "status"]
        )

        from news.publishing.tasks import _active_channels, publish_article_now

        telegram_channels, lk_channels = _active_channels()
        publish_article_now(article, telegram_channels, lk_channels)
    else:
        from news.editor.tasks import run_editor_for_manual_article

        run_editor_for_manual_article.delay(article.id)

    return render(request, "moderation/manual_publish_done.html", {"article": article, "skip_llm": skip_llm})
