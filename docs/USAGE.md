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
| `-c / --caption` | required | Persistent caption. Emoji supported (⚡🔥🧪💻…). Auto-balanced onto two lines. |
| `-o / --output` | `<input>_tokcut.mp4` | Output path. |
| `--target N` | none | Solve speed-ups so the result is ≈ N seconds. Without it, base speeds are used (dead 3.2x, lag 1.7x, action 1x). |
| `--caption-pos` | `auto` | `auto` builds a saliency map (motion + detail + brightness over the whole video) and places the caption over the calmest region inside the TikTok safe zone, so it never covers the screen/device. `top` pins it just below the top UI bar; `bottom` uses a letterboxed band below the video (legacy style — risks TikTok UI overlap). |
| `--hook` / `--no-hook` | on | Cold-open: prepend ~1.3s of the video's strongest beat (biased toward late peaks, where the payoff lives) before the chronological cut. The single biggest retention lever. |
| `--crop` / `--no-crop` | on | Auto-zoom into the motion-energy bounding box, dropping static margins (desktop wallpaper, window chrome). Only crops when it gains ≥10% — otherwise leaves the frame alone. |
| `--keep-audio` | off | Keep the original ambient audio. **By default the export is muted** (no audio track) so you add a TikTok sound in-app. |
| `--music [FILE]` | off | Bake in music (implies sound). Bare flag synthesizes a royalty-free track; pass a path to use your own audio. For off-platform posts. |
| `--music-style` | synthwave | `synthwave` or `phonk` (the darker, heavier one). |
| `--music-bpm N` | 84 | Tempo of the synthesized track. |
| `--crf N` | 18 | x265 quality (lower = better/bigger). 18 is visually lossless for screen content. |
| `--preset P` | medium | x265 preset. Use `fast` if you're in a hurry, `slow` for max quality. |
| `--dry-run` | off | Print the edit decision list (segments + speeds) and exit — no encode. |

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
  there's no in-app sound library.

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
