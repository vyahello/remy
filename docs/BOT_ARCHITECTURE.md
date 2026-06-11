# Design proposal: Claude-orchestrated Telegram editing bot

Status: **proposal / analysis** — not built yet. This documents the target
architecture for the "film → Telegram → Claude edits → approve/redo" loop.

## The one idea that makes this work

**Claude directs and critiques; `tokcut` + ffmpeg execute.**

`tokcut` already does the deterministic, expensive work — motion scoring,
cut detection, speed-ramps, caption rendering, encoding — fast, free, and
reproducibly. Claude is slower and costs tokens per call, so it must not
re-derive any of that. Claude's job is the part code can't do well:

| Claude (judgment) | Deterministic code (`tokcut`) |
|---|---|
| Read frames → understand what's happening → **write the caption** | Motion analysis, tier classification, cut points |
| Run `check_caption` reasoning + pick safe wording | Speed solving to hit `--target` |
| Pick the **hook** moment (most striking frame) | Caption rendering + saliency placement |
| **Review the rendered output** and fix mistakes | 10-bit HEVC encode, HLG color |
| Turn NL "redo" feedback → concrete param changes | The hard `check_caption` eligibility gate |

This division is what keeps it cheap and reliable. Claude orchestrates a
deterministic tool; it doesn't hand-edit pixels.

## Surface: Claude API + tool use (self-hosted on the VPS)

Two Anthropic surfaces exist: **Claude API + tool use** (you host the
compute and define the tools) and **Managed Agents** (Anthropic hosts a
container where tools run). For this pipeline the answer is clearly the
former, because:

- The source clips are large (a 95 s iPhone HEVC clip is ~250 MB) and
  `ffmpeg`/`x265` must run where the file lives — your VPS — not inside
  Anthropic's sandbox.
- The tool surface is small and well-defined (run `tokcut`, extract
  frames, send to Telegram).

So: a Python service on the VPS holds the Telegram connection **and** the
Anthropic client, exposes a few tools, and runs the agent loop (SDK tool
runner, or a manual loop for human-in-the-loop gating). Default model
`claude-opus-4-8` with adaptive thinking.

### Tool surface Claude gets

Constrained, typed tools — **not** raw bash — so the harness validates
every action:

| Tool | Does | Returns |
|---|---|---|
| `analyze_plan(target)` | `tokcut --dry-run` | edit decision list (JSON) |
| `extract_frames(timestamps[])` | ffmpeg keyframes | images for Claude to view |
| `render(caption, target, caption_pos, music, keep_audio)` | `tokcut` | output path |
| `inspect_output()` | frames from the *rendered* file | images — for self-review |
| `send_to_telegram(file, message)` | deliver as **document** | delivery status |

`render` validates its args (clamps target, runs `check_caption` as a hard
gate) regardless of what Claude proposes — Claude proposes, code disposes.

## The loop

```
1. You upload a clip to the bot (as a file/document, not video).
2. Bot starts a Claude conversation:
     system = "You are a TikTok editor. Produce a post-worthy vertical clip.
               Use the tools. Verify your own output before sending."
3. Claude: extract_frames + analyze_plan → reason → decide caption + target
   → render → inspect_output → critique → maybe re-render (bounded) →
   send_to_telegram.
4. You tap [✅ Approve] or [🔁 Redo].
5. Redo text is appended to the SAME conversation (multi-turn), so Claude
   has full context of what it did and why → it adjusts params → re-renders.
```

The "review your own output" step (`inspect_output` → critique) is what
"make it more reliable" actually means: Claude looks at the rendered frames
and catches a chopped reveal or a mis-worded caption *before* you ever see
it. Bound re-renders (e.g. max 2) to cap latency/cost.

## Auth: Claude Code subscription OAuth (decided)

**Decision: use the Claude subscription via OAuth**, not a metered API key.
Generate a long-lived token with `claude setup-token` and put it in the
bot's environment as `CLAUDE_CODE_OAUTH_TOKEN`. The judgment work runs
through **Claude Code headless** (`claude -p …`) or the Claude Agent SDK,
both of which pick up that token — so caption/review costs draw from the
Max-plan quota instead of a per-token bill.

Division of labor (the user's rule): **everything Python can do, Python
does** — Telegram I/O, allow-list, downloads, running `tokcut`, frame
extraction. **Everything else goes to Claude Code** — reading frames to
write the caption, reviewing the rendered output, and turning redo feedback
into parameter changes.

Notes / caveats to keep in mind:
- Interactive OAuth login targets dev machines; on the VPS use the
  `setup-token` long-lived token in the service environment (systemd
  `EnvironmentFile`, not the repo).
- Subscription usage has rate/usage limits rather than per-call billing —
  fine at this volume (a few calls per video), but back off and retry on
  limit responses.
- If we ever outgrow the subscription, switching to an API key is a config
  change, not a rewrite.

## Reliability & safety

- **Allow-list your Telegram user ID** — the bot is private.
- **Constrained tools, not bash** — Claude can only run `tokcut` with
  validated arguments; it can't run arbitrary commands on the VPS.
- **`check_caption` stays a hard gate** in `render`, independent of Claude.
- **Secrets in env / a secrets manager** on the VPS, never in the repo.
- **Send/receive as `document`** — `video` uploads get re-compressed by
  Telegram. Files >50 MB need a local Bot API server (see IDEAS.md).
- **Bounded self-review** — cap re-renders so a critique loop can't run
  away on cost or time.

## Honest limitations (don't oversell)

- **Claude can't watch video — only frames.** ffmpeg decodes keyframes to
  images; Claude reasons over those. So the deterministic motion analysis
  stays the source of truth for *where* to cut; Claude judges *whether* the
  plan looks right and owns caption/hook decisions. Don't expect Claude to
  pick frame-exact cut points by "watching."
- **Latency is real** — x265 encodes take minutes; plus Claude round-trips.
  The async approve/redo design already absorbs this, but it's not instant.
- **Non-deterministic wording** — Claude may phrase the caption differently
  run to run. Fine here; pin with low effort if you want stability.

## Build order

1. ✅ **Bot skeleton** — `python-telegram-bot`, allow-list, receive
   document, reply with the dry-run edit plan. (`tokcut/bot/`, see `BOT.md`.)
2. ✅ **Full round-trip** — `cli.edit()` is the reusable pipeline core;
   the bot runs it in a worker thread behind a render lock (sequential —
   OOM lesson), streams progress into a status message, and sends the
   finished clip back as a document. Caption = message caption, else
   filename stem.
3. ✅ **Claude Code judgment** (`tokcut/judge.py`) — headless `claude -p`
   on the subscription token. Python extracts sampled frames; Claude
   watches them, identifies the subject, and writes the caption (validated
   against `check_caption`, with alternatives as fallback); after the
   render Claude reviews the output (hook frame included in the samples,
   verdicts constrained to fixable problems). Bot uses Claude when no
   caption is given; `TOKCUT_CLAUDE=off` disables.
4. ✅ **Approve/redo loop** — every delivered render carries
   [✅ Approve] [🔁 Redo] buttons. Redo feedback in free text goes to
   Claude (`judge.interpret_feedback`), which maps it onto editor
   settings; `session.validate_updates` is the hard gate (whitelist +
   clamps) before anything touches the render. Sessions keep history and
   past captions, renders are versioned (`_r1`, `_r2`, …), and
   "different caption" regenerates via Claude avoiding rejected ones.
   Keyword fallback (shorter/longer) when Claude is unavailable.
5. ✅ **Local Bot API server for >50 MB files** — `TOKCUT_BOT_API_URL`
   flips the bot into PTB `local_mode` against a self-hosted
   `telegram-bot-api` (compose file: `docker-compose.botapi.yml`), lifting
   the up/download cap from 50 MB to 2 GB so full iPhone clips go through.
   Downloads resolve to shared local paths; the bot logs the active
   endpoint at startup. Cloud API stays the default when the var is unset.
6. ✅ **Beat-aligned music cuts** (`analysis.beat_align`) — the
   synthesized track's beat grid is exact (fixed bpm, beat at t=0), so
   when music is on, segment boundaries are nudged so every cut lands on
   a beat in output time, and the video ends on one. Applies in
   `cli.edit()` whenever `--music` synthesizes the track (CLI and bot);
   user-supplied music files skip it (unknown bpm).
7. ✅ **VPS deploy kit** — `deploy/bootstrap.sh` (idempotent server
   setup: packages, service user, venv, Claude CLI, Bot API container,
   systemd units, CI sudoers rule), `deploy/tokcut-bot.service`,
   `deploy/env.example`, and the CI deploy job armed by the
   `TOKCUT_DEPLOY=enabled` repo variable + `VPS_*` secrets. Runbook:
   `docs/DEPLOY.md`.
