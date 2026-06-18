# Video pipeline discovery — animated hook cards for first-second retention

> **Discovery pass only.** No source files were modified. This document maps
> the current pipeline, identifies the weakest link in the hook step for
> first-second retention, evaluates two ways to add an animated hook card,
> recommends one, and proposes a minimal integration plan.

## 0. Reconciliation: brief vs. this repo

The task brief describes a project ("tiktok-prep") whose shape only partly
matches this repo (`tokcut`). To keep the plan honest, here is what is real
and what is not, with the mapping I used:

| Brief assumption | Reality in this repo | Mapping used below |
|---|---|---|
| `docs/spec.md` | **Does not exist.** Docs are `USAGE.md`, `IDEAS.md`, `BOT.md`, `BOT_ARCHITECTURE.md`, `DEPLOY.md`. | Read the modules directly; treat `CLAUDE.md` + `USAGE.md` as the de-facto spec. |
| `pydantic-settings` config | **Not used.** CLI config is `argparse` in `tokcut/cli.py`, threaded as keyword args into `edit(...)`. The bot has its own config object. | "Preserve config" = preserve the argparse → `edit()` kwarg flow. |
| Two-scorer architecture | Two candidates: (a) `analysis.motion_scores` + `analysis.saliency_map` (deterministic scoring), (b) `judge.py` two Claude calls — caption-writer + output-reviewer. | I read "two-scorer" as the `judge.py` judgment layer and keep it untouched; motion+saliency also untouched. |
| Anti-reupload fingerprinting (FFmpeg) | **No such module.** `grep` finds only an unrelated "re-upload" note in `IDEAS.md`. | Listed as *absent* in the current-state map; not part of this plan. |
| Hook **overlay** via `drawtext` | **No `drawtext` anywhere.** The "hook" is `analysis.pick_hook` prepending ~1.3s of real footage (a cold open). On-screen text is a **Pillow PNG** overlaid with ffmpeg `overlay`. | The "hook-overlay step" = the cold-open segment + the persistent caption that rides over it. This is exactly the gap an animated hook card fills. |
| Burned-in captions, `loudnorm`, `claude` via subprocess, no Whisper, lean deps | **All true.** | Constraints honored as written. |

The substantive request — animate the first second to lift retention — is
valid and worth doing. The rest of this doc proceeds on the real code.

## 1. Current state — how each step is implemented

All editing is one Python analysis pass plus a single ffmpeg `filter_complex`
(or a bounded two-pass for heavy clips). No second runtime, no Node.

### 1a. The "hook" (cold open)
- `analysis.pick_hook` (`analysis.py:158`) scans the smoothed motion score,
  weights later peaks up (`np.linspace(0.4, 1.0, …)` — the payoff lives late),
  skips the first 4s, and returns a `(start, end)` window ~1.3s long.
- `cli.plan` (`cli.py:148`) prepends that window as `segs[0]` at speed `1.0`
  (`(hook_win[0], hook_win[1], 1.0)`), so the edit *opens on its strongest
  beat*, then cuts to the chronological body.
- **There is no graphic, text reveal, or animation on the hook.** It is raw
  source footage. The only text in frame is the persistent caption (below).

### 1b. Captions (burned in)
- `caption.make_caption` (`caption.py:97`) renders purple bold-italic text on
  rounded white boxes **as a Pillow PNG**, with real color-emoji compositing
  (`_emoji_tile`, `caption.py:83`) — drawtext can't do color emoji, this can.
- `layout.compute_layout` places it over the calmest safe-zone region from the
  saliency map.
- `render._format_video` (`render.py:108`) overlays the PNG at `cap_x,cap_y`:
  `[base][{cap}]overlay={cap_x}:{cap_y},format=yuv420p10le[vout]`.
- The caption is **static and persistent** for the whole video — including the
  hook second. It does not animate, and its saliency-chosen position is often
  mid/low frame, not a top-of-frame first-second promise.
- `caption.check_caption` (`caption.py:42`) warns on moderation-risky wording.
- Landscape sources get **no caption** by rule (creator overlays their own).

### 1c. Loudness normalization
- `render._mix_and_norm` (`render.py:112`) mixes music/ambient then applies
  `LOUDNORM = "loudnorm=I=-14:TP=-1.5:LRA=11"` (`render.py:27`) → TikTok's
  −14 LUFS. Only runs when audio is kept/added; default export is muted.

### 1d. "Anti-reupload fingerprinting"
- **Not implemented.** The encode does set source-matched color tags
  (`render.color_args`), `hvc1`, `+faststart`, `aq-mode=3` — but there is no
  perceptual perturbation / metadata scrub / micro-crop for de-dup evasion.
  Out of scope here; noted so the map is complete.

### 1e. Where the filtergraph is built
- `render.build_filtergraph` (`render.py:139`) — single-pass graph: each
  segment a seek-decoded input → `setpts` → `concat` → `_format_video`
  (crop/fps/scale/grade/pad/overlay) → `_mix_and_norm`.
- `render.use_two_pass` (`render.py:236`) routes heavy/long clips to
  `_render_segmented` (`render.py:316`), which encodes each segment alone then
  concat-demuxes — one decoder at a time, flat memory.

## 2. Weakest link for first-second retention

**The first second carries no explicit, motion-rendered promise.** Concretely:

1. **The cold open is raw footage with no verbal hook.** For faceless,
   terminal-heavy content, frame 1 is often near-static text on a dark
   background. TikTok's first-second retention is driven by a *pattern
   interrupt* — bold kinetic text stating the payoff — and the pipeline emits
   none. It bets entirely on the footage being self-evidently interesting.
2. **The only text is the persistent caption, which is not designed as a
   first-frame hook.** It is static, saliency-placed (often mid/low frame, to
   *avoid* the action), and worded as a steady label ("How I set this up ⚡"),
   not as a scroll-stopping promise. It enters with no animation.
3. **The cut into the body is a hard cut with no visual punch** — no push-in,
   no text reveal, nothing that reads as "produced."

So the highest-leverage gap is a **1–2s animated hook card**: a bold, motion-
rendered text promise over the opening beat, separate from the steady caption.

## 3. Options for the animated hook card

### Path A — pure FFmpeg (no new deps)
Render the card with filters the engine already has: a bold text promise that
fades/scales in over the hook segment, optional push-in (`zoompan`/`scale`),
optional dim/blur of the underlying frame.

- **Text rendering nuance:** `drawtext` is the obvious tool but **cannot do
  the brand's color emoji** (the repo deliberately uses Pillow PNG compositing
  for exactly this reason — `caption.py:83`). The stronger Path-A variant is
  to **render the card text as a Pillow PNG (reuse `caption.make_caption`)**
  and animate the PNG with `overlay` + `fade` (alpha) + a scale ramp. Zero new
  deps, reuses code that already solves emoji + 10-bit-safe overlay.
- Stays inside the existing `_format_video` / two-pass machinery and the
  10-bit (`yuv420p10le`) pipeline.

### Path B — Remotion card, then concat/overlay via ffmpeg
Render a 1–2s React/Remotion composition to a clip, then concat/overlay it.

- Remotion renders via **headless Chromium + a Node/TypeScript toolchain**.
  That means: a second language runtime, an npm dependency tree, and a browser
  binary added to a **Python-only** CLI that deploys to a **3.7 GB shared VPS
  which already livelocks under a single x265 encode** (`render.py:11` history).
- New CI surface (Node alongside the 3.11–3.13 Python matrix), new bootstrap
  steps, cold-start/render latency, and a class of failure (browser/font/GPU)
  the project has never had to support.

### Comparison

| Criterion | Path A (ffmpeg + Pillow PNG) | Path B (Remotion) |
|---|---|---|
| New deps | **None** (ffmpeg + Pillow already core) | Node, npm tree, headless Chromium |
| Fits lean/faceless constraint | **Yes** | No — heavy by definition |
| VPS memory risk | Negligible (one overlay) | Browser RSS on a box that already swaps |
| Color emoji (brand style) | **Yes** (reuse `make_caption`) | Yes |
| 10-bit / HDR pipeline | **Native** (stays in `yuv420p10le`) | Needs careful color round-trip on concat |
| CI / deploy impact | None | New runtime in CI + bootstrap |
| Motion-graphics ceiling | Fades/scale/push-in/blur (enough for a text card) | Very high (unneeded here) |
| Time to ship | Low | High |

### Recommendation — **Path A**, specifically the Pillow-PNG variant

For a lean, faceless, Python+ffmpeg CLI on a memory-constrained VPS, a 1–2s
**text** card is squarely within ffmpeg's reach, and Remotion's only real
advantage (rich React motion graphics) is capability this content does not
need. Path B's cost — a Node/TS/Chromium toolchain bolted onto a Python CLI
that already fights for memory — is disproportionate to a one-to-two-second
card and directly violates the "avoid heavy new deps / lean toolchain"
constraint.

Within Path A, **render the card as a Pillow PNG (reusing `caption.py`) and
animate it with `overlay`+`fade`+scale**, rather than `drawtext` — it reuses
the code that already solves color emoji and 10-bit-safe overlay, and keeps
the card visually consistent with the house caption style.

## 4. Minimal integration plan

Goal: add the card with the smallest diff, preserving `--dry-run`, the
argparse→`edit()` config flow, and the `judge.py` judgment layer untouched.

### Where it slots in
- **Vertical only.** Landscape exports deliberately carry no baked text
  (creator overlays their own) — the card follows that rule.
- **Overlay on the existing hook segment — do not add a new segment.** The
  hook is already `segs[0]` (`cli.py:148`). Animate the card *over* that
  segment's first ~1–2s inside `_format_video`, time-gated with
  `overlay=…:enable='lte(t,1.6)'` plus a `fade` on the card's alpha. This adds
  **zero duration** (protects the completion-rate target) and **does not
  perturb the beat grid** (`analysis.beat_align` sees the same segment list).

### Concrete steps
1. **Card text source.** Default: reuse the persistent caption text. Optional
   upgrade: a distinct one-line hook from `judge.suggest_captions` (the LLM
   layer already writes from frames — reuse, don't rebuild). Either way the
   scorers in `analysis.py` and the two Claude calls in `judge.py` are
   **unchanged**.
2. **Render the card PNG** via a thin wrapper over `caption.make_caption`
   (bigger font, top-of-frame placement) into the existing `tmp` dir in
   `cli.edit` (`cli.py:232`).
3. **Animate in the filtergraph.** Add an optional `hook_card` overlay branch
   to `render._format_video` (`render.py:87`) used by **both** the single-pass
   and two-pass paths (they already share `_format_video`, so parity is free):
   `fade=in:…:alpha=1` on the card input, `overlay=…:enable='lte(t,DUR)'`,
   optional underlying `zoompan`/`scale` push-in. Keep `format=yuv420p10le`.
4. **Config (argparse, not pydantic).** Add `--hook-card/--no-hook-card`
   (`BooleanOptionalAction`, default **off** to ship safely) and optional
   `--hook-card-text`; thread them through `edit(...)` kwargs exactly like the
   existing `hook`/`look` flags. Mirror in the bot's feedback map later.
5. **`--dry-run` preserved.** In `cli.edit`, before the `if dry_run: return`
   (`cli.py:229`), print one line: `hook card: "<text>" (0.0–1.6s, fade-in)`.
   No frames are rendered in dry-run — the card decision is metadata only.

### What stays unchanged
- All of `analysis.py` (tiers, speeds, `pick_hook`, `beat_align`,
  crop/zoom) — the card is a render-time overlay, not an edit decision.
- The persistent-caption pipeline, `layout.compute_layout`, saliency.
- Audio mix + `loudnorm`, encoder params, `color_args`, `use_two_pass`
  dispatch, the two-pass memory strategy.
- `judge.py` (the two-call judgment layer) — at most *reused* for card text.

## 5. Open questions

1. **Card text = persistent caption, or a distinct hook line?** Distinct lifts
   retention but spends one extra `judge` call (and an LLM round-trip in the
   bot path). Default to reuse; make distinct opt-in.
2. **Duration & motion:** 1.0s, 1.6s, or 2.0s? Hard fade-out vs. hold-through-
   hook? Push-in on the underlying footage, or card-only motion? Needs a quick
   on-phone A/B.
3. **Landscape:** confirmed *excluded* (no baked text rule). Agree, or do
   creators want an optional card there too despite overlaying their own?
4. **Interaction with the cold-open content:** the card sits over the
   strongest *late* beat shown first — does bold text over already-busy action
   help or clutter? May want the card to prefer a dimmed/blurred backing for
   legibility.
5. **HDR (HLG/PQ) sources:** verify `fade`/`overlay` alpha behaves on a 10-bit
   HLG base (it should — the pipeline is already `yuv420p10le`), but worth a
   visual check on a real iPhone clip.
6. **Should the persistent caption suppress itself during the card** to avoid
   two text blocks competing in second one? Likely yes — gate the persistent
   caption `enable` to start after the card fades.
