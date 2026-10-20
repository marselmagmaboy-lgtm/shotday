#!/usr/bin/env bash
# Установка/обновление ShotDay на сервере. Запуск из ~/shotday/server: bash deploy/install.sh
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
[ -f .env ] || { cp .env.example .env; chmod 600 .env; echo "Заполни server/.env и запусти снова"; exit 1; }
chmod 600 .env
sudo cp deploy/shotday-bot.service deploy/shotday-scanner.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now shotday-bot
sudo systemctl restart shotday-bot
if [ -f "$(grep ^TG_SESSION= .env | cut -d= -f2).session" ]; then
  sudo systemctl enable --now shotday-scanner
  sudo systemctl restart shotday-scanner
else
  echo "Сканер не запущен: сначала .venv/bin/python -m shotday.scanner_login"
fi
systemctl --no-pager status shotday-bot shotday-scanner | grep -E "●|Active"
