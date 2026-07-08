# Using remy

## Quick start

```bash
cd ~/tkprop
remy YOUR_CLIP.MOV -c "Your caption text ⚡"      # if pip-installed
# or, without installing:
venv/bin/python3 -m remy YOUR_CLIP.MOV -c "Your caption text ⚡"
```

Output lands next to the input as `YOUR_CLIP_remy.mp4` unless you pass
`-o out.mp4`.

## Options

| Flag | Default | Meaning |
|------|---------|---------|
| `-c / --caption` | — | Persistent caption (optional). Emoji supported (⚡🔥🧪💻…). Auto-balanced onto two lines. Omit it for a clean vertical export with no baked caption. **Landscape sources never get a caption** — see below. |
| `-o / --output` | `<input>_remy.mp4` | Output path. |
| `--target` | `auto` | Output length. `auto` (default) solves a TikTok-friendly length: natural pacing ≤55s is kept, longer compresses toward a ~45s sweet spot and is capped at ~2 min — the coding may speed up mildly (≤1.8x, 2.5x only past the cap; silent exports only), while the judge-detected demo span always plays real time. A number solves for ≈ N seconds; `none` keeps base tier speeds (dead 2.4x, lag 1.3x, action 1x). |
| `--style` | `black` | Caption look: `black` (white on translucent dark glass — the house default), `purple` (purple on white), `yellow` (black on yellow). |
| `--caption-pos` | `auto` | `auto` builds a saliency map (motion + detail + brightness over the whole video) and places the caption over the calmest region across the **whole** TikTok safe zone — a mild top bias only breaks ties, so a calm frame rides high on the black bar while a bright/busy top (e.g. a laptop screen) pushes the caption down onto the still region below (a dark keyboard, a hand), never over the content. `top` pins it just below the top UI bar; `bottom` uses a letterboxed band below the video (legacy style — risks TikTok UI overlap). |
| `--hook` / `--no-hook` | off | Cold-open: prepend a ~3s teaser of the video's strongest beat (biased toward late peaks; with `--detect-payoff`, picked from inside the actual demo) before the chronological cut, with the hook card on top saying what's coming. Opt-in on the CLI — add `--hook` to enable (the Telegram bot defaults it ON). |
| `--detect-payoff` | off | Have Claude find the demo/payoff span: it plays at strict 1.0x however hard the rest compresses, the cold open teases it, and its line words the hook card (needs the `claude` CLI; best-effort). The bot runs this automatically at upload. |
| `--crop` / `--no-crop` | on | Auto-zoom into the motion-energy bounding box, dropping static margins (desktop wallpaper, window chrome). Only crops when it gains ≥10% — otherwise leaves the frame alone. |
| `--zoom F` | 1.0 | Framing dial on top of the auto-zoom: `1.2` punches in tighter around the same center, `0.8` pulls wider. Works even with `--no-crop` (a deliberate centered punch-in). |
| `--trim-start SEC` | 0 | Hard-cut this many seconds off the source **head** — a recorder-UI intro (OBS/screen-capture window), a long fumble before the action. Stacks on the automatic edge-trim (whichever removes more wins). |
| `--trim-end SEC` | 0 | Hard-cut this many seconds off the source **tail** — a redundant outro (exiting the app, the stop-recording shuffle). Stacks on the automatic edge-trim. |
| `--hook-card` / `--no-hook-card` | follows `--hook` | Animated text card over the opening ~4.3s (vertical only). On by default whenever the hook is on; `--no-hook-card` opts out. See [Hook card](#hook-card). |
| `--hook-card-text` | payoff line / caption | Override the hook card text (defaults to the detected payoff line, else the caption). |
| `--hook-card-pushin` / `--no-hook-card-pushin` | off | Also ease the footage in under the card while it's visible. |
| `--keep-audio` | off | Keep the original ambient audio. **By default the export is muted** (no audio track) so you add a TikTok sound in-app. |
| `--music [FILE]` | off | Bake in music (implies sound). Bare flag synthesizes a royalty-free track; pass a path to use your own audio. For off-platform posts. |
| `--music-style` | synthwave | `synthwave` or `phonk` (the darker, heavier one). |
| `--music-bpm N` | style default | Tempo of the synthesized track — synthwave 84, phonk 132 unless overridden. |
| `--crf N` | 18 | x265 quality (lower = better/bigger). 18 is visually lossless for screen content. |
| `--preset P` | medium | x265 preset. Use `fast` if you're in a hurry, `slow` for max quality. |
| `--dry-run` | off | Print the edit decision list (segments + speeds) and exit — no encode. |

## Hook card

The hook card is an **animated text card over the opening ~4.3s** — the
single most-watched moment — that tells scrollers exactly what they're
about to watch. It bakes automatically whenever `--hook` is on (the cold
open without words is just unexplained footage); `--no-hook-card` opts
out, and `--hook-card` forces it on without a hook.

```bash
# reuse the caption text as the card
remy clip.MOV -c "How I set this up ⚡" --hook-card
# different words on the card than the persistent caption
remy clip.MOV -c "btop — terminal system monitor" \
  --hook-card --hook-card-text "I replaced my system monitor"
```

How it behaves:

- **Vertical only.** Landscape exports carry no baked text (you overlay your
  own), so the card is silently skipped there.
- **Text** defaults to the judge's payoff line (`--detect-payoff` / the
  bot's automatic pass), else the caption; `--hook-card-text` overrides
  both. It's rendered with the same styled boxes + color emoji as the
  caption (just bigger), and auto-shrinks the font if a long line would
  overflow.
- **Motion:** fades in over 0.3s with a subtle 0.92→1.0 scale ramp, holds,
  then fades out — on a dimmed backing box so it reads against busy footage.
- **One text block at a time:** the persistent caption is held back until the
  card fades, so the first second isn't cluttered. Caption then runs as usual.
- **Adds zero length** — it overlays the existing cold-open, so it doesn't
  fight your completion rate or change the cut timing.
- `--hook-card-pushin` additionally eases the footage in (a gentle settle)
  under the card; off by default.

It **follows `--hook`** — on with the cold open, `--no-hook-card` to opt
out, `--hook-card` to force it without one.

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
   venv/bin/python3 -m remy clip.MOV -c "..." --target 50 --dry-run
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
policy-sensitive wording. `remy` warns automatically (`check_caption`)
about risky terms — heed the warnings:

- **Terms it flags by default**: hack/hacking/hacker, attack, exploit,
  deauth, crack, bypass, payload, spy, jam, steal, "free wifi". (Edit
  `RISKY_TERMS` in `remy/caption.py` to fit your own content.)
- **Prefer descriptive over edgy** — phrasing that plainly says what's
  happening is safe; clickbait that implies wrongdoing risks removal.
- The same applies to the description and hashtags you type when posting:
  keep them descriptive and on-topic rather than sensational.

## Recording a screen clip (local helper)

`scripts/record_tiktok_screen.sh` captures an X11 screen straight into a
pristine source ready for remy, in either orientation:

- **vertical** (default) → **1080x1920** (9:16) — TikTok full-screen, remy
  adds a caption. Grabs the tallest centered 9:16 column and upscales it
  with lanczos.
- **full** → your **whole screen at its native size** (e.g. **1920x1200**
  on a 16:10 laptop), no aspect cropping, captured 1:1. Kept native by remy;
  best for terminals & screen recordings (wide content fills the frame, no
  dead bars).

Before capture it **draws the chosen region on screen as a bright green
frame** so you can drag your window into it instead of guessing where the
recording lands, then press Enter to start. Default quality is
**visually-lossless 10-bit 4:4:4 H.264** (CRF 14), no audio. The dead
**first 2 s and last 2 s are auto-trimmed** off the result
(`TRIM_HEAD` / `TRIM_TAIL`).

```bash
scripts/record_tiktok_screen.sh                 # interactive vertical, q to stop
scripts/record_tiktok_screen.sh full            # interactive whole screen (e.g. 1920x1200)
scripts/record_tiktok_screen.sh start full       # record in the BACKGROUND
scripts/record_tiktok_screen.sh stop            # stop it (from any directory)
scripts/record_tiktok_screen.sh status          # is one running?
scripts/record_tiktok_screen.sh install         # → ~/.local/bin/remy-rec (run anywhere)
DURATION=30 scripts/record_tiktok_screen.sh     # auto-stop after 30 s
```

The interactive mode records in the foreground (stop with `q`↵ or Ctrl-C).
`start`/`stop` run it **detached in the background** so your terminal stays
free — `start` it, do your demo in the same shell, `stop` it from anywhere.
After `install`, `remy-rec start` / `remy-rec stop` work in any directory; the
clip lands in your current directory.

The file is finalized however it stops — capture goes to a fragmented
`*.part.mp4` that stays playable even if the recorder is killed mid-session,
then is losslessly remuxed into the final faststart MP4 and trimmed. (If a
hard crash ever leaves a `*.part.mp4` behind, it's still a valid video — just
rename it to `.mp4`.) Tune via env vars: `ORIENT` (`vertical`|`full`),
`FPS` (60), `CRF` (14), `ENCODER` (`x264`|`x265`|`nvenc`|`lossless`), `PRESET`,
`OUTDIR`, `DRAW_MOUSE` (1=show cursor), `TRIM_HEAD`/`TRIM_TAIL` (2), `GUIDE`
(0 to skip the frame), `REGION` (`WxH+X+Y` to grab an exact rectangle). It then
prints the `remy` command to edit the result. X11 only (Wayland: use
`wf-recorder`).

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
