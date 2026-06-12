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
| `tokcut/music.py` | chord-progression synthwave/phonk generator — numpy composition, optional pedalboard mastering |
| `tokcut/render.py` | ffmpeg filtergraph builder + encode |
| `tokcut/cli.py` | argparse entry point (`python -m tokcut` / `tokcut`) |
| `tokcut/types.py` | shared `SourceInfo`/`Layout` TypedDicts + `Segment`/`SpeedSegment` aliases |
| `tokcut/judge.py` | Claude Code judgment layer: headless `claude -p` writes captions from sampled frames and reviews rendered output (subscription OAuth) |
| `tokcut/bot/` | private Telegram bot (`config`, `pipeline`, `app`) — see docs/BOT.md |
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
   intentional. Near-uniform motion collapses to one tier (no noise
   tiers on constantly-updating screen recordings).
4. **Editorial cuts** (`analysis.trim_dead_ends`, `pick_hook`,
   `content_crop`) — hard-trim boring lead-ins/outros (open and close on
   action); prepend a ~1.3s cold-open hook of the strongest beat (biased
   late, where the payoff lives; `--no-hook` to disable); auto-zoom into
   the motion-energy bounding box when it gains ≥10% (`--no-crop`).
   `--zoom F` (`analysis.zoom_crop`) is the creator's framing dial on
   top of the auto framing: >1 punches in tighter around the same
   center, <1 pulls wider (bot buttons 🔎/🔭, free text "closer"/"wider").
5. **Speeds** (`analysis.assign_speeds`) — action 1.0x, lag ≈1.7x,
   dead ≈3.2x; `--target N` binary-searches the fast-tier speeds to hit N
   seconds. Default is `--target auto` (`analysis.auto_target`): natural
   pacing ≤35s is kept, longer compresses toward the ~30s completion-rate
   sweet spot, floored by the 1x action time (action is never sped up).
   `--target none` keeps base tier speeds.
6. **Caption** (`caption.make_caption` + `layout.compute_layout`) — Pillow
   renders purple bold-italic on rounded white boxes + color emoji. A
   saliency map (brightness-dominant, because screens glow in dark-room
   footage) places it over the calmest region inside the TikTok safe zone
   (y between 11% and 78%). `caption.check_caption` warns about wording
   that risks TikTok moderation.
7. **Audio** — muted by default (the export is silent so a TikTok sound is
   added in-app; `render` emits `-an`). `--keep-audio` retains the original
   ambient track; `--music` (`music.generate`) synthesizes a royalty-free
   synthwave/phonk track and `render` ducks it under the ambient audio with
   `amix ... normalize=0`; any kept audio is loudness-normalized to
   TikTok's -14 LUFS (`loudnorm`). The generator composes real music in
   stereo: chord progressions (phonk i-i-VI-VII in Gm, synthwave
   Am-F-C-G), a fixed cowbell riff motif, arpeggios, a gliding
   tanh-driven 808 doubling the kick, sidechain pumping
   (`music._sidechain`), swung hats, gated-reverb snare and vinyl
   crackle. The master bus runs Spotify's `pedalboard` FX chain
   (compressor/chorus/reverb/limiter — installed via the `bot` extra)
   with a lowpass+softclip fallback when it's absent. With synthesized
   music the cuts are
   **beat-aligned** (`analysis.beat_align`): the track's beat grid is exact
   (known bpm, beat at t=0), so segment boundaries are nudged to land on
   beats in output time — no beat detection involved. User-supplied music
   files skip it (unknown bpm).
8. **Render** (`render.render`) — one ffmpeg `filter_complex`: per-segment
   trim/setpts + atempo, concat, optional crop, lanczos scale into
   1080x1920, caption overlay, encode **libx265 main10 crf 18** with
   `hvc1` tag, `render.encoder_params` (**aq-mode=3** — bits flow to the
   dark regions terminal footage lives in; screen content also relaxes
   the deblocker to keep text edges) and **color tags matched to the
   source** (`render.color_args`: HLG/PQ kept for HDR, bt709 for SDR —
   never hardcode HLG). `+faststart`.

## Conventions and constraints

- **Run via the venv**: `venv/bin/python3 -m tokcut …` (or `tokcut` if
  `pip install -e .` was run).
- **Color tags must match the source** (`render.color_args`) — HLG sources
  encoded without `arib-std-b67` look washed out, and SDR sources tagged
  as HLG look washed out too. Never hardcode either direction.
- **Vertical sources** export **1080x1920, ≥30fps (keep 60 if source is
  60), 10-bit HEVC** — quality is a hard requirement. **Landscape sources
  (w > h, e.g. OBS screen recordings) stay native resolution** — same
  cuts/speeds/hook/crop/music, but NO vertical canvas and NO caption
  (a boxed landscape video can't go fullscreen in TikTok; the creator
  overlays their own caption). Landscape also hard-trims OBS edges
  (`analysis.edit_window`: head 1.5s, tail 3.0s), crops to the **window
  hosting the action** (`analysis.window_crop`: desktop strips/docks
  fall away, on-screen text is never sliced — motion-only boxes cut
  static terminal text mid-character), and may speed the action tier up
  to **1.5x** (`SCREEN_ACTION_MAX`) to hit the auto length — screen
  content stays followable; camera action stays strictly 1.0x.
- One caption per video (vertical only), persistent for the entire
  duration. Make it specific about what the viewer is watching, e.g.
  "How I set this up ⚡". Run it past `check_caption` —
  sensational/policy-sensitive wording can get the post
  flagged/shadowbanned.
- Audio is **muted by default** — the export has no audio stream so a
  trending TikTok sound is added in-app (ranks better, no copyright mute).
  `--keep-audio` retains original ambient; `--music` bakes in the
  synthwave/phonk track (for off-platform posts).
- Use `--dry-run` first when tuning: prints the edit decision list without
  encoding (encode takes minutes).
- **Run renders sequentially, never in parallel** — two concurrent x265
  encodes on 1080p60 sources have OOM-killed ffmpeg on this 15 GB machine.
  The Telegram bot must queue renders one at a time.

## Develop

```bash
venv/bin/pip install -e ".[dev]"
venv/bin/pytest          # 33 tests, < 1s, no ffmpeg required
venv/bin/ruff check tokcut tests
venv/bin/mypy            # fully typed; must stay clean
```

The codebase is fully type-hinted and mypy-clean — keep it that way when
editing. CI (`.github/workflows/ci.yml`) runs ruff + mypy + pytest on
3.11–3.13. The deploy stage pushes `main` to the VPS over SSH; it is
armed by the repo variable `TOKCUT_DEPLOY=enabled` + `VPS_*` secrets and
skipped otherwise. Server setup: `deploy/bootstrap.sh`, runbook
`docs/DEPLOY.md`.

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
