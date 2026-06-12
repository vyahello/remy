# Running the Telegram bot

Status: **step 5** — the full loop on a local Bot API server. Send a clip,
Claude watches it and writes the caption, the bot renders the 1080x1920
edit, Claude reviews the result, and the file arrives with
**[✅ Approve] [🔁 Redo]** buttons. Tap Redo and say what to change in your
own words — Claude maps it onto the editor's settings and a new revision is
rendered. With the optional local Bot API server, full ~250 MB iPhone clips
go through (the cloud API caps at 50 MB).

## How a clip flows

1. You send a video **as a file**. If you add a message caption, that
   exact text is used on-video. If not, **Claude watches sampled frames
   and writes the caption itself** (subject + caption are messaged to you).
   **Landscape clips** (laptop/OBS recordings) bake in no caption: they
   keep their native resolution so they can go fullscreen in TikTok —
   cuts/speed-ups/edge-trims only. Claude still messages you **caption
   ideas to copy** into TikTok's own text tool.
2. The caption passes the eligibility check; warnings are forwarded.
3. The render queues (one at a time — parallel encodes can OOM the box)
   and a status message live-updates with the edit plan and progress.
4. **Claude reviews the rendered output** (hook, caption legibility,
   ending) and its verdict is attached to the reply.
5. The finished `.mp4` comes back as a **document** (no recompression)
   with [✅ Approve] [🔁 Redo] buttons.
6. **Redo**: a quick-tap keyboard covers the common tweaks — ⚡ shorter,
   🐢 longer, 🔎 tighter / 🔭 wider framing, 🪝 cold open on/off,
   🔍 zoom on/off, ✨ look on/off, 🥁 phonk / 🎹 synthwave / 🔇 no music,
   plus ✍️ new caption and 🎨 next style on vertical clips. Buttons apply
   instantly (no Claude round-trip). And **chat is always on**: any text
   you send while a clip is in session counts as feedback — no Redo tap
   needed. "make it more zoomed", "caption at the top", "white on black
   caption" — Claude maps it to settings (validated and clamped in
   Python). The next take arrives with the same buttons. Sessions remember
   history and rejected captions, so regenerated captions don't repeat.
7. **Approve cleans up**: tapping ✅ deletes the downloaded original and
   every rendered revision from the workdir (they already live in
   Telegram). Sending a new clip likewise clears any abandoned session,
   so the workdir doesn't fill up with ~250 MB originals.

## Claude auth (subscription OAuth)

The judgment layer runs Claude Code headless (`claude -p`). On a dev
machine an existing `claude` login is enough. On a server, generate a
long-lived token from your subscription with `claude setup-token` and set
`CLAUDE_CODE_OAUTH_TOKEN` in the bot's environment. Set `TOKCUT_CLAUDE=off`
to disable the judgment layer entirely (filename captions, no review).

## Setup

```bash
venv/bin/pip install -e ".[bot]"     # installs python-telegram-bot
cp .env.example .env                 # then fill in the values
```

Get the two required values:
- **`TELEGRAM_BOT_TOKEN`** — create a bot via [@BotFather](https://t.me/BotFather).
- **`TOKCUT_ALLOWED_USER_ID`** — your numeric Telegram id from
  [@userinfobot](https://t.me/userinfobot). The bot only answers this user.

## Run

```bash
set -a; . ./.env; set +a      # load .env into the environment
venv/bin/tokcut-bot           # or: venv/bin/python3 -m tokcut.bot.app
```

Then in Telegram: send `/start`, then send a clip. **Send it as a *file*
(document), not as a video** — Telegram re-compresses videos and would
ruin the quality. The bot edits it and sends the finished vertical clip
back as a document.

> The standard cloud Bot API caps downloads at **50 MB**. A 95 s iPhone HEVC
> clip is ~250 MB, so for full clips run a local Bot API server (next
> section) — it lifts the cap to 2 GB.

## Big clips: local Bot API server (step 5)

The cloud Bot API rejects files over 50 MB. To handle full-length clips, run
your own `telegram-bot-api` and point the bot at it. A compose file is
included.

1. Get a `TELEGRAM_API_ID` + `TELEGRAM_API_HASH` from
   [my.telegram.org](https://my.telegram.org) → **API development tools**.
   (These identify the *app*; the bot still uses its @BotFather token.)
2. Put them in `.env` (gitignored), then start the server:
   ```bash
   docker compose -f docker-compose.botapi.yml up -d
   ```
3. Point the bot at it and run on the **same host** (they share a download
   directory at an identical path — see the compose file):
   ```bash
   echo 'TOKCUT_BOT_API_URL=http://127.0.0.1:8081' >> .env
   set -a; . ./.env; set +a
   venv/bin/tokcut-bot
   ```

On startup the bot logs which endpoint it's using (`api=local Bot API …` vs
`api=cloud Bot API (≤50 MB)`). In local mode downloads resolve to local file
paths instead of an HTTP copy, so even large clips land instantly.

## What runs where

- **Python** (this code): Telegram I/O, allow-list, downloads, running
  `tokcut` — everything deterministic.
- **Claude Code** (subscription OAuth, later): caption wording, reviewing
  the rendered output, the approve/redo conversation. Set
  `CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`) now so it's ready.
