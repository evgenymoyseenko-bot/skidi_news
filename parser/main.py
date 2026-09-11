"""
Точка входа минимального скелета: один источник, без дедупликации.

Что делает: тянет RSS одного источника, приводит записи к схеме NewsItem, печатает и
сохраняет батч в JSON. Это ЗАГЛУШКА вместо реальной передачи батча редактору — как именно
батч должен уходить редактору (прямой вызов LLM API, очередь, файл) — открытый вопрос,
см. архитектурное обсуждение. Дальше по плану: дедупликация, несколько источников, реальный
триггер батча (расписание/микробатч), интеграция с остальным.
"""

import json
from dataclasses import asdict

from sources import SOURCES, fetch_source


def run() -> list[dict]:
    batch: list[dict] = []
    for source in SOURCES:
        items = fetch_source(source)
        print(f"[info] источник {source.name!r}: спарсено {len(items)} записей")
        batch.extend(asdict(item) for item in items)
    return batch


if __name__ == "__main__":
    result_batch = run()

    print(json.dumps(result_batch, ensure_ascii=False, indent=2))

    with open("batch_output.json", "w", encoding="utf-8") as f:
        json.dump(result_batch, f, ensure_ascii=False, indent=2)

    print(f"\n[info] батч из {len(result_batch)} новостей сохранён в batch_output.json")
