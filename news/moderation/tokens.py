"""
Подписанные ссылки-действия — django.core.signing, без отдельной таблицы токенов (см.
docs/DATA_MODEL.md, «Модерация по email и очередь публикации», п.3). Подпись сама себя
валидирует; защита от повторного использования — не одноразовость токена, а проверка текущего
status статьи в view (news/moderation/views.py) в момент перехода по ссылке.
"""

from django.conf import settings
from django.core import signing

ACTIONS = ("publish", "publish_urgent", "reject")

_SALT = "news.moderation"


def make_token(article_id: int, action: str) -> str:
    if action not in ACTIONS:
        raise ValueError(f"Неизвестное действие модерации: {action!r}")
    payload = signing.dumps({"article_id": article_id, "action": action}, salt=_SALT, key=settings.MODERATION_SIGNING_KEY)
    return payload


def read_token(token: str) -> dict:
    """Возвращает {"article_id": int, "action": str}. Бросает signing.BadSignature /
    signing.SignatureExpired при невалидном/просроченном токене — обрабатывается в view."""
    return signing.loads(
        token,
        salt=_SALT,
        key=settings.MODERATION_SIGNING_KEY,
        max_age=settings.MODERATION_TOKEN_MAX_AGE_SECONDS,
    )


# Форма срочной ручной публикации новости Клуба (13.09.2026, news/moderation/manual_publish.py)
# — отдельная соль: токен не привязан к конкретной Article (её ещё не существует на момент
# перехода по ссылке, форма её создаёт), поэтому не может использовать make_token/read_token
# выше (у тех payload обязательно содержит article_id существующей записи). Разная соль также
# не даёт токену формы случайно провалидироваться как токен действия модерации, и наоборот.
_MANUAL_PUBLISH_SALT = "news.manual_publish"


def make_manual_publish_token() -> str:
    return signing.dumps({"purpose": "manual_publish"}, salt=_MANUAL_PUBLISH_SALT, key=settings.MODERATION_SIGNING_KEY)


def read_manual_publish_token(token: str) -> dict:
    """Бросает signing.BadSignature/signing.SignatureExpired — обрабатывается в view, как и
    read_token()."""
    return signing.loads(
        token,
        salt=_MANUAL_PUBLISH_SALT,
        key=settings.MODERATION_SIGNING_KEY,
        max_age=settings.MODERATION_TOKEN_MAX_AGE_SECONDS,
    )
