#!/usr/bin/env bash
# Управление кодами подписки. Запускать на сервере лицензий из папки license-server.
#   ./manage.sh gen 1 50       — создать 50 кодов на 1 месяц
#   ./manage.sh gen 12 10      — создать 10 кодов на 12 месяцев
#   ./manage.sh stock          — сколько свободных кодов по планам
#   ./manage.sh servers        — список серверов клиентов и их статус
set -euo pipefail
cd "$(dirname "$0")"
URL="http://127.0.0.1:${PORT:-8900}"
TOKEN="$(grep '^KASSA_ADMIN_TOKEN=' .env | cut -d= -f2)"
H="X-Admin-Token: $TOKEN"
case "${1:-}" in
  gen)
    months="${2:?Укажите месяцы: 1 3 6 12}"; count="${3:?Укажите количество}"
    curl -s -X POST "$URL/admin/generate" -H "$H" -H "Content-Type: application/json" \
      -d "{\"months\":$months,\"count\":$count}" | python3 -c "import sys,json;d=json.load(sys.stdin);print('\n'.join(d.get('codes',[d.get('error','ошибка')])))"
    ;;
  stock)   curl -s "$URL/admin/codes?status=free" -H "$H" | python3 -c "import sys,json;d=json.load(sys.stdin);print('Свободных кодов:', d.get('free_by_plan', d))";;
  servers) curl -s "$URL/admin/servers" -H "$H" | python3 -m json.tool;;
  *) echo "Использование: $0 {gen МЕСЯЦЫ КОЛ-ВО | stock | servers}";;
esac
