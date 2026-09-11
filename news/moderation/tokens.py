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
