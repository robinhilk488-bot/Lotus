#!/usr/bin/env bash
# Установка сервера лицензий Kassa на ваш VPS (там же, где другие сайты, или отдельно).
set -euo pipefail
cd "$(dirname "$0")"
[ "$(id -u)" -eq 0 ] || { echo "Запустите от root"; exit 1; }
command -v docker >/dev/null || curl -fsSL https://get.docker.com | sh
if [ ! -f .env ]; then
  echo "KASSA_ADMIN_TOKEN=$(openssl rand -hex 24)" > .env
  echo "PORT=8900" >> .env
  chmod 600 .env
fi
mkdir -p data
docker compose up -d --build
IP="$(curl -s4 --max-time 5 https://api.ipify.org || hostname -I | awk '{print $1}')"
echo
echo "Сервер лицензий работает."
echo "Адрес для воркеров клиентов (KASSA_LICENSE_URL): http://$IP:$(grep '^PORT=' .env | cut -d= -f2)"
echo "Управление кодами: ./manage.sh gen 1 50   |   ./manage.sh stock   |   ./manage.sh servers"
