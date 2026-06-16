# Running the Telegram bot

Status: **step 5** — the full loop on a local Bot API server. Send a clip,
Claude proposes captions, you pick the caption and options in a setup
screen and tap **🎬 Render**, the bot renders the 1080x1920 edit, Claude
writes paste-ready TikTok post copy (an actionable blurb — what it teaches
+ how to use it — plus relevant hashtags), and
the file arrives with that copy and **[✅ Approve] [🔁 Redo]** buttons. Tap Redo and say what to change in your own words — Claude maps it
onto the editor's settings and a new revision is rendered. With the optional
local Bot API server, full ~250 MB iPhone clips go through (the cloud API
caps at 50 MB).

## How a clip flows

1. You send a video **as a file**. **Claude watches sampled frames and
   proposes captions** (subject + ideas are messaged to you). Nothing
   renders yet.
2. A **setup screen** appears. You pick the caption — tap an idea, *type
   your own*, or **🚫 No caption** — and flip the options you want before
   rendering: **cold open** (off by default), zoom, look, music. Length is
   auto (a TikTok-friendly target solved from the content); tune it with
   ⚡/🐢 in the redo loop after you've seen a take.
   Defaults are **no cold open and no caption**. Tap **🎬 Render** to start.
   A message caption sent with the file pre-selects it. **Landscape clips**
   (laptop/OBS recordings) bake no caption — native resolution so they go
   fullscreen in TikTok; Claude sends caption ideas to copy instead.
3. The render queues (one at a time — parallel encodes can OOM the box)
   and a status message live-updates with the edit plan and progress.
4. **Claude writes the TikTok post copy** from the rendered frames — a
   precise, actionable description — what the video teaches and how a
   viewer can use it — plus the 5 most relevant hashtags, grounded only in what
   the video shows (no invented features) and moderation-filtered. It's
   attached to the reply, ready to paste into the TikTok caption box.
5. The finished `.mp4` comes back as a **document** (no recompression)
   with the post copy and [✅ Approve] [🔁 Redo] buttons.
6. **Redo**: a quick-tap keyboard (two buttons per row, so nothing is
   clipped on iPhone) covers the common tweaks — ⚡ shorter, 🐢 longer,
   🔎 tighter / 🔭 wider framing, 🪝 cold open on/off, 🔍 zoom on/off,
   ✨ look on/off, 🥁 phonk / 🎹 synthwave, 🔥 faster / 🧊 slower beat,
   🎲 new mix, 🔇 no music, plus ✍️ new caption and 🎨 next style on
   vertical clips. Buttons apply
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
`CLAUDE_CODE_OAUTH_TOKEN` in the bot's environment. Set `REMY_CLAUDE=off`
to disable the judgment layer entirely (filename captions, no post copy).

## Setup

```bash
venv/bin/pip install -e ".[bot]"     # installs python-telegram-bot
cp .env.example .env                 # then fill in the values
```

Get the two required values:
- **`TELEGRAM_BOT_TOKEN`** — create a bot via [@BotFather](https://t.me/BotFather).
- **`REMY_ALLOWED_USER_ID`** — your numeric Telegram id from
  [@userinfobot](https://t.me/userinfobot). The bot only answers this user.

## Run

```bash
set -a; . ./.env; set +a      # load .env into the environment
venv/bin/remy-bot           # or: venv/bin/python3 -m remy.bot.app
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
   echo 'REMY_BOT_API_URL=http://127.0.0.1:8081' >> .env
   set -a; . ./.env; set +a
   venv/bin/remy-bot
   ```

On startup the bot logs which endpoint it's using (`api=local Bot API …` vs
`api=cloud Bot API (≤50 MB)`). In local mode downloads resolve to local file
paths instead of an HTTP copy, so even large clips land instantly.

## What runs where

- **Python** (this code): Telegram I/O, allow-list, downloads, running
  `remy` — everything deterministic.
- **Claude Code** (subscription OAuth): caption wording, the paste-ready
  TikTok post copy, and the approve/redo conversation. Set
  `CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`) now so it's ready.
