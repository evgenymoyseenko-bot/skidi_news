"""
Дедупликация — второй уровень поверх unique-констрейнта на Article.external_url (см.
docs/TECH_STACK.md, п.4). Сравнивает заголовок новой записи с заголовками недавних Article
через rapidfuzz — ловит "разные источники, один инфоповод".

Порог 87% — разумный дефолт, НЕ подобран эмпирически на реальных данных (см.
docs/TECH_STACK.md, п.4: "порог нужно будет подобрать эмпирически... заложите это как
отдельную задачу тестирования"). Поправить после подключения реальных источников (Этап 8).
"""

from typing import Optional

from rapidfuzz import fuzz

SIMILARITY_THRESHOLD = 87.0
RECENT_WINDOW_DAYS = 7


def find_duplicate(title: str, recent_titles: list[tuple[int, str]]) -> tuple[Optional[int], Optional[float]]:
    """recent_titles — [(article_id, title), ...] за последние RECENT_WINDOW_DAYS. Возвращает
    (id найденного дубликата, similarity_score) либо (None, None)."""
    best_id = None
    best_score = None
    for article_id, other_title in recent_titles:
        score = fuzz.token_sort_ratio(title, other_title)
        if score >= SIMILARITY_THRESHOLD and (best_score is None or score > best_score):
            best_id = article_id
            best_score = score
    return best_id, best_score
