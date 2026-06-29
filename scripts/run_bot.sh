#!/bin/bash
# Запуск nmbot-бота в фоне с логом в logs/bot.log
set -a
source "$(dirname "$0")/../.env"
set +a
cd "$(dirname "$0")/.."
exec python3 scripts/chat_tester_bot.py
