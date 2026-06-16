# Ideas: making these videos pop on TikTok

Format-level tactics that apply to any vertical clip — tutorials, vlogs,
screen recordings, builds, process videos — plus the product roadmap.

## Format playbook (what remy already does + what to add)

1. **Hook in the first 1.5 s** — never open on a static, low-energy frame.
   *Idea (next version):* `--hook` flag that finds the highest-motion or
   most visually striking moment and prepends 1–1.5 s of it as a cold-open
   teaser before cutting to the start. "Show the payoff first, then how I
   got there" massively lifts retention.
2. **Persistent specific caption** (done) — say plainly what the viewer is
   watching. Specificity = clarity = saves/shares from people who want it.
3. **Speed ramps instead of cuts** (done) — the time-lapse feel keeps a
   process honest and lets viewers rewatch to catch details — that's the
   loop signal TikTok rewards.
4. **End on the payoff** — finish on the result/reveal, not on setup or
   dead time. The motion analysis usually does this naturally; a future
   `--end-on-action` flag could guarantee it.
5. **Loop-friendly ending** — if the last frame visually resembles the
   first, the video loops seamlessly and TikTok counts the rewatch. Worth
   framing your shots with this in mind.

## Content tactics (series > one-offs)

- **Consistent template** — same caption style and structure every episode
  makes your feed instantly recognizable as a brand.
- **Before/after** — a half-second of the "before" state up front, then the
  transformation, reads as an irresistible payoff.
- **Break a topic into a series** — one idea per clip keeps each video tight
  and gives viewers a reason to follow for the next part.
- **Show the fails** — a mistake and the recovery outperforms a clean
  success for engagement; it's relatable and re-watchable.
- Post consistently (3–5/week at steady times) and reply to comments —
  comments are one of the strongest ranking signals, especially early on.

## Quality signals

- 10-bit HEVC + HLG preserved (done) — footage keeps the rich phone-camera
  look instead of going gray after upload.
- Original audio kept under speed-ups (done) — real ambient sound reads as
  authentic even with a music bed on top.
- No AI voice-over, no stock transitions — keep it feeling real.

## Roadmap: Telegram bot (the ideal workflow)

Goal: film on phone → share to a private Telegram bot → get back the edited
file, review, approve or redo.

**Architecture (simple and sufficient):**

```
phone ──(video file, NOT photo-compressed)──> Telegram bot (python-telegram-bot)
   bot downloads file → runs remy → sends result back as *document*
```

**Conversation flow:**

```
1. Upload raw clip (as file/document)
2. Bot analyses it fully automatically:
     - motion edit plan (remy)
     - caption text (vision model reads the frames and proposes one)
     - caption position (saliency auto-placement, already implemented)
     - eligibility check (check_caption) before render
3. Bot renders and sends the result back as a document
4. Review on the phone: [✅ Approve] [🔁 Redo]
     - Redo asks what to change (shorter/longer, different caption,
       caption elsewhere, more/less speed-up, music on/off) via inline
       buttons, then re-renders with adjusted params
```

Key implementation notes for when we build it:

- Use `python-telegram-bot` v21+, polling mode (no public server needed).
- **Always send/receive as `document`**, not `video` — Telegram re-compresses
  `video` uploads and would destroy the quality we worked for. Document
  uploads are bit-exact. Bot API file limit is 50 MB down / 2 GB up via a
  local Bot API server — a 95 s iPhone HEVC clip is ~250 MB, so we will
  need `telegram-bot-api` (local server) or have the phone trim/transcode
  first. Plan for the local Bot API server from day one.
- Inline keyboard for the approve/redo loop; keep the raw upload cached
  until approval so redos don't need a re-upload.
- Queue renders (one at a time) — x265 is CPU-heavy.
- Allow-list your own Telegram user ID; this bot should be private.

## Roadmap: automatic audio

The bot should eventually set music too. Two realities to balance:

- **In-app TikTok sounds rank better** — trending sounds feed discovery and
  "use this sound" traffic, and commercial tracks added outside the app can
  get the video muted by copyright detection.
- **Baked-in audio saves time** and lets us sync edits to the beat.

Plan:

1. Keep the built-in synthesized tracks (`remy.music`, zero copyright
   risk) and optionally maintain a small library of royalty-free tracks
   tagged by BPM and mood.
2. Bot picks a track matching the clip duration/energy and mixes it under
   the original audio (music as a bed, original sound kept audible).
3. **Beat-aligned editing** (the killer feature): snap speed-ramp segment
   boundaries to the track's beat grid, so cuts land on the beat —
   instantly looks professionally edited.
4. Approve/redo flow gets a [🎵 Change track] button; "no music" stays an
   option for posts where a trending in-app sound is the better play.

**Stretch ideas:**

- Auto-caption suggestion via a vision model (or OCR for on-screen text),
  proposing 2–3 caption options per upload.
- `--hook` cold-open (see above) as a bot toggle.
- Auto-generate caption/hashtag suggestions per upload.
- Optional 1.05x micro speed-up of ACTION segments — keeps perceived pace
  high without looking sped-up (a common pro-editor trick).
