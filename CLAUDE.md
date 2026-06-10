# tokcut — auto TikTok editor

## What this project is

A general-purpose pipeline that turns raw phone clips (long, with dead time)
into tight, high-quality vertical TikTok videos: redundant chunks
fast-forwarded, action kept at real speed, a persistent styled caption
auto-placed where it won't cover the action, and optional synthesized music
mixed under the original audio. Works for any talking-head, screen-recording,
tutorial, vlog, or process video.

End goal: record on phone → send to a private Telegram bot → bot returns the
edited clip ready to post (see `docs/IDEAS.md`).

## Package layout

| Path | Purpose |
|------|---------|
| `tokcut/analysis.py` | probe, motion scoring, saliency map, edit decision list |
| `tokcut/caption.py` | caption PNG rendering + TikTok-eligibility checks |
| `tokcut/layout.py` | 1080x1920 canvas layout + saliency-aware caption placement |
| `tokcut/music.py` | procedural dark-synthwave/phonk generator (numpy) |
| `tokcut/render.py` | ffmpeg filtergraph builder + encode |
| `tokcut/cli.py` | argparse entry point (`python -m tokcut` / `tokcut`) |
| `tokcut/types.py` | shared `SourceInfo`/`Layout` TypedDicts + `Segment`/`SpeedSegment` aliases |
| `tests/` | pytest suite — pure logic, no ffmpeg/network needed (one font-gated test) |
| `docs/USAGE.md` | how to run it |
| `docs/IDEAS.md` | content/format brainstorm + Telegram bot + music roadmap |
| `original.MOV` | sample raw clip (iPhone, HEVC 10-bit HLG, 60fps) — gitignored |
| `edited.MP4` | hand-made reference edit (the quality bar) — gitignored |
| `auto_edited*.mp4` | sample outputs — gitignored |

## How the pipeline works

1. **Probe** (`analysis.probe`) — ffprobe for dimensions/duration/fps/
   rotation/audio.
2. **Motion analysis** (`analysis.motion_scores`) — decode tiny (120px)
   grayscale frames at 6 fps, score = mean absolute frame difference.
   Active moments (typing, handling the device) score higher than idle.
3. **Classify** (`analysis.classify` + `to_segments`) — adaptive
   percentile thresholds (45th/80th) split the timeline into
   dead/lag/action tiers; runs shorter than 1.4s merge so cuts feel
   intentional.
4. **Speeds** (`analysis.assign_speeds`) — action 1.0x, lag ≈1.7x,
   dead ≈3.2x; `--target N` binary-searches the fast-tier speeds to hit N
   seconds.
5. **Caption** (`caption.make_caption` + `layout.compute_layout`) — Pillow
   renders purple bold-italic on rounded white boxes + color emoji. A
   saliency map (brightness-dominant, because screens glow in dark-room
   footage) places it over the calmest region inside the TikTok safe zone
   (y between 11% and 78%). `caption.check_caption` warns about wording
   that risks TikTok moderation.
6. **Audio** — muted by default (the export is silent so a TikTok sound is
   added in-app; `render` emits `-an`). `--keep-audio` retains the original
   ambient track; `--music` (`music.generate`) synthesizes a royalty-free
   synthwave/phonk track and `render` ducks it under the ambient audio with
   `amix ... normalize=0`.
7. **Render** (`render.render`) — one ffmpeg `filter_complex`: per-segment
   trim/setpts + atempo, concat, lanczos scale into 1080x1920, caption
   overlay, encode **libx265 main10 crf 18** with the source HLG color tags
   (`bt2020`/`arib-std-b67`) and `hvc1` tag. `+faststart`.

## Conventions and constraints

- **Run via the venv**: `venv/bin/python3 -m tokcut …` (or `tokcut` if
  `pip install -e .` was run).
- **Never strip the color tags** — source is iPhone HLG; encoding without
  `-color_trc arib-std-b67` makes footage look washed out.
- Output stays **1080x1920, ≥30fps (keep 60 if source is 60), 10-bit HEVC**
  — quality is a hard requirement.
- One caption per video, persistent for the entire duration. Make it
  specific about what the viewer is watching, e.g. "How I set this up ⚡".
  Run it past `check_caption` — sensational/policy-sensitive wording can
  get the post flagged/shadowbanned.
- Audio is **muted by default** — the export has no audio stream so a
  trending TikTok sound is added in-app (ranks better, no copyright mute).
  `--keep-audio` retains original ambient; `--music` bakes in the
  synthwave/phonk track (for off-platform posts).
- Use `--dry-run` first when tuning: prints the edit decision list without
  encoding (encode takes minutes).

## Develop

```bash
venv/bin/pip install -e ".[dev]"
venv/bin/pytest          # 33 tests, < 1s, no ffmpeg required
venv/bin/ruff check tokcut tests
venv/bin/mypy            # fully typed; must stay clean
```

The codebase is fully type-hinted and mypy-clean — keep it that way when
editing. CI (`.github/workflows/ci.yml`) runs ruff + mypy + pytest on
3.11–3.13. The deploy stage is stubbed (`if: ... && false`) until the
VPS/Telegram bot exists.

## Reproduce the sample result

```bash
venv/bin/python3 -m tokcut original.MOV \
  -c "How I set this up ⚡" \
  --target 53 -o auto_edited.mp4
# with music:
venv/bin/python3 -m tokcut original.MOV \
  -c "How I set this up ⚡" \
  --target 53 --music --music-style phonk -o auto_edited_music.mp4
```
