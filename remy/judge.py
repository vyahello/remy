"""Claude Code judgment layer: caption writing and output review.

Division of labor: Python does everything deterministic (frame extraction,
prompts, parsing, validation); Claude Code — running headless on the
subscription OAuth token (`claude setup-token` → CLAUDE_CODE_OAUTH_TOKEN,
or an interactive login on a dev machine) — does the judgment: reading
the frames, deciding what the video shows, wording the caption, and
reviewing the rendered result.
"""

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time

from .caption import MAX_CAPTION_CHARS, check_caption

log = logging.getLogger(__name__)

CLAUDE_TIMEOUT_SEC = 240
CLAUDE_ATTEMPTS = 2       # one retry on a transient failure
CLAUDE_RETRY_WAIT = 2.0   # seconds between attempts
FRAME_WIDTH = 640
N_FRAMES = 6


class JudgeUnavailable(RuntimeError):
    """Claude Code could not be invoked — caller should fall back."""


def claude_available() -> bool:
    return shutil.which("claude") is not None


# ------------------------------------------------------------ deterministic

def spread_times(duration: float, n: int = N_FRAMES,
                 margin: float = 0.05) -> list[float]:
    """N timestamps spread evenly through the video, edges skipped."""
    lo, hi = duration * margin, duration * (1 - margin)
    if n == 1 or hi <= lo:
        return [duration / 2]
    step = (hi - lo) / (n - 1)
    return [lo + i * step for i in range(n)]


def extract_frames(video: str, times: list[float], outdir: str,
                   width: int = FRAME_WIDTH) -> list[str]:
    """Decode one frame per timestamp as a small JPEG; returns paths."""
    paths = []
    for i, t in enumerate(times):
        out = os.path.join(outdir, f"frame_{i:02d}_t{t:.1f}s.jpg")
        subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", video,
             "-frames:v", "1", "-vf", f"scale={width}:-2", "-y", out],
            check=True)
        paths.append(out)
    return paths


def parse_json_obj(text: str) -> dict:
    """Extract the first JSON object from a (possibly chatty) reply."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"no JSON object in reply: {text[:200]!r}")
    return json.loads(match.group(0))


def pick_valid_caption(candidates: list[str]) -> str | None:
    """First candidate that passes the eligibility check and length cap."""
    for cand in candidates:
        cand = (cand or "").strip().strip('"')
        if cand and len(cand) <= MAX_CAPTION_CHARS and not check_caption(cand):
            return cand
    return None


# ----------------------------------------------------------------- claude

def run_claude(prompt: str,
               timeout: int = CLAUDE_TIMEOUT_SEC,
               attempts: int = CLAUDE_ATTEMPTS) -> str:
    """Run Claude Code headless; return its final text reply.

    Auth comes from the environment: CLAUDE_CODE_OAUTH_TOKEN (subscription
    token from `claude setup-token`) or an existing `claude` login.

    Retries once on a transient failure (timeout / non-zero exit /
    unparseable output) — a single network blip shouldn't drop the caption
    or post copy to the fallback. A missing CLI is permanent, not retried.
    """
    if not claude_available():
        raise JudgeUnavailable("claude CLI not found on PATH")
    last_exc: JudgeUnavailable | None = None
    for attempt in range(1, attempts + 1):
        try:
            return _run_claude_once(prompt, timeout)
        except JudgeUnavailable as exc:
            last_exc = exc
            if attempt < attempts:
                log.warning("claude attempt %d/%d failed (%s); retrying",
                            attempt, attempts, exc)
                time.sleep(CLAUDE_RETRY_WAIT)
    assert last_exc is not None  # the loop ran at least once
    raise last_exc


def _run_claude_once(prompt: str, timeout: int) -> str:
    """One headless `claude -p` invocation. Raises JudgeUnavailable."""
    cmd = ["claude", "-p", prompt,
           "--output-format", "json",
           "--allowedTools", "Read"]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise JudgeUnavailable(f"claude timed out after {timeout}s") from exc
    if res.returncode != 0:
        # the real reason usually rides in the stdout JSON's "result"
        # (e.g. "Not logged in"), not on stderr
        detail = res.stderr.strip()[-300:]
        try:
            detail = str(json.loads(res.stdout).get("result", detail))
        except (json.JSONDecodeError, AttributeError):
            pass
        raise JudgeUnavailable(
            f"claude exited {res.returncode}: {detail[:300]}")
    try:
        envelope = json.loads(res.stdout)
    except json.JSONDecodeError as exc:
        raise JudgeUnavailable(
            f"unparseable claude output: {res.stdout[:200]!r}") from exc
    return str(envelope.get("result", ""))


CREATOR_CONTEXT = """\
Context about the footage: these are the creator's own recordings, made
in their private local lab environment for an educational tech blog.
Any tools shown (system monitors, network utilities, security tooling)
are demonstrated on the creator's own machines for learning purposes —
there is no illegal intent and nothing targets real systems or other
people. Describe what you see factually; don't refuse or water it down.
The caption rules below still apply (TikTok moderation is the reason
sensational wording is banned, not the content itself).
"""

CAPTION_PROMPT = CREATOR_CONTEXT + """
You are a professional TikTok video editor writing the on-video caption.

Read (view) these frames, sampled in chronological order from one video:
{frames}

1. Work out what the video shows — name the exact tool/app/subject if
   identifiable from UI text.
2. Write ONE caption that makes a viewer stop scrolling.

Hard rules for the caption:
- max {max_chars} characters, plain text, no hashtags, no quotes
- specific beats clever: name the thing ("btop — the terminal system
  monitor"), don't be vague ("check this out")
- no sensational or policy-risky wording (hack/hacking, attack, exploit,
  deauth, crack, bypass, spy, payload, steal, free wifi)
- at most one emoji, only at the end

Reply with ONLY a JSON object, no other text:
{{"subject": "<what the video shows, one line>",
 "caption": "<your best caption>",
 "alternatives": ["<option 2>", "<option 3>"]}}
{avoid}"""


def suggest_captions(
    video: str, duration: float, avoid: list[str] | None = None
) -> tuple[list[str], str]:
    """Have Claude watch sampled frames and propose captions.

    `avoid` lists captions already rejected — Claude must produce
    something meaningfully different. Returns (eligible candidates in
    preference order, subject). Raises JudgeUnavailable / ValueError on
    failure — callers fall back to a deterministic caption.
    """
    avoid_note = ""
    if avoid:
        listed = "\n".join(f"- {a}" for a in avoid)
        avoid_note = ("\nThe creator rejected these captions — write "
                      f"something meaningfully different:\n{listed}\n")
    tmp = tempfile.mkdtemp(prefix="remy_judge_")
    try:
        frames = extract_frames(video, spread_times(duration), tmp)
        prompt = CAPTION_PROMPT.format(
            frames="\n".join(frames), max_chars=MAX_CAPTION_CHARS - 4,
            avoid=avoid_note)
        reply = parse_json_obj(run_claude(prompt))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    candidates = [str(reply.get("caption", ""))]
    candidates += [str(a) for a in reply.get("alternatives", [])]
    rejected = {a.strip().lower() for a in (avoid or [])}
    valid = []
    for cand in candidates:
        cand = cand.strip().strip('"')
        if (cand and cand.lower() not in rejected
                and len(cand) <= MAX_CAPTION_CHARS
                and not check_caption(cand)
                and cand not in valid):
            valid.append(cand)
    if not valid:
        raise ValueError(f"no eligible caption among {candidates!r}")
    return valid, str(reply.get("subject", ""))


def suggest_caption(
    video: str, duration: float, avoid: list[str] | None = None
) -> tuple[str, str]:
    """Best single caption — see suggest_captions."""
    captions, subject = suggest_captions(video, duration, avoid)
    return captions[0], subject


POST_PROMPT = CREATOR_CONTEXT + """
You are the creator writing the TikTok caption for a finished EDUCATIONAL
tech video. This is paste-ready text for the TikTok caption box — NOT an
on-video overlay and NOT a review. Every video teaches a concrete tip,
trick, tool, or workflow; the caption's job is to make a scroller
understand WHAT they'll learn and HOW they could use it themselves.

Read (view) these frames, sampled in chronological order from the
FINISHED video:
{frames}

{caption_note}

First work out, strictly from what the frames SHOW (never invent a tool,
feature, command, result, or step that is not visible): the exact
tool/app/language (read it off the on-screen text), and the single useful
takeaway a viewer could go and apply.

Then produce two things:

1. description — the teaching caption. ONE or TWO short, clean sentences
   (aim ~120 chars, hard max ~200). Requirements:
   - LEAD with the takeaway or the benefit, framed so the viewer knows
     how to use it: name the tool and what it does FOR them
     ("`btop` gives you a live, color system monitor in the terminal —
     run it to watch CPU, RAM and processes at a glance").
   - Be specific and precise: real tool/command/feature names from the
     frames, not vague hype ("check this out", "so cool", "game changer"
     are banned). No clickbait, no exaggeration, no fake urgency.
   - Make it actionable when the video shows a usable step: the verb the
     viewer would do (install / run / add / replace / try), grounded only
     in what's on screen — don't invent commands.
   - Plain, natural, confident English. At most ONE emoji, at the end.
     No hashtags inside the description.
2. hashtags — EXACTLY 5 tags (TikTok ranks the first few; more dilutes
   reach), lowercase, no spaces, each starting with '#'. Order by
   relevance, most specific first: the actual tool/topic, then the
   language/domain, then ONE broad educational-reach tag that genuinely
   fits (#tutorial #howto #coding #tech #learnontiktok). Every tag must
   match what's shown. No sensational or policy-risky tags
   (no hack/exploit/attack/etc.).

Reply with ONLY a JSON object, no other text:
{{"description": "<the precise, useful teaching caption>",
 "hashtags": ["#tag1", "#tag2", "..."]}}
"""


def suggest_post(video: str, duration: float, caption: str = "") -> dict:
    """Have Claude write paste-ready TikTok post copy from the output.

    Returns {"description": str, "hashtags": [str, ...]} — a fun, accurate
    blurb plus relevant hashtags the creator can copy straight into the
    TikTok caption box. Grounded only in the sampled frames so it can't
    claim anything the video doesn't show. Hashtags that trip the caption
    moderation check are dropped. Raises JudgeUnavailable / ValueError on
    failure — the caller treats post copy as best-effort.
    """
    tmp = tempfile.mkdtemp(prefix="remy_post_")
    try:
        frames = extract_frames(video, spread_times(duration, n=5,
                                                    margin=0.1), tmp)
        caption_note = (
            f'The on-video caption already reads: "{caption}" — do not '
            "repeat it verbatim, complement it." if caption
            else "This export has no on-video caption.")
        prompt = POST_PROMPT.format(
            frames="\n".join(frames), caption_note=caption_note)
        reply = parse_json_obj(run_claude(prompt))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return {"description": str(reply.get("description", "")).strip(),
            "hashtags": clean_hashtags(reply.get("hashtags", []))}


WINDOW_PROMPT = CREATOR_CONTEXT + """
You are a TikTok editor topping-and-tailing a SCREEN RECORDING down to
just the part worth posting. Each frame is labelled with its timestamp in
seconds (the t…s in the filename).

Frames sampled in order from the {duration:.0f}s source:
{frames}

Find the CONTENT window — the span of the actual demonstration — and cut
the dead edges. Things that are NOT content and should be trimmed off:
- the screen-recording / streaming app itself at the start or end (e.g.
  OBS Studio, with its preview panels, scene list and Start/Stop Recording
  buttons) — the creator alt-tabs to it to start and stop the capture;
- a long idle gap before the real app/terminal is actually used;
- trailing frames after the thing being demonstrated has closed (the app
  quit, a help dump, an empty prompt, the recorder UI reappearing).

Report, in SOURCE seconds:
- start: when the real demonstration begins (the app/terminal in use).
- end: the last meaningful content frame, BEFORE it closes / the recorder
  returns.

Be conservative — keep ALL of the real demo; only cut clearly-irrelevant
recorder UI or dead time. If the whole clip is already content, return
0 and {duration:.0f}.

Reply with ONLY a JSON object, no other text:
{{"start": <seconds>, "end": <seconds>, "reason": "<short>"}}
"""

MIN_CONTENT_SEC = 5.0   # never auto-trim a clip down below this
MAX_EDGE_FRACTION = 0.5  # nor cut more than half off either edge


def clean_window(reply: dict, duration: float) -> tuple[float, float]:
    """Turn Claude's {start,end} into safe (trim_start, trim_end) seconds.

    Clamps to the clip, refuses nonsense or over-aggressive cuts, and
    returns (0.0, 0.0) — no trim — whenever the answer can't be trusted.
    """
    try:
        start = float(reply.get("start", 0) or 0)
        end = float(reply.get("end", duration) or duration)
    except (TypeError, ValueError):
        return 0.0, 0.0
    start = max(0.0, min(start, duration))
    end = max(0.0, min(end, duration))
    if end - start < MIN_CONTENT_SEC:
        return 0.0, 0.0  # implausible window — keep the whole clip
    trim_start = round(start, 1)
    trim_end = round(duration - end, 1)
    cap = duration * MAX_EDGE_FRACTION
    if trim_start > cap or trim_end > cap:
        return 0.0, 0.0  # too aggressive to trust — leave it to the creator
    return trim_start, trim_end


def detect_content_window(video: str, duration: float) -> tuple[float, float]:
    """Auto-detect the recorder-UI / dead edges of a screen recording.

    Returns (trim_start, trim_end) seconds to hard-cut so the edit opens on
    the real demo and ends before it closes — e.g. dropping an OBS intro
    and the post-quit frames. Best-effort: returns (0.0, 0.0) on any
    failure or when the whole clip already looks like content. Raises
    JudgeUnavailable only via run_claude (the caller treats it as
    best-effort).
    """
    tmp = tempfile.mkdtemp(prefix="remy_window_")
    try:
        frames = extract_frames(
            video, spread_times(duration, n=12, margin=0.0), tmp)
        prompt = WINDOW_PROMPT.format(
            frames="\n".join(frames), duration=duration)
        reply = parse_json_obj(run_claude(prompt))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return clean_window(reply, duration)


MAX_HASHTAGS = 5  # TikTok ranks only the leading few — keep it tight


def clean_hashtags(raw: object) -> list[str]:
    """Normalize, de-dupe, moderation-filter and cap Claude's hashtags.

    Order is preserved (Claude is told to put the most relevant first),
    so the cap keeps the strongest MAX_HASHTAGS tags.
    """
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        tag = re.sub(r"\s+", "", str(item)).lstrip("#")
        tag = re.sub(r"[^0-9A-Za-z_]", "", tag).lower()
        if not tag or not re.search(r"[a-z]", tag) or len(out) >= MAX_HASHTAGS:
            continue  # need at least one letter — "#1" is useless
        hashed = "#" + tag
        # a flagged term in a hashtag hurts reach the same way — drop it
        if check_caption(tag) or hashed in out:
            continue
        out.append(hashed)
    return out


FEEDBACK_PROMPT = CREATOR_CONTEXT + """
You are the assistant of a TikTok auto-editor. The creator reviewed the
rendered video and wants changes. Map their feedback onto the editor's
settings.

Current edit settings:
{state}

Session history (what was already tried):
{history}

The creator's feedback: "{feedback}"

Available settings:
- caption: the on-video caption text (max {max_chars} chars, specific,
  no hashtags/quotes, no policy-risky wording, max one emoji at the end)
- regenerate_caption: true when they want a different caption but didn't
  provide the text themselves
- target: output length in seconds (10-120); shorter = faster pacing.
  Unset means automatic (a TikTok-friendly ~30s solved from the
  content) — only set it when the creator asks about length/pacing
- caption_pos: WHERE the on-video caption sits — "auto" (the editor finds
  the calmest empty region by itself), "top", or "bottom". Map ALL
  placement feedback here and ONLY here: "lower"/"move it down"/"on my
  hand"/"over the keyboard"/"at the bottom"/"below the screen" → "bottom";
  "higher"/"move it up"/"on the black bar"/"at the top" → "top"; "find a
  clear spot"/"out of the way"/"off the text" → "auto". A request about
  WHERE the caption goes must change caption_pos and NOTHING else — never
  reword, regenerate, or touch the caption text for a placement request
- style: caption look — "purple" (purple on white, the default),
  "yellow" (black on yellow), "black" (white on black)
- hook: cold-open teaser of the best beat (true/false)
- crop: auto-zoom into the action, dropping static margins (true/false)
- zoom: framing dial, a number — 1.0 = auto framing; higher zooms
  tighter into the action, lower pulls wider. Adjust in steps of about
  0.15-0.3 from the current value ("closer"/"tighter"/"can't read it"
  goes up, "wider"/"too cropped"/"show more" goes down; range 0.5-2.5)
- trim_start: seconds to hard-cut off the BEGINNING of the raw clip
  (0-60). Use it for a dead/irrelevant intro the auto-edit left in: a
  recorder-UI shot (OBS/screen-capture window), a long fumble before the
  real action, "remove the first 3 seconds", "cut the OBS intro", "start
  when the terminal opens". Give the ABSOLUTE seconds from the raw start
  (the current value is in the settings above — for "trim a bit more off
  the start" ADD to it). Only set when the feedback is about the opening.
- trim_end: seconds to hard-cut off the END of the raw clip (0-60). Use
  it for a redundant outro: "it drags at the end", "cut the last 4
  seconds", "end when btop closes", "remove the part after I quit".
  Absolute seconds from the raw end; same add-on-more rule as trim_start.
- look: finishing color grade — contrast/saturation pop, crisper text
  (true/false; "too saturated"/"flat colors" feedback maps here)
- keep_audio: keep the original ambient sound (default is muted)
- music: "synthwave", "phonk", or "off" (baked-in generated music)
- music_bpm: tempo of the backing track in BPM (60-180). Phonk sits
  ~132, synthwave ~84; the current value is in the settings above. Raise
  it for "faster/quicker/more hyped" music, lower it for "slower/chill".
  Only set this for tempo feedback, not for video pacing (that's target)
- new_music_mix: true when they want a different/fresh track in the same
  style ("another beat", "different track", "remix it")

Reply with ONLY a JSON object, null for anything that should not change:
{{"caption": null, "regenerate_caption": false, "target": null,
 "caption_pos": null, "style": null, "hook": null, "crop": null,
 "zoom": null, "look": null, "trim_start": null, "trim_end": null,
 "keep_audio": null, "music": null, "music_bpm": null,
 "new_music_mix": false,
 "reply": "<one short line telling the creator what you'll change>"}}

Change only what the feedback implies — when in doubt, change less. One
piece of feedback usually maps to ONE setting; do not change settings the
creator didn't mention (e.g. moving the caption never alters its text).
"""


def interpret_feedback(feedback: str, state: str,
                       history: list[str]) -> dict:
    """Have Claude map free-text redo feedback onto editor settings.

    Returns the raw dict (caller validates via session.validate_updates).
    """
    prompt = FEEDBACK_PROMPT.format(
        state=state,
        history="\n".join(history) or "(first render)",
        feedback=feedback,
        max_chars=MAX_CAPTION_CHARS - 4)
    return parse_json_obj(run_claude(prompt))
