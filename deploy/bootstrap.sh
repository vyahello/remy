#!/usr/bin/env bash
# tokcut VPS bootstrap — idempotent; run as root on a fresh Debian/Ubuntu
# box (Debian 12+ / Ubuntu 22.04+):
#
#   sudo bash deploy/bootstrap.sh
#
# Sets up: system packages, the tokcut service user, the repo in
# /opt/tokcut with a venv, the Claude Code CLI, the local Telegram Bot
# API container, the systemd service, and the sudoers rule the CI deploy
# uses to restart the service. After it finishes:
#
#   1. sudo nano /etc/tokcut/env       # fill in tokens (see env.example)
#   2. sudo systemctl start tokcut-botapi tokcut-bot
#
# Full runbook: docs/DEPLOY.md
set -euo pipefail

REPO="${TOKCUT_REPO:-https://github.com/vyahello/tokcut.git}"
APP_DIR=/opt/tokcut
# the user the bot runs as — defaults to a dedicated service account;
# set TOKCUT_USER=youruser to run under an existing account instead:
#   sudo TOKCUT_USER=cax bash deploy/bootstrap.sh
SVC_USER="${TOKCUT_USER:-tokcut}"

[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo)"; exit 1; }

echo "==> system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git curl rsync ffmpeg fonts-dejavu \
    fonts-noto-color-emoji python3 python3-venv python3-pip acl

# docker: only install the distro packages when docker is absent —
# boxes with Docker CE (docker.com) conflict with Ubuntu's docker.io
if ! command -v docker &>/dev/null; then
    apt-get install -y -qq docker.io
fi
if ! docker compose version &>/dev/null; then
    apt-get install -y -qq docker-compose-v2
fi

echo "==> service user + dirs"
id "$SVC_USER" &>/dev/null || useradd -m -s /bin/bash "$SVC_USER"
mkdir -p /etc/tokcut /var/lib/telegram-bot-api
chmod 755 /var/lib/telegram-bot-api
# the Bot API container writes downloads as its own (container) user
# with group-only perms — grant the bot user read/traverse, and make it
# the default so future files inherit it (else downloads 404)
setfacl -R -m "u:$SVC_USER:rX" /var/lib/telegram-bot-api
setfacl -R -d -m "u:$SVC_USER:rX" /var/lib/telegram-bot-api

echo "==> repo at $APP_DIR"
if [ -d "$APP_DIR/.git" ]; then
    git -C "$APP_DIR" pull --ff-only
else
    git clone "$REPO" "$APP_DIR"
fi
chown -R "$SVC_USER:$SVC_USER" "$APP_DIR"

echo "==> python venv"
sudo -u "$SVC_USER" bash -c "
    cd $APP_DIR
    [ -d venv ] || python3 -m venv venv
    venv/bin/pip install -q --upgrade pip
    venv/bin/pip install -q -e '.[bot]'
"

echo "==> Claude Code CLI (for the judgment layer)"
if ! sudo -u "$SVC_USER" bash -lc 'command -v claude' &>/dev/null; then
    sudo -u "$SVC_USER" bash -c 'curl -fsSL https://claude.ai/install.sh | bash'
fi
# the service PATH must reach it
ln -sf "/home/$SVC_USER/.local/bin/claude" /usr/local/bin/claude 2>/dev/null || true

echo "==> env file"
if [ ! -f /etc/tokcut/env ]; then
    sed "s|/home/tokcut|/home/$SVC_USER|g" \
        "$APP_DIR/deploy/env.example" > /etc/tokcut/env
    chmod 600 /etc/tokcut/env && chown root:root /etc/tokcut/env
    echo "    !!! fill in /etc/tokcut/env before starting the bot"
fi

echo "==> local Bot API server (systemd-wrapped docker compose)"
cat > /etc/systemd/system/tokcut-botapi.service <<'UNIT'
[Unit]
Description=Local Telegram Bot API server for tokcut
After=docker.service network-online.target
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=true
EnvironmentFile=/etc/tokcut/env
WorkingDirectory=/opt/tokcut
ExecStart=/usr/bin/docker compose -f docker-compose.botapi.yml up -d
ExecStop=/usr/bin/docker compose -f docker-compose.botapi.yml down

[Install]
WantedBy=multi-user.target
UNIT

echo "==> bot service (rendered for user $SVC_USER)"
sed "s/^User=tokcut/User=$SVC_USER/; s/^Group=tokcut/Group=$SVC_USER/; \
     s|/home/tokcut|/home/$SVC_USER|g" \
    "$APP_DIR/deploy/tokcut-bot.service" \
    > /etc/systemd/system/tokcut-bot.service
chmod 644 /etc/systemd/system/tokcut-bot.service

echo "==> sudoers rule for CI deploys (restart only)"
cat > /etc/sudoers.d/tokcut-deploy <<SUDO
$SVC_USER ALL=(root) NOPASSWD: /usr/bin/systemctl restart tokcut-bot
SUDO
chmod 440 /etc/sudoers.d/tokcut-deploy

systemctl daemon-reload
systemctl enable tokcut-botapi tokcut-bot

echo
echo "Bootstrap done. Next:"
echo "  1. sudo nano /etc/tokcut/env   (tokens — see deploy/env.example)"
echo "  2. sudo systemctl start tokcut-botapi tokcut-bot"
echo "  3. journalctl -u tokcut-bot -f   (watch it come up)"
