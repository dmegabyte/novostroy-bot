#!/usr/bin/env bash
set -euo pipefail

BASE=/home/neiro/rescue-bot
ENV_FILE="$BASE/rescue-bot.env"
SUDOERS_SRC="$BASE/rescue-bot.sudoers"
SUDOERS_DST=/etc/sudoers.d/rescue-bot

echo "Telegram rescue-bot setup"
echo "Important: use a separate Telegram bot token, not the main nmbot token."
printf "RESCUE_BOT_TOKEN: "
read -rs RESCUE_BOT_TOKEN
printf "\nRESCUE_ALLOWED_USER_IDS, comma-separated numeric Telegram IDs: "
read -r RESCUE_ALLOWED_USER_IDS

umask 077
cat > "$ENV_FILE" <<EOF
RESCUE_BOT_TOKEN=$RESCUE_BOT_TOKEN
RESCUE_TELEGRAM_API_BASE_URL=https://telegram-bot-proxy.d-megabyte.workers.dev/bot
RESCUE_ALLOWED_USER_IDS=$RESCUE_ALLOWED_USER_IDS
RESCUE_PUBLIC_IP=193.107.155.236
RESCUE_OPENCODE_API_URL=http://127.0.0.1:4096
RESCUE_OPENCODE_AGENT=chati
RESCUE_OPENCODE_MODEL=opencode/deepseek-v4-flash-free
RESCUE_OPENCODE_PUBLIC_URL=http://193.107.155.236:4097
RESCUE_OPENCODE_WEBCONSOLE_URL=http://193.107.155.236:8443
RESCUE_OPENCODE_IPGATE_URL=http://193.107.155.236:8445
RESCUE_WG_CONF=/home/neiro/wg0.conf
RESCUE_LOG_DIR=/home/neiro/rescue-bot/logs
EOF
chmod 600 "$ENV_FILE"
echo "Wrote $ENV_FILE"

echo "Installing sudoers whitelist for exact rescue commands..."
sudo install -o root -g root -m 0440 "$SUDOERS_SRC" "$SUDOERS_DST"
sudo visudo -cf "$SUDOERS_DST"

systemctl --user daemon-reload
systemctl --user enable --now rescue-bot.service
sleep 3
systemctl --user status rescue-bot.service --no-pager
echo "Done. Send /start or /status to the rescue bot from the allowed Telegram account."
