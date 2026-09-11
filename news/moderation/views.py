"""
GET-view по клику из письма модерации — сознательное отступление от "GET не должен менять
состояние" ради UX одного клика (см. docs/DATA_MODEL.md, п.3, тот же паттерн, что и типовые
email-unsubscribe-ссылки). Не требует логина — сама подпись токена и есть авторизация.
"""

from django.core import signing
from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.http import require_GET

from news.models import Article

from .tokens import read_token

_PAGE_TEMPLATE = """<!doctype html><html><head><meta charset="utf-8">
<title>Модерация новости</title></head><body style="font-family:sans-serif;max-width:640px;margin:40px auto;">
<h2>{title}</h2><p>{message}</p></body></html>"""


def _render(title: str, message: str, status: int = 200) -> HttpResponse:
    return HttpResponse(_PAGE_TEMPLATE.format(title=title, message=message), status=status)


@require_GET
def moderation_action(request, token: str) -> HttpResponse:
    try:
        payload = read_token(token)
    except signing.SignatureExpired:
        return _render("Ссылка устарела", "Срок действия ссылки истёк.", status=410)
    except signing.BadSignature:
        return _render("Некорректная ссылка", "Ссылка повреждена или недействительна.", status=400)

    try:
        article = Article.objects.get(pk=payload["article_id"])
    except Article.DoesNotExist:
        return _render("Новость не найдена", "Эта новость больше не существует.", status=404)

    action = payload["action"]

    # Идемпотентность: и обычное письмо (pending_moderation -> approved/rejected), и
    # еженедельный дайджест (approved -> approved/rejected повторно) используют один и тот же
    # view — см. docs/DATA_MODEL.md, п.6.
    if action in ("publish", "publish_urgent"):
        if article.status not in (Article.Status.PENDING_MODERATION, Article.Status.APPROVED):
            return _render(
                "Уже обработано",
                f"Новость «{article.edited_title or article.title}» уже находится в статусе "
                f"«{article.get_status_display()}» — повторный клик ничего не меняет.",
            )
        article.status = Article.Status.APPROVED
        article.is_urgent = action == "publish_urgent"
        article.moderated_at = timezone.now()
        article.save(update_fields=["status", "is_urgent", "moderated_at"])
        return _render(
            "Одобрено" + (" (срочно)" if article.is_urgent else ""),
            f"Новость «{article.edited_title or article.title}» поставлена в очередь публикации.",
        )

    if action == "reject":
        if article.status not in (Article.Status.PENDING_MODERATION, Article.Status.APPROVED):
            return _render(
                "Уже обработано",
                f"Новость «{article.edited_title or article.title}» уже находится в статусе "
                f"«{article.get_status_display()}» — повторный клик ничего не меняет.",
            )
        article.status = Article.Status.REJECTED
        article.moderated_at = timezone.now()
        article.save(update_fields=["status", "moderated_at"])
        return _render("Отклонено", f"Новость «{article.edited_title or article.title}» отклонена.")

    return _render("Неизвестное действие", "Не удалось распознать действие в ссылке.", status=400)
