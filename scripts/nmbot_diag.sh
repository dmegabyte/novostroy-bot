#!/usr/bin/env bash
# nmbot_diag.sh — единая диагностика nmbot
# Точка входа: показывает статус продакшн-бота на VPS и dev-окружения
#
# Использование:
#   bash scripts/nmbot_diag.sh          # полная диагностика
#   bash scripts/nmbot_diag.sh --quick  # только статус (PID + uptime)
#   bash scripts/nmbot_diag.sh --logs   # только лог

set -euo pipefail

VPS_HOST="193.107.155.236"
VPS_PORT="1905"
VPS_USER="neiro"
VPS_BOT_DIR="/home/neiro/novostroy-bot"
SERVICE="novostroy-bot.service"

# ── helpers ──────────────────────────────────────────────────

section() {
  echo ""
  echo "══════════════════════════════════════════════════════════"
  echo "  $1"
  echo "══════════════════════════════════════════════════════════"
}

check_vps() {
  ssh -p "$VPS_PORT" -o ConnectTimeout=5 -o BatchMode=yes "$VPS_USER@$VPS_HOST" "true" 2>/dev/null
}

# ── диагностика ──────────────────────────────────────────────

diag_status() {
  section "🖥 Production (VPS — $VPS_HOST)"

  if ! check_vps; then
    echo "✗ VPS недоступен (ssh -p $VPS_PORT $VPS_USER@$VPS_HOST)"
    return
  fi

  ssh -p "$VPS_PORT" "$VPS_USER@$VPS_HOST" bash <<'SSH'
    SERVICE="novostroy-bot.service"
    BOT_DIR="/home/neiro/novostroy-bot"

    echo "Сервис: $SERVICE"
    echo "───"
    systemctl --user status "$SERVICE" --no-pager --lines 0 2>&1 | head -6
    echo ""

    PID=$(systemctl --user show "$SERVICE" -p MainPID --value 2>/dev/null)
    if [ "$PID" != "0" ] && [ -n "$PID" ]; then
      echo "PID: $PID"
      echo "Uptime: $(ps -o etime= -p "$PID" 2>/dev/null | xargs)"
      echo "Memory: $(ps -o rss= -p "$PID" 2>/dev/null | awk '{printf "%.0f MB", $1/1024}')"
      echo "CPU:    $(ps -o %cpu= -p "$PID" 2>/dev/null | xargs)%"
    else
      echo "⚠ Бот НЕ запущен (PID=0)"
    fi

    echo ""
    echo "Последний коммит:"
    cd "$BOT_DIR" && git log --oneline -1 2>/dev/null || echo "не git"

    echo ""
    echo "Рабочая директория: $BOT_DIR"
    ls -la "$BOT_DIR/src/bot.py" "$BOT_DIR/src/config.py" "$BOT_DIR/src/session.py" 2>/dev/null
SSH
}

diag_logs() {
  section "📋 Лог (последние 15 строк)"

  if ! check_vps; then
    echo "✗ VPS недоступен"
    return
  fi

  ssh -p "$VPS_PORT" "$VPS_USER@$VPS_HOST" "tail -15 $VPS_BOT_DIR/logs/bot.log 2>/dev/null || echo 'Лог не найден'"
}

diag_dev() {
  section "💻 Dev-окружение (local — $HOME/ai/projects/nmbot)"

  PROC=$(pgrep -f "chat_tester_bot.py" 2>/dev/null || true)
  if [ -n "$PROC" ]; then
    echo "Dev-бот запущен (PID: $PROC)"
  else
    echo "Dev-бот не запущен (это нормально, если не тестируете)"
  fi

  if [ -f "scripts/nmbot_deploy_smoke.py" ]; then
    echo "Deploy-smoke:"
    python3 scripts/nmbot_deploy_smoke.py 2>&1 || true
  fi

  echo ""
  echo "Скрипты: $(ls scripts/*.py scripts/*.sh 2>/dev/null | wc -l) шт"
  echo "Тесты: python3 scripts/nmbot_test_agent.py --suite deploy"
}

diag_all() {
  echo "nmbot — единая диагностика"
  echo "Дата: $(date '+%Y-%m-%d %H:%M:%S')"

  case "${1:-all}" in
    --quick|status) diag_status ;;
    --logs|logs)    diag_logs ;;
    --dev)           diag_dev ;;
    *)               diag_status; diag_logs; diag_dev ;;
  esac

  echo ""
  echo "✅ Диагностика завершена"
}

# ── запуск ───────────────────────────────────────────────────

diag_all "${1:-all}"
