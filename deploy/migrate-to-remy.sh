#!/usr/bin/env bash
# One-time, idempotent migration of an already-running *tokcut* VPS to the
# *remy* names. Transforms the installed systemd units, directories, env
# file and sudoers rule in place — no apt/docker reinstall. Safe to re-run.
#
#   sudo bash /opt/tokcut/deploy/migrate-to-remy.sh    # first run
#   sudo bash /opt/remy/deploy/migrate-to-remy.sh      # re-run (already moved)
#
# After it finishes, do the GitHub side by hand: set the repo variable
# REMY_DEPLOY=enabled (and drop the old TOKCUT_DEPLOY) so CI deploys resume.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo)"; exit 1; }

SD=/etc/systemd/system
OLD_BOT="$SD/tokcut-bot.service"
# the service user is whatever the existing unit runs as (a dedicated
# account or your own login); override with REMY_USER=… if needed
SVC_USER="${REMY_USER:-$([ -f "$OLD_BOT" ] && awk -F= '/^User=/{print $2; exit}' "$OLD_BOT")}"
SVC_USER="${SVC_USER:-tokcut}"
HOME_DIR="$(getent passwd "$SVC_USER" | cut -d: -f6)"
echo "==> migrating for service user: $SVC_USER (home: $HOME_DIR)"

echo "==> stopping legacy services + freeing the Bot API port"
systemctl stop tokcut-bot tokcut-botapi tokcut-gc.timer tokcut-gc \
    2>/dev/null || true
# the new compose names the container remy-bot-api; the old one still holds
# 127.0.0.1:8081, so drop it before the new one comes up
docker rm -f tokcut-bot-api 2>/dev/null || true

echo "==> moving directories (skipped if already moved)"
[ -d /opt/tokcut ] && [ ! -e /opt/remy ] && mv /opt/tokcut /opt/remy || true
[ -d /etc/tokcut ] && [ ! -e /etc/remy ] && mv /etc/tokcut /etc/remy || true
if [ -n "$HOME_DIR" ] && [ -d "$HOME_DIR/.tokcut" ] \
        && [ ! -e "$HOME_DIR/.remy" ]; then
    mv "$HOME_DIR/.tokcut" "$HOME_DIR/.remy"
fi

echo "==> rewriting env file (var names + paths)"
if [ -f /etc/remy/env ]; then
    sed -i 's/^TOKCUT_/REMY_/; s#/\.tokcut/#/.remy/#g; s#/opt/tokcut#/opt/remy#g' \
        /etc/remy/env
fi

echo "==> transforming installed systemd units in place"
for base in bot botapi gc; do
    for ext in service timer; do
        src="$SD/tokcut-$base.$ext"
        [ -f "$src" ] || continue
        dst="$SD/remy-$base.$ext"
        sed 's/TOKCUT_/REMY_/g; s/tokcut/remy/g; s/Tokcut/Remy/g' \
            "$src" > "$dst"
        rm -f "$src"
        echo "    $(basename "$src") -> $(basename "$dst")"
    done
done

echo "==> sudoers rule for CI restarts"
rm -f /etc/sudoers.d/tokcut-deploy
cat > /etc/sudoers.d/remy-deploy <<SUDO
$SVC_USER ALL=(root) NOPASSWD: /usr/bin/systemctl restart remy-bot
SUDO
chmod 440 /etc/sudoers.d/remy-deploy

echo "==> pulling latest code + reinstalling the venv as $SVC_USER"
# guarantees /opt/remy holds the renamed package so the remy-bot console
# script exists for the new unit's ExecStart
sudo -u "$SVC_USER" bash -c "
    cd /opt/remy
    git fetch origin main && git reset --hard origin/main
    venv/bin/pip install -q -e '.[bot]'
    venv/bin/pip install -q --no-deps tinysoundfont
"

echo "==> reload, enable and start the remy services"
systemctl daemon-reload
systemctl enable remy-botapi remy-bot remy-gc.timer 2>/dev/null || true
systemctl start remy-botapi
systemctl start remy-bot
systemctl --no-pager --lines=8 status remy-bot || true

echo
echo "Box migrated to the remy names. Last step (GitHub, manual):"
echo "  Actions → Variables: add REMY_DEPLOY=enabled, remove TOKCUT_DEPLOY."
