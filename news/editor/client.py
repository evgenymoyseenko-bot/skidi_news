"""
Обёртка над GigaChat SDK (Этап 3, docs/TECH_STACK.md п.5а) — физлицо, тариф Pro, 1 поток
генерации, наш паттерн — один последовательный запрос на батч 2 раза в день, конкурентность не
нужна (лимиты проверены, см. TECH_STACK.md).
"""

from django.conf import settings


class GigaChatEditorClient:
    """Тонкая обёртка — вызов LLM изолирован здесь, чтобы pipeline.py и таски были тестируемы
    без реального сетевого вызова (замена клиента на mock в тестах)."""

    def __init__(self):
        self._chat = None

    def _get_chat(self):
        if self._chat is None:
            from gigachat import GigaChat

            self._chat = GigaChat(
                credentials=settings.GIGACHAT_CREDENTIALS,
                scope=settings.GIGACHAT_SCOPE,
                model=settings.GIGACHAT_MODEL,
                verify_ssl_certs=False,
            )
        return self._chat

    def complete(self, prompt: str) -> str:
        """Отправляет промт целиком (инструкция + батч + контекст), возвращает текстовый ответ
        редактора как есть — разбор на посты делает news/editor/pipeline.py."""
        chat = self._get_chat()
        response = chat.chat(prompt)
        return response.choices[0].message.content
