<div align="center">

<img src="remybot.png" alt="remy logo" width="200">

# ✂️🎬 remy

### raw phone clip in → scroll-stopping TikTok out

*Shoot a long, messy clip, let `remy` cut the boring bits, slap on a clean
caption, and drop a beat underneath — ready to upload.*

[![CI](https://github.com/vyahello/remy/actions/workflows/ci.yml/badge.svg)](https://github.com/vyahello/remy/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%20|%203.12%20|%203.13-blue?logo=python&logoColor=white)](https://www.python.org)
[![Tests](https://img.shields.io/badge/tests-188%20passing-brightgreen?logo=pytest&logoColor=white)](tests)
[![Lint: ruff](https://img.shields.io/badge/lint-ruff-261230?logo=ruff&logoColor=white)](https://docs.astral.sh/ruff)
[![Types: mypy](https://img.shields.io/badge/types-mypy-2a6db2?logo=python&logoColor=white)](https://mypy-lang.org)
[![ffmpeg](https://img.shields.io/badge/powered%20by-ffmpeg-007808?logo=ffmpeg&logoColor=white)](https://ffmpeg.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

</div>

---

An auto-editor that turns raw phone footage into tight, high-quality
vertical TikTok clips. 🎯 Motion analysis drives speed-ramps (boring stretches
fast-forwarded ⏩, action kept real-time ▶️), a persistent styled caption is
auto-placed where it won't cover the action 🏷️, and the export is **muted by
default** 🔇 so you add a trending TikTok sound in-app (optional synthesized
music if you want it baked in).

Works for any talking-to-camera, screen-recording, tutorial, vlog, or
process video where there's dead time to trim and a moment worth keeping. 🎬

## ✨ What it does for you

| | |
|---|---|
| ⏩ **Kills dead air** | Fast-forwards the parts where you're just reading docs; keeps the payoff at 1x |
| 🏷️ **Smart captions** | Purple-on-white sticker text, auto-placed over the calmest part of the frame so it never hides your screen |
| 🛡️ **Won't get you flagged** | Warns about wording TikTok's moderation tends to penalize before you post |
| 🔇 **Ready for in-app sound** | Exports silent by default so you tap a trending TikTok sound — or bake in royalty-free music with `--music` |
| 📱 **Phone-grade quality** | 1080×1920, 10-bit HEVC, iPhone HLG color preserved — survives TikTok's re-encode |
| 🖥️ **Landscape-aware** | Laptop/OBS recordings keep native resolution (so they can go fullscreen), recorder-UI edges auto-trimmed, no baked caption |

## 📦 Install

```bash
python3 -m venv venv
venv/bin/pip install -e ".[dev]"   # needs ffmpeg + DejaVu/Noto fonts on the system
```

System requirements: `ffmpeg`/`ffprobe` with libx265, fonts
`fonts-dejavu` + `fonts-noto-color-emoji`.

## 🚀 Use

```bash
# preview the cut plan (instant, no encode) — length is solved
# automatically (TikTok completion-rate sweet spot, action never sped up)
remy clip.MOV -c "How I set this up ⚡" --dry-run

# render — muted by default, you add a trending sound in TikTok
remy clip.MOV -c "How I set this up ⚡"

# pin the length yourself / keep your original ambient audio
remy clip.MOV -c "How I set this up ⚡" --target 45 --keep-audio

# bake in a synthesized phonk track, cuts snapped to the beat
remy clip.MOV -c "How I set this up ⚡" --music --music-style phonk

# or your own audio file
remy clip.MOV -c "..." --music ~/tracks/mytrack.mp3
```

(`python -m remy ...` works too if you didn't `pip install`.)

> 💡 **Pro tip:** always `--dry-run` first — it prints the cut plan
> (which seconds get fast-forwarded vs kept) in a fraction of a second,
> so you can sanity-check the auto-picked length (or pin `--target N`)
> before committing to a multi-minute encode.

## ⚙️ How it works

1. **Probe** the source (dimensions/rotation/fps/duration/audio).
2. **Motion analysis** — decode tiny grayscale frames at 6 fps, score by
   mean frame-to-frame difference.
3. **Classify** the timeline into dead / lag / action tiers (adaptive
   percentile thresholds; short runs merged so cuts feel intentional).
4. **Editorial cuts** — cold-open **hook** on the strongest beat,
   hard-trim boring lead-ins/outros (open and close on action), and
   **auto-zoom** into the action when static margins waste the frame.
5. **Speeds** — action 1x, lag ≈1.7x, dead ≈3.2x; `--target N` solves the
   fast-tier speeds to land at N seconds.
6. **Caption** — Pillow renders purple bold-italic on rounded white boxes
   (emoji supported). A saliency map places it over the calmest region
   inside TikTok's UI safe zone, so it never covers the screen/device.
7. **Audio** — muted by default (silent export for in-app TikTok sound).
   `--keep-audio` retains the original ambient; `--music` bakes in a
   royalty-free synthwave/phonk track synthesized by `remy.music` (zero
   copyright risk) — and since the track's beat grid is known exactly,
   every cut is **snapped onto the beat** 🥁.
8. **Render** — one ffmpeg `filter_complex`: per-segment trim/setpts +
   atempo, concat, optional crop, lanczos scale into 1080x1920, caption
   overlay, encode **libx265 main10 crf 18** with color tags matched to
   the source (HLG/PQ for HDR, bt709 for SDR).

## 🗂️ Layout

```
remy/            package
  analysis.py        probe, motion scoring, saliency, hook/crop/trims
  caption.py         caption rendering + TikTok-eligibility checks
  layout.py          canvas layout + saliency-aware caption placement
  music.py           procedural dark-synthwave/phonk generator
  render.py          ffmpeg filtergraph + encode (source-matched color)
  cli.py             argparse CLI + reusable edit() pipeline core
  judge.py           Claude Code: caption writing + TikTok post copy
  types.py           shared TypedDicts + type aliases
  bot/               private Telegram bot (config, session, pipeline, app)
tests/             pytest suite (logic-level, no GPU/network needed)
docs/              USAGE.md, IDEAS.md, BOT.md, BOT_ARCHITECTURE.md
.github/workflows/ CI (ruff + mypy + pytest; deploy stage stubbed for VPS)
```

## 🧪 Develop

```bash
venv/bin/pytest          # run tests
venv/bin/ruff check .    # lint
venv/bin/mypy            # type-check (codebase is fully typed)
```

## 🤖 Telegram bot — meet *Remy*

📱 Phone → private Telegram bot. Send a clip as a file and **Remy** (the
bot's persona) takes it from there:

1. **Claude watches it and proposes captions.** You pick one, type your
   own, or go caption-free — and flip the options you want (cold open,
   zoom, look, music) in a setup screen. Length is auto; tap **🎬 Render**.
2. The edit comes back as a clean file with **paste-ready TikTok copy** —
   a fun blurb + the 5 most relevant hashtags, grounded only in what the
   video actually shows — plus **✅ Approve / 🔁 Redo**.
3. **Redo in plain words** ("shorter", "move the caption on my hand", "add
   phonk music"). Claude maps it to settings — and a placement request just
   moves the caption, it never rewrites it.

Claude Code runs on your subscription OAuth — see [`docs/BOT.md`](docs/BOT.md)
to run it and [`docs/BOT_ARCHITECTURE.md`](docs/BOT_ARCHITECTURE.md) for the
design.

```bash
pip install -e ".[bot]"          # adds python-telegram-bot
cp .env.example .env             # fill in token + your Telegram id
remy-bot
```

For full-length clips over Telegram's 50 MB cap, run a local Bot API server
(`docker compose -f docker-compose.botapi.yml up -d`) and set
`REMY_BOT_API_URL` — it lifts the limit to 2 GB. See [`docs/BOT.md`](docs/BOT.md).

## 🗺️ Roadmap

The pipeline is feature-complete; what's left is running it somewhere:
[`docs/DEPLOY.md`](docs/DEPLOY.md) walks a fresh VPS to a 24/7 bot in
~10 minutes (`deploy/bootstrap.sh` + systemd + auto-deploy from CI).
See [`docs/IDEAS.md`](docs/IDEAS.md) for the content playbook.

---

<div align="center">

Built for creators who'd rather film than edit. 🖤 Licensed MIT — take it and remix it.

</div>
