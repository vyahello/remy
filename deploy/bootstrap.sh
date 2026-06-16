#!/usr/bin/env bash
# remy VPS bootstrap — idempotent; run as root on a fresh Debian/Ubuntu
# box (Debian 12+ / Ubuntu 22.04+):
#
#   sudo bash deploy/bootstrap.sh
#
# Sets up: system packages, the remy service user, the repo in
# /opt/remy with a venv, the Claude Code CLI, the local Telegram Bot
# API container, the systemd service, and the sudoers rule the CI deploy
# uses to restart the service. After it finishes:
#
#   1. sudo nano /etc/remy/env       # fill in tokens (see env.example)
#   2. sudo systemctl start remy-botapi remy-bot
#
# Full runbook: docs/DEPLOY.md
set -euo pipefail

REPO="${REMY_REPO:-https://github.com/vyahello/remy.git}"
APP_DIR=/opt/remy
# the user the bot runs as — defaults to a dedicated service account;
# set REMY_USER=youruser to run under an existing account instead:
#   sudo REMY_USER=cax bash deploy/bootstrap.sh
SVC_USER="${REMY_USER:-remy}"

[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo)"; exit 1; }

echo "==> system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git curl rsync ffmpeg fonts-dejavu \
    fonts-noto-color-emoji python3 python3-venv python3-pip acl
# FluidR3 (~140 MB) is a far better GM soundfont than the tiny default —
# the music generator auto-prefers it (music.find_soundfont). Best-effort.
apt-get install -y -qq fluid-soundfont-gm || \
    echo "  (fluid-soundfont-gm unavailable; using existing soundfont)"

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
mkdir -p /etc/remy /var/lib/telegram-bot-api
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
    # SoundFont music instruments (--no-deps: skip pyaudio,
    # which is live-playback only and needs system portaudio)
    venv/bin/pip install -q --no-deps tinysoundfont
"

echo "==> Claude Code CLI (for the judgment layer)"
if ! sudo -u "$SVC_USER" bash -lc 'command -v claude' &>/dev/null; then
    sudo -u "$SVC_USER" bash -c 'curl -fsSL https://claude.ai/install.sh | bash'
fi
# the service PATH must reach it
ln -sf "/home/$SVC_USER/.local/bin/claude" /usr/local/bin/claude 2>/dev/null || true

echo "==> env file"
if [ ! -f /etc/remy/env ]; then
    sed "s|/home/remy|/home/$SVC_USER|g" \
        "$APP_DIR/deploy/env.example" > /etc/remy/env
    chmod 600 /etc/remy/env && chown root:root /etc/remy/env
    echo "    !!! fill in /etc/remy/env before starting the bot"
fi

echo "==> local Bot API server (systemd-wrapped docker compose)"
cat > /etc/systemd/system/remy-botapi.service <<'UNIT'
[Unit]
Description=Local Telegram Bot API server for remy
After=docker.service network-online.target
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=true
EnvironmentFile=/etc/remy/env
WorkingDirectory=/opt/remy
ExecStart=/usr/bin/docker compose -f docker-compose.botapi.yml up -d
ExecStop=/usr/bin/docker compose -f docker-compose.botapi.yml down

[Install]
WantedBy=multi-user.target
UNIT

echo "==> daily media GC (Bot API server cache + stale workdir files)"
cat > /etc/systemd/system/remy-gc.service <<UNIT
[Unit]
Description=Prune old remy media (Bot API cache, stale workdir)

[Service]
Type=oneshot
# the Bot API server re-downloads from Telegram on demand, so pruning
# its media cache is safe; binlogs at the dir root are NOT touched.
# Leading '-': the cache dirs are container-owned, so find hits
# permission-denied and exits non-zero — that must NOT abort the
# service before the workdir prune below (oneshot stops on first
# ExecStart failure otherwise).
ExecStart=-/usr/bin/find /var/lib/telegram-bot-api -mindepth 2 -type f \
    \( -path '*/videos/*' -o -path '*/documents/*' -o -path '*/photos/*' \
       -o -path '*/music/*' -o -path '*/animations/*' -o -path '*/temp/*' \) \
    -mmin +1440 -delete
# unapproved/orphaned sessions: the workdir is working space, not an
# archive — anything older than a day is abandoned (the bot also sweeps
# it on startup, so this is just the backstop for a long-lived process)
ExecStart=-/usr/bin/find /home/$SVC_USER/.remy/work -type f \
    -mmin +1440 -delete
UNIT
cat > /etc/systemd/system/remy-gc.timer <<'UNIT'
[Unit]
Description=Daily remy media GC

[Timer]
OnCalendar=daily
Persistent=true

[Install]
WantedBy=timers.target
UNIT

echo "==> bot service (rendered for user $SVC_USER)"
sed "s/^User=remy/User=$SVC_USER/; s/^Group=remy/Group=$SVC_USER/; \
     s|/home/remy|/home/$SVC_USER|g" \
    "$APP_DIR/deploy/remy-bot.service" \
    > /etc/systemd/system/remy-bot.service
chmod 644 /etc/systemd/system/remy-bot.service

echo "==> sudoers rule for CI deploys (restart only)"
cat > /etc/sudoers.d/remy-deploy <<SUDO
$SVC_USER ALL=(root) NOPASSWD: /usr/bin/systemctl restart remy-bot
SUDO
chmod 440 /etc/sudoers.d/remy-deploy

systemctl daemon-reload
systemctl enable remy-botapi remy-bot
systemctl enable --now remy-gc.timer

echo
echo "Bootstrap done. Next:"
echo "  1. sudo nano /etc/remy/env   (tokens — see deploy/env.example)"
echo "  2. sudo systemctl start remy-botapi remy-bot"
echo "  3. journalctl -u remy-bot -f   (watch it come up)"
