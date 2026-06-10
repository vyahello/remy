<div align="center">

# ✂️🎬 tokcut

### raw phone clip in → scroll-stopping TikTok out

*Point your phone at the screen, ramble for 90 seconds, let `tokcut` cut the
boring bits, slap on a clean caption, and drop a dark-synth beat underneath.*

[![CI](https://github.com/vyahello/tokcut/actions/workflows/ci.yml/badge.svg)](https://github.com/vyahello/tokcut/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%20|%203.12%20|%203.13-blue?logo=python&logoColor=white)](https://www.python.org)
[![Tests](https://img.shields.io/badge/tests-30%20passing-brightgreen?logo=pytest&logoColor=white)](tests)
[![Lint: ruff](https://img.shields.io/badge/lint-ruff-261230?logo=ruff&logoColor=white)](https://docs.astral.sh/ruff)
[![ffmpeg](https://img.shields.io/badge/powered%20by-ffmpeg-007808?logo=ffmpeg&logoColor=white)](https://ffmpeg.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

</div>

---

Personal auto-editor that turns raw phone footage into tight, high-quality
vertical TikTok clips. 🎯 Motion analysis drives speed-ramps (boring stretches
fast-forwarded ⏩, action kept real-time ▶️), a persistent styled caption is
auto-placed where it won't cover the action 🏷️, and an optional synthesized
dark-synthwave / phonk track 🎧 is mixed under the ambient audio.

Built for a personal hacker-gadget / maker blog — flashing firmware,
glowing terminals, M5Stick, the whole forbidden-tech aesthetic. 🛠️⚡

## ✨ What it does for you

| | |
|---|---|
| ⏩ **Kills dead air** | Fast-forwards the parts where you're just reading docs; keeps the payoff at 1x |
| 🏷️ **Smart captions** | Purple-on-white sticker text, auto-placed over the calmest part of the frame so it never hides your screen |
| 🛡️ **Won't get you flagged** | Warns about wording TikTok's moderation hates (`hack`, `deauth`, `exploit`…) before you post |
| 🎧 **Instant vibe** | Generates a royalty-free synthwave/phonk bed — zero copyright strikes |
| 📱 **Phone-grade quality** | 1080×1920, 10-bit HEVC, iPhone HLG color preserved — survives TikTok's re-encode |

## 📦 Install

```bash
python3 -m venv venv
venv/bin/pip install -e ".[dev]"   # needs ffmpeg + DejaVu/Noto fonts on the system
```

System requirements: `ffmpeg`/`ffprobe` with libx265, fonts
`fonts-dejavu` + `fonts-noto-color-emoji`.

## 🚀 Use

```bash
# preview the cut plan (instant, no encode)
tokcut clip.MOV -c "Flashing Bruce 1.15 on M5StickC Plus2 ⚡" --target 50 --dry-run

# render, no music (you add a trending sound in TikTok)
tokcut clip.MOV -c "Flashing Bruce 1.15 on M5StickC Plus2 ⚡" --target 50

# render with a synthesized phonk track baked in
tokcut clip.MOV -c "Flashing Bruce 1.15 on M5StickC Plus2 ⚡" --target 50 --music --music-style phonk

# or your own audio file
tokcut clip.MOV -c "..." --music ~/tracks/mytrack.mp3
```

(`python -m tokcut ...` works too if you didn't `pip install`.)

> 💡 **Pro tip:** always `--dry-run` first — it prints the cut plan
> (which seconds get fast-forwarded vs kept) in a fraction of a second,
> so you can dial in `--target` before committing to a multi-minute encode.

## ⚙️ How it works

1. **Probe** the source (dimensions/rotation/fps/duration/audio).
2. **Motion analysis** — decode tiny grayscale frames at 6 fps, score by
   mean frame-to-frame difference.
3. **Classify** the timeline into dead / lag / action tiers (adaptive
   percentile thresholds; short runs merged so cuts feel intentional).
4. **Speeds** — action 1x, lag ≈1.7x, dead ≈3.2x; `--target N` solves the
   fast-tier speeds to land at N seconds.
5. **Caption** — Pillow renders purple bold-italic on rounded white boxes
   (emoji supported). A saliency map places it over the calmest region
   inside TikTok's UI safe zone, so it never covers the screen/device.
6. **Music** (optional) — `tokcut.music` synthesizes a royalty-free
   dark-synthwave/phonk track to exact length and ducks it under the
   ambient audio. Zero copyright risk.
7. **Render** — one ffmpeg `filter_complex`: per-segment trim/setpts +
   atempo, concat, lanczos scale into 1080x1920, caption overlay, encode
   **libx265 main10 crf 18** preserving the iPhone HLG color tags.

## 🗂️ Layout

```
tokcut/            package
  analysis.py        probe, motion scoring, saliency, edit decision list
  caption.py         caption rendering + TikTok-eligibility checks
  layout.py          canvas layout + saliency-aware caption placement
  music.py           procedural dark-synthwave/phonk generator
  render.py          ffmpeg filtergraph + encode
  cli.py             argparse entry point
tests/             pytest suite (logic-level, no GPU/network needed)
docs/              USAGE.md, IDEAS.md
.github/workflows/ CI (lint + test; deploy stage stubbed for the VPS)
```

## 🧪 Develop

```bash
venv/bin/pytest          # run tests
venv/bin/ruff check .    # lint
```

## 🗺️ Roadmap

📱 Phone → private Telegram bot → edited file back (approve / redo loop),
plus 🥁 beat-aligned music cuts. See [`docs/IDEAS.md`](docs/IDEAS.md).

---

<div align="center">

Made for one creator's blog. 🖤 Licensed MIT — take it and remix it.

</div>
