"""
Пайплайн LLM-редактора (Этап 3, docs/DATA_MODEL.md «От редактора — к очереди на модерацию»):
собрать батч + промт → вызвать GigaChat → разобрать текстовый ответ на посты → сопоставить
каждый пост с исходной Article по ссылке "Источник: <url>" → применить квоту (3/батч, решение
кодера 07.09.2026) → перевести одобренные в pending_moderation.

Разделитель между постами — "===" на отдельной строке (добавлено в parser/EDITOR_PROMPT.md
07.09.2026). Картинка к посту не передаётся текстом — код сопоставляет пост с Article по
canonicalize(url) из строки "Источник: <url>", а image_url берётся из найденной Article.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from news.models import Article
from sources.parsing.common import canonicalize_url

POST_SEPARATOR = "==="
SOURCE_LINE_RE = re.compile(r"Источник:\s*(\S+)\s*$", re.MULTILINE)

EDITOR_PROMPT_PATH = Path(settings.BASE_DIR) / "parser" / "EDITOR_PROMPT.md"


def load_editor_instructions() -> str:
    """Достаёт исполняемый текст промта из parser/EDITOR_PROMPT.md (внутри тройных кавычек
    ``` ``` ) — единственный источник правды, чтобы не дублировать промт в двух местах."""
    raw = EDITOR_PROMPT_PATH.read_text(encoding="utf-8")
    match = re.search(r"```\n(.*?)\n```", raw, re.DOTALL)
    if not match:
        raise ValueError(f"Не найден текст промта в {EDITOR_PROMPT_PATH}")
    return match.group(1).strip()


# Обрезка текста статьи перед отправкой редактору — найдено 09.09.2026 на реальном батче из
# 26 новостей: полный текст (в среднем ~2 КБ/статья, до 8.5 КБ у самой длинной) на 25 статьях
# давал промт ~64К символов, на котором GigaChat вместо ответа возвращал системную заглушку
# «тема временно ограничена» (не по смыслу текста — заглушка одинаковая что на этом батче, что
# в целом; похоже на исчерпание контекста/квоты, не на реальную модерацию контента). 600
# символов оказалось достаточно для содержательного пересказа без потери конкретики и без
# ошибки — контрольная проверка на том же батче. Точный порог не подобран (могло быть и больше
# 600, меньше 8.5К) — если снова начнёт падать с тем же сообщением на большом батче, уменьшать
# дальше или резать батч на части, не отправлять весь целиком.
ARTICLE_TEXT_LIMIT_CHARS = 600


def build_batch_section(articles: list[Article]) -> str:
    lines = ["<новости_на_рассмотрение>"]
    for article in articles:
        text = (article.content or article.summary)[:ARTICLE_TEXT_LIMIT_CHARS]
        lines.append(
            f"- Заголовок: {article.title}\n"
            f"  Текст: {text}\n"
            f"  Источник: {article.external_url}\n"
            f"  Дата: {article.source_published_at.isoformat() if article.source_published_at else 'неизвестна'}"
        )
    lines.append("</новости_на_рассмотрение>")
    return "\n".join(lines)


def build_recent_context_section(recent_titles: list[str]) -> str:
    if not recent_titles:
        return ""
    lines = ["<уже_опубликовано_недавно>"]
    lines.extend(f"- {title}" for title in recent_titles)
    lines.append("</уже_опубликовано_недавно>")
    return "\n".join(lines)


def build_prompt(articles: list[Article], recent_titles: list[str]) -> str:
    parts = [load_editor_instructions(), build_batch_section(articles)]
    recent_section = build_recent_context_section(recent_titles)
    if recent_section:
        parts.append(recent_section)
    return "\n\n".join(parts)


@dataclass
class EditorPost:
    post_text: str
    article: Article


def parse_editor_response(response_text: str, batch: list[Article]) -> list[EditorPost]:
    """Разбивает ответ по разделителю "===", для каждого поста находит исходную Article по
    строке "Источник: <url>" (canonicalize + точное совпадение). Посты без распознанной или
    неизвестной ссылки отбрасываются — сигнал, что редактор нарушил формат, логируется как
    None-матч (пусть лучше пропадёт один пост, чем упадёт вся таска)."""
    by_url = {canonicalize_url(a.external_url): a for a in batch}

    posts: list[EditorPost] = []
    for chunk in response_text.split(POST_SEPARATOR):
        chunk = chunk.strip()
        if not chunk:
            continue
        match = SOURCE_LINE_RE.search(chunk)
        if not match:
            continue
        article = by_url.get(canonicalize_url(match.group(1)))
        if article is None:
            continue
        posts.append(EditorPost(post_text=chunk, article=article))

    return posts


def apply_editor_verdicts(batch: list[Article], approved_posts: list[EditorPost], quota: int) -> None:
    """Применяет вердикт редактора ко всей батч-выборке. Одобренные сверх квоты — НЕ считаются
    обработанными (editor_processed_at не проставляется), попадут в следующий прогон вместе с
    новым батчем (решение кодера, см. docs/DATA_MODEL.md, «От редактора — к очереди на
    модерацию»)."""
    now = timezone.now()

    approved_posts_sorted = sorted(
        approved_posts,
        key=lambda p: p.article.source_published_at or now,
    )
    taken = approved_posts_sorted[:quota]
    taken_article_ids = {p.article.id for p in taken}

    for post in taken:
        article = post.article
        article.edited_title = _extract_title(post.post_text)
        article.edited_post_text = post.post_text
        article.editor_verdict = "publish"
        article.editor_processed_at = now
        article.status = Article.Status.PENDING_MODERATION
        article.save(
            update_fields=[
                "edited_title",
                "edited_post_text",
                "editor_verdict",
                "editor_processed_at",
                "status",
            ]
        )

    for article in batch:
        if article.id in taken_article_ids:
            continue
        # Одобрено сверх квоты -> оставляем необработанным (следующий прогон).
        was_approved_over_quota = any(p.article.id == article.id for p in approved_posts_sorted)
        if was_approved_over_quota:
            continue
        article.editor_verdict = "reject"
        article.editor_processed_at = now
        article.save(update_fields=["editor_verdict", "editor_processed_at"])


def _extract_title(post_text: str) -> str:
    """Первая непустая строка поста, без markdown-звёздочек — практическое приближение
    "заголовка" для edited_title (используется в письме модератору и дайджесте)."""
    for line in post_text.splitlines():
        line = line.strip()
        if line:
            return line.strip("*").strip()
    return ""
