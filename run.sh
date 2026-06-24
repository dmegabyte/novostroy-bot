#!/usr/bin/env bash
set -euo pipefail

if [ ! -f .env ]; then
    echo "❌ Нет .env файла. Сделайте cp .env.example .env и заполните."
    exit 1
fi

set -a
source .env
set +a

echo "🚀 Запуск бота..."
python -m src.bot
