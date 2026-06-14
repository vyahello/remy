# Using tokcut

## Quick start

```bash
cd ~/tkprop
tokcut YOUR_CLIP.MOV -c "Your caption text ⚡"      # if pip-installed
# or, without installing:
venv/bin/python3 -m tokcut YOUR_CLIP.MOV -c "Your caption text ⚡"
```

Output lands next to the input as `YOUR_CLIP_tokcut.mp4` unless you pass
`-o out.mp4`.

## Options

| Flag | Default | Meaning |
|------|---------|---------|
| `-c / --caption` | — | Persistent caption (required for vertical sources). Emoji supported (⚡🔥🧪💻…). Auto-balanced onto two lines. **Landscape sources never get a caption** — see below. |
| `-o / --output` | `<input>_tokcut.mp4` | Output path. |
| `--target` | `auto` | Output length. `auto` (default) solves a TikTok-friendly length: natural pacing ≤35s is kept, longer compresses toward the ~30s completion-rate sweet spot — floored by the real-time action, which is never sped up. A number solves for ≈ N seconds; `none` keeps base tier speeds (dead 3.2x, lag 1.7x, action 1x). |
| `--style` | `purple` | Caption look: `purple` (purple bold-italic on white — the house style), `yellow` (black on yellow), `black` (white on black). |
| `--caption-pos` | `auto` | `auto` builds a saliency map (motion + detail + brightness over the whole video) and places the caption over the calmest region inside the TikTok safe zone, so it never covers the screen/device. `top` pins it just below the top UI bar; `bottom` uses a letterboxed band below the video (legacy style — risks TikTok UI overlap). |
| `--hook` / `--no-hook` | on | Cold-open: prepend ~1.3s of the video's strongest beat (biased toward late peaks, where the payoff lives) before the chronological cut. The single biggest retention lever. |
| `--crop` / `--no-crop` | on | Auto-zoom into the motion-energy bounding box, dropping static margins (desktop wallpaper, window chrome). Only crops when it gains ≥10% — otherwise leaves the frame alone. |
| `--zoom F` | 1.0 | Framing dial on top of the auto-zoom: `1.2` punches in tighter around the same center, `0.8` pulls wider. Works even with `--no-crop` (a deliberate centered punch-in). |
| `--hook-card` / `--no-hook-card` | off | Animated text card over the opening 1.6s (vertical only). See [Hook card](#hook-card). |
| `--hook-card-text` | caption | Override the hook card text (defaults to the caption). |
| `--hook-card-pushin` / `--no-hook-card-pushin` | off | Also ease the footage in under the card while it's visible. |
| `--keep-audio` | off | Keep the original ambient audio. **By default the export is muted** (no audio track) so you add a TikTok sound in-app. |
| `--music [FILE]` | off | Bake in music (implies sound). Bare flag synthesizes a royalty-free track; pass a path to use your own audio. For off-platform posts. |
| `--music-style` | synthwave | `synthwave` or `phonk` (the darker, heavier one). |
| `--music-bpm N` | style default | Tempo of the synthesized track — synthwave 84, phonk 132 unless overridden. |
| `--crf N` | 18 | x265 quality (lower = better/bigger). 18 is visually lossless for screen content. |
| `--preset P` | medium | x265 preset. Use `fast` if you're in a hurry, `slow` for max quality. |
| `--dry-run` | off | Print the edit decision list (segments + speeds) and exit — no encode. |

## Hook card

`--hook-card` bakes an **animated text card over the opening 1.6s** — the
single most-watched moment — to give scrollers an explicit reason to stay.
It's a faceless-content retention lever: a bold, motion-rendered promise in
the first frames, not just raw footage.

```bash
# reuse the caption text as the card
tokcut clip.MOV -c "How I set this up ⚡" --hook-card
# different words on the card than the persistent caption
tokcut clip.MOV -c "btop — terminal system monitor" \
  --hook-card --hook-card-text "I replaced my system monitor"
```

How it behaves:

- **Vertical only.** Landscape exports carry no baked text (you overlay your
  own), so the card is silently skipped there.
- **Text** defaults to the caption; `--hook-card-text` overrides it. It's
  rendered with the same styled boxes + color emoji as the caption (just
  bigger), and auto-shrinks the font if a long line would overflow.
- **Motion:** fades in over 0.3s with a subtle 0.92→1.0 scale ramp, holds,
  then fades out — on a dimmed backing box so it reads against busy footage.
- **One text block at a time:** the persistent caption is held back until the
  card fades, so the first second isn't cluttered. Caption then runs as usual.
- **Adds zero length** — it overlays the existing cold-open, so it doesn't
  fight your completion rate or change the cut timing.
- `--hook-card-pushin` additionally eases the footage in (a gentle settle)
  under the card; off by default.

It's **off by default** — add `--hook-card` when you want it.

## Landscape sources (laptop screen recordings)

A landscape source (w > h — OBS captures, screen recordings) is **not**
boxed into a 1080x1920 canvas: a small video floating on a vertical
canvas can't go fullscreen in TikTok. Instead it keeps its native
(post-crop) resolution and gets the same treatment otherwise — cuts,
speed-ups, hook, auto-zoom into the action, optional music with
beat-aligned cuts. Differences:

- **No caption is rendered** (there's no spare canvas for one) — overlay
  your own when posting. `-c` is not required.
- **Recorder-UI edges are hard-trimmed**: the first 1.5s and last 3.0s
  (where OBS & friends show their own windows) never make the cut.
- **The crop targets the window, not just the motion**: desktop strips,
  docks and wallpaper around the app window fall away, while the
  window's own static text is never sliced (a motion-only box would cut
  still terminal text mid-character).
- **Action may run up to 1.5x** when needed to hit the auto length —
  typing and scrolling output stay followable sped up; camera footage
  keeps action at strict real time.

## Recommended workflow

1. **Dry run first** to sanity-check the cut plan:
   ```bash
   venv/bin/python3 -m tokcut clip.MOV -c "..." --target 50 --dry-run
   ```
   You'll see which time ranges are kept at 1x (ACTION) vs fast-forwarded.
2. If the plan looks too aggressive/too soft, adjust `--target`
   (longer target = gentler speed-ups).
3. Render, check on your phone, post.

## Audio: muted by default

The export is **silent by default** (no audio stream) because the intended
workflow is to add a trending sound inside the TikTok app — that ranks
better for discovery and the app won't mute you for copyright. Just upload
the clip and tap a sound.

Two opt-outs when you want sound baked in:

- **`--keep-audio`** — keeps your original ambient audio (e.g. real
  keyboard/room sound) instead of muting.
- **`--music`** — bakes in a synthesized royalty-free track (synthwave /
  phonk, zero copyright risk), ducked under the original audio. Use this
  for posts you'll share **off** TikTok (Reels, Shorts, your site), where
  there's no in-app sound library. Because the track is generated at a
  known bpm, the cuts are **snapped onto its beat grid** — every segment
  change lands on a beat and the video ends on one. (A music *file* you
  pass yourself plays as-is, no alignment — its bpm is unknown.)

## Picking a target duration

- TikTok sweet spot: **35–60 s**.
- Rule of thumb: target ≈ 55 % of the raw duration.
- Don't go below ~35 % of raw length — fast-forward above ~5x starts to
  look like a glitch instead of a time-lapse.

## Caption guidelines

- Be **specific** about what the viewer is watching — a concrete caption
  ("How I set up my new desk", "Day 3 of the build") reads as intentional
  and is searchable; vague ones ("check this out") get scrolled past.
- Keep it under ~40 characters so both lines stay big and readable.
- One caption for the whole video — no mid-video text changes.

### TikTok eligibility (avoid getting flagged or shadowbanned)

TikTok OCRs on-screen text, and its moderation penalizes sensational or
policy-sensitive wording. `tokcut` warns automatically (`check_caption`)
about risky terms — heed the warnings:

- **Terms it flags by default**: hack/hacking/hacker, attack, exploit,
  deauth, crack, bypass, payload, spy, jam, steal, "free wifi". (Edit
  `RISKY_TERMS` in `tokcut/caption.py` to fit your own content.)
- **Prefer descriptive over edgy** — phrasing that plainly says what's
  happening is safe; clickbait that implies wrongdoing risks removal.
- The same applies to the description and hashtags you type when posting:
  keep them descriptive and on-topic rather than sensational.

## Recording a screen clip (local helper)

`scripts/record_tiktok_screen.sh` captures an X11 screen straight into a
pristine **1080x1920** source ready for tokcut. Your laptop screen is
landscape, so it grabs the tallest centered **9:16 column** and upscales it
to 1080x1920 with lanczos — arrange the app you're filming in the middle of
the screen. Default quality is **visually-lossless 10-bit 4:4:4 H.264**
(CRF 14), no audio.

```bash
scripts/record_tiktok_screen.sh                 # record until you press q
DURATION=30 scripts/record_tiktok_screen.sh     # auto-stop after 30 s
ENCODER=nvenc scripts/record_tiktok_screen.sh   # GPU encode (long sessions)
```

Stop with `q`↵ (or Ctrl-C); the file is finalized either way. Tune via env
vars: `FPS` (60), `CRF` (14), `ENCODER` (`x264`|`x265`|`nvenc`|`lossless`),
`PRESET`, `OUTDIR`, `DRAW_MOUSE` (1=show cursor), `REGION` (`WxH+X+Y` to
grab an exact rectangle instead of the auto 9:16 column). It then prints the
`tokcut` command to edit the result. X11 only (Wayland: use `wf-recorder`).

## Quality notes

- Output: 1080x1920, 60 fps, HEVC main10 (`hvc1`), HLG color preserved,
  AAC 192k audio, faststart. This survives TikTok's re-encode well.
- Encode time: roughly 2–4 minutes per 30 s of output on this machine
  with `--preset medium`.

## Troubleshooting

- **Washed-out colors** → fixed: output color tags now follow the source
  (HLG/PQ kept for HDR phone footage, bt709 for SDR screen recordings).
- **Caption missing emoji** → the glyph isn't in Noto Color Emoji, or the
  char's codepoint is below U+2600 (the simple emoji detector threshold).
- **Too many tiny speed changes** → raise `MIN_SEG_SEC` in `tikedit.py`.
