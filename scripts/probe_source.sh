#!/usr/bin/env bash
# Разведка кандидата в источники новостей (Этап A, docs/SOURCES.md) — повторяет вручную
# накопленные шаги: статус страницы, правила robots.txt для конкретного пути, для RSS/XML —
# число записей и первые заголовки, для картинок — реальный content-type (частая находка:
# страница-заглушка антибот-проверки маскируется под 200 OK, но content-type — text/html, не
# image/*, см. docs/SOURCES.md про rosakhutor.ru).
#
# Использование:
#   ./scripts/probe_source.sh <url>
#
# Фиксированный набор флагов/поведения — безопасно заносить в allowlist разрешений как
# `Bash(./scripts/probe_source.sh *)`: скрипт только читает (GET-запросы), ничего не отправляет
# и не меняет на удалённой стороне, варьируется только сам URL-аргумент.

set -euo pipefail

if [ $# -ne 1 ]; then
  echo "Использование: $0 <url>" >&2
  exit 1
fi

URL="$1"
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
TIMEOUT=15

# Домен для robots.txt — схема + хост из URL.
DOMAIN=$(printf '%s' "$URL" | sed -E 's#^(https?://[^/]+).*#\1#')
PATH_ONLY=$(printf '%s' "$URL" | sed -E 's#^https?://[^/]+##')
[ -z "$PATH_ONLY" ] && PATH_ONLY="/"

echo "=== URL ==="
echo "$URL"
echo

echo "=== robots.txt ($DOMAIN/robots.txt) ==="
ROBOTS=$(curl -s --max-time "$TIMEOUT" -A "$UA" "$DOMAIN/robots.txt" || true)
if [ -z "$ROBOTS" ]; then
  echo "(пусто / недоступен)"
else
  # Показываем только блок User-agent: * целиком плюс любые строки, где путь запроса — префикс
  # (грубая эвристика, не полноценный robots.txt-парсер — для точной проверки использовать
  # sources/parsing или Python urllib.robotparser).
  echo "$ROBOTS" | awk '
    /^[Uu]ser-[Aa]gent:[[:space:]]*\*/ { inblock=1 }
    /^[Uu]ser-[Aa]gent:/ && !/\*/ { inblock=0 }
    inblock { print }
  '
fi
echo

echo "=== Запрос страницы ==="
HEADERS_FILE=$(mktemp)
BODY_FILE=$(mktemp)
trap 'rm -f "$HEADERS_FILE" "$BODY_FILE"' EXIT

HTTP_CODE=$(curl -s -D "$HEADERS_FILE" -o "$BODY_FILE" -w "%{http_code}" \
  --max-time "$TIMEOUT" -A "$UA" -H "Accept-Language: ru-RU,ru;q=0.9" -L "$URL" || echo "000")

CONTENT_TYPE=$( (grep -i '^content-type:' "$HEADERS_FILE" || true) | tail -1 | cut -d: -f2- | tr -d '\r' | sed 's/^ //')
SIZE=$(wc -c < "$BODY_FILE" | tr -d ' ')

echo "HTTP статус: $HTTP_CODE"
echo "Content-Type: ${CONTENT_TYPE:-(нет заголовка)}"
echo "Размер тела: $SIZE байт"
echo

case "$CONTENT_TYPE" in
  *xml*|*rss*)
    echo "=== Похоже на RSS/XML — сводка ==="
    ITEM_COUNT=$(grep -c '<item' "$BODY_FILE" || true)
    echo "Найдено <item>: ${ITEM_COUNT:-0}"
    echo "Первые заголовки:"
    (grep -o '<title>[^<]*</title>' "$BODY_FILE" || true) | head -6 | sed 's/^/  /'
    echo "Первые даты публикации:"
    (grep -o '<pubDate>[^<]*</pubDate>' "$BODY_FILE" || true) | head -6 | sed 's/^/  /'
    ;;
  image/*)
    echo "=== Это картинка — OK, content-type подтверждает ==="
    ;;
  text/html*)
    echo "=== HTML-страница — реальный content-type: text/html ==="
    echo "(если ожидалась картинка — это, скорее всего, антибот-заглушка, не файл; см. docs/SOURCES.md про rosakhutor.ru)"
    ;;
esac
