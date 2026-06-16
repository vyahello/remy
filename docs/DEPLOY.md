# Deploying the bot to a VPS

Goal: the bot runs 24/7 on a server, restarts itself, and every push to
`main` that passes CI lands on it automatically. Total hands-on time on
a fresh box: ~10 minutes.

## 1. Pick a box

- **4 vCPU / 8 GB RAM** recommended — x265 on 1080p60 is the load;
  fewer cores work but renders take proportionally longer. Renders are
  queued one at a time (the bot enforces this), so burstable instances
  are fine.
- ~40 GB disk (the workdir self-cleans on approve, but originals are
  ~250 MB each while a session is open).
- Debian 12+ or Ubuntu 22.04+.

> **Already running the old `tokcut` build?** Don't re-bootstrap — run the
> one-time in-place migration instead, then arm `REMY_DEPLOY` (step 4):
> ```bash
> sudo bash /opt/tokcut/deploy/migrate-to-remy.sh
> ```

## 2. Bootstrap the server

```bash
ssh root@your-vps
git clone https://github.com/vyahello/remy.git /opt/remy
sudo bash /opt/remy/deploy/bootstrap.sh
# …or run the bot under an existing account instead of a dedicated one:
sudo REMY_USER=youruser bash /opt/remy/deploy/bootstrap.sh
```

The script is idempotent and sets up: packages (ffmpeg, fonts, Python,
Docker — distro docker is skipped when Docker CE is already present),
the service user (created if missing; `REMY_USER` to use your own),
the venv, the Claude Code CLI, the local Bot API server
(systemd-wrapped docker compose), the `remy-bot` systemd service
(rendered for the chosen user), and a sudoers rule that lets CI restart
the service — nothing else.

## 3. Fill in the secrets

```bash
sudo nano /etc/remy/env        # template: deploy/env.example
```

| Variable | Where it comes from |
|---|---|
| `TELEGRAM_BOT_TOKEN` | @BotFather (same bot you use now) |
| `REMY_ALLOWED_USER_ID` | your numeric id (@userinfobot) |
| `CLAUDE_CODE_OAUTH_TOKEN` | run **`claude setup-token`** on your own machine, paste the result — never log in interactively on the server |
| `TELEGRAM_API_ID` / `HASH` | https://my.telegram.org (same pair as local) |

Then:

```bash
sudo systemctl start remy-botapi remy-bot
journalctl -u remy-bot -f      # expect: api=local Bot API (…, ≤2 GB)
```

> ⚠️ **Stop the bot on your laptop first.** Telegram allows one
> `getUpdates` consumer per bot token — two instances fight (409
> Conflict). One token, one bot.

## 4. Arm the CI deploy

In the GitHub repo settings:

- **Secrets** (Actions → Secrets): `VPS_HOST`, `VPS_USER` (the service
  user), `VPS_SSH_KEY` (a dedicated private key; put its `.pub` in the
  service user's `~/.ssh/authorized_keys` on the VPS), and `VPS_APP_DIR`
  (the deploy checkout path, e.g. `/opt/remy`) — kept as a secret so the
  server layout never appears in the workflow file or the build logs.
- **Variable** (Actions → Variables): `REMY_DEPLOY` = `enabled`.

From then on every push to `main` that passes ruff + mypy + pytest is
pulled onto the VPS and the service restarts. Flip the variable to
anything else to pause deploys (e.g. while testing locally).

## 5. Day-2 operations

```bash
journalctl -u remy-bot -f                  # live logs
systemctl restart remy-bot                 # manual restart
docker logs remy-bot-api --tail 50         # Bot API server logs
du -sh /home/remy/.remy/work             # workdir size (self-cleans)
```

- **Claude token expiry**: `claude setup-token` tokens are long-lived
  but not eternal — if captions/reviews stop while edits still work,
  re-run `claude setup-token` locally and update `/etc/remy/env`.
- **Bot API server data**: lives in `/var/lib/telegram-bot-api`; safe
  to wipe while the services are stopped.
- The bot is allow-listed to one Telegram user id; the Bot API port
  binds to loopback only. There is nothing else listening.
