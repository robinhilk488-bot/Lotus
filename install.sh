#!/usr/bin/env bash
# Установка сервера Lotus на VPS (Ubuntu 22.04+).
# Готовую команду показывает приложение на экране «Как установить сервер».
# Повторный запуск обновляет сервер и снова показывает ключ подключения.
set -euo pipefail

REPO="${KASSA_REPO:-https://github.com/robinhilk488-bot/Lotus.git}"
DIR="${KASSA_DIR:-/opt/kassa}"
PORT="${KASSA_PORT:-8765}"

green() { printf "\033[32m%s\033[0m\n" "$*"; }
step()  { printf "\n\033[1m› %s\033[0m\n" "$*"; }

[ "$(id -u)" -eq 0 ] || { echo "Запустите от root (sudo -i)"; exit 1; }

step "Проверяю зависимости"
apt-get update -qq
apt-get install -y -qq git curl openssl >/dev/null
if ! command -v docker >/dev/null; then
  step "Устанавливаю Docker"
  curl -fsSL https://get.docker.com | sh
fi

step "Загружаю воркер"
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" pull --ff-only
else
  git clone --depth 1 "$REPO" "$DIR"
fi
cd "$DIR"

if [ ! -f .env ]; then
  echo "KASSA_TOKEN=$(openssl rand -hex 24)" > .env
  echo "KASSA_PORT=$PORT" >> .env
  # Адрес вашего сервера лицензий. Пусто — плагины работают без подписки (режим «для себя»).
  echo "KASSA_LICENSE_URL=${KASSA_LICENSE_URL:-}" >> .env
  chmod 600 .env
fi
PORT="$(grep '^KASSA_PORT=' .env | cut -d= -f2)"

mkdir -p data/certs
if [ ! -f data/certs/cert.pem ]; then
  step "Создаю сертификат для шифрованного подключения"
  openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -subj "/CN=kassa-worker" \
    -keyout data/certs/key.pem -out data/certs/cert.pem 2>/dev/null
  chmod 600 data/certs/key.pem
fi

step "Запускаю контейнер"
docker compose up -d --build

if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
  ufw allow "$PORT"/tcp >/dev/null
fi

step "Настраиваю автообновление"
# Служба обновляется с GitHub каждые 5 минут — клиент всегда на свежей версии.
# Путь $DIR подставляется сразу; служебные $(...) экранированы, чтобы попасть в файл как есть.
cat > /usr/local/bin/lotus-update.sh << UPD
#!/bin/bash
set -e
cd $DIR || exit 1
git fetch origin main --quiet
[ "\$(git rev-parse HEAD)" = "\$(git rev-parse origin/main)" ] && exit 0
echo "\$(date '+%F %T') обновляю..." >> /var/log/lotus-update.log
git reset --hard origin/main >> /var/log/lotus-update.log 2>&1
docker compose up -d --build >> /var/log/lotus-update.log 2>&1
echo "\$(date '+%F %T') обновлено" >> /var/log/lotus-update.log
UPD
chmod +x /usr/local/bin/lotus-update.sh

cat > /etc/systemd/system/lotus-update.service << 'UNIT'
[Unit]
Description=Lotus auto-update
After=network-online.target docker.service
[Service]
Type=oneshot
ExecStart=/usr/local/bin/lotus-update.sh
UNIT

cat > /etc/systemd/system/lotus-update.timer << 'TIMER'
[Unit]
Description=Lotus auto-update
[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
[Install]
WantedBy=timers.target
TIMER

systemctl daemon-reload
systemctl enable --now lotus-update.timer >/dev/null 2>&1

TOKEN="$(grep '^KASSA_TOKEN=' .env | cut -d= -f2)"
FP="$(openssl x509 -in data/certs/cert.pem -noout -fingerprint -sha256 | cut -d= -f2 | tr -d ':')"
IP="$(curl -s4 --max-time 5 https://api.ipify.org || hostname -I | awk '{print $1}')"

KEY="lotus_$(printf '%s' "$IP|$PORT|$TOKEN|$FP" | base64 -w0 | tr '+/' '-_' | tr -d '=')"

echo
green "Готово. Сервер работает и будет перезапускаться сам."
echo
echo "Ваш ключ подключения — скопируйте его в приложение Lotus:"
echo
green "$KEY"
echo
echo "Никому не передавайте ключ: он даёт полный доступ к серверу."
echo "Показать ключ ещё раз: bash $DIR/install.sh"
