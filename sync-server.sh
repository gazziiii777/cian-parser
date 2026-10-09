#!/bin/bash

# Кладёт на gazziiii777 то, чего CI не видит: compose.yaml и .env. Сам образ собирает
# GitHub Actions и публикует в ghcr.io, сервер его только тянет (см. .github/workflows/ci.yml).
# Запускать после изменения .env или compose.yaml, затем docker compose up -d на сервере
# (или дождаться следующего пуша в main). Серверный .env собирается из локального: адрес
# мобильного прокси становится прямым, на мак он идёт через ssh-туннель (см. README, «Прокси»).

set -euo pipefail

SERVER="gazziiii777@77.37.238.72"
SSH_PORT=2216
REMOTE_PATH="/home/gazziiii777/apps/cian-watch"
LOCAL_PATH="$(cd "$(dirname "$0")" && pwd)"
SERVER_PROXY="socks5://185.234.59.17:20624"

SSH="ssh -p $SSH_PORT $SERVER"

$SSH "mkdir -p $REMOTE_PATH"
scp -P "$SSH_PORT" "$LOCAL_PATH/compose.yaml" "$SERVER:$REMOTE_PATH/compose.yaml"
sed -e "s#^CIAN_PROXY_SERVER=.*#CIAN_PROXY_SERVER=$SERVER_PROXY#" -e '/^CIAN_HEADLESS=/d' "$LOCAL_PATH/.env" | $SSH "cat > $REMOTE_PATH/.env && chmod 600 $REMOTE_PATH/.env"

echo "✅ compose.yaml и .env на сервере. Применить: $SSH 'cd $REMOTE_PATH && docker compose up -d'"
