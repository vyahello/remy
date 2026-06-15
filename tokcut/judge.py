"""Claude Code judgment layer: caption writing and output review.

Division of labor: Python does everything deterministic (frame extraction,
prompts, parsing, validation); Claude Code — running headless on the
subscription OAuth token (`claude setup-token` → CLAUDE_CODE_OAUTH_TOKEN,
or an interactive login on a dev machine) — does the judgment: reading
the frames, deciding what the video shows, wording the caption, and
reviewing the rendered result.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile

from .caption import MAX_CAPTION_CHARS, check_caption

CLAUDE_TIMEOUT_SEC = 240
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
               timeout: int = CLAUDE_TIMEOUT_SEC) -> str:
    """Run Claude Code headless; return its final text reply.

    Auth comes from the environment: CLAUDE_CODE_OAUTH_TOKEN (subscription
    token from `claude setup-token`) or an existing `claude` login.
    """
    if not claude_available():
        raise JudgeUnavailable("claude CLI not found on PATH")
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
    tmp = tempfile.mkdtemp(prefix="tokcut_judge_")
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
You are the creator writing the post copy for a finished TikTok video.
This is paste-ready text for the TikTok caption box — NOT an on-video
overlay and NOT a review.

Read (view) these frames, sampled in chronological order from the
FINISHED video:
{frames}

{caption_note}

Produce two things, both STRICTLY grounded in what the frames actually
show — never invent a tool, feature, result, or step that is not
visible. Identify the exact app/tool/language from on-screen text.

1. description — one or two short, fun sentences (max ~150 chars) that
   say what the viewer is watching. Energetic but accurate; at most two
   emoji. No hashtags inside it.
2. hashtags — EXACTLY 5 tags (TikTok ranks the first few; more dilutes
   reach), lowercase, no spaces, each starting with '#'. Order by
   relevance, most specific first: the actual tool/topic the video is
   about, then one or two broader reach tags (#coding #tech). Every tag
   must genuinely match what's shown. No sensational or policy-risky tags
   (no hack/exploit/attack/etc.).

Reply with ONLY a JSON object, no other text:
{{"description": "<the fun, accurate description>",
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
    tmp = tempfile.mkdtemp(prefix="tokcut_post_")
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
- caption_pos: "auto" (calmest spot), "top", "bottom"
- style: caption look — "purple" (purple on white, the default),
  "yellow" (black on yellow), "black" (white on black)
- hook: cold-open teaser of the best beat (true/false)
- crop: auto-zoom into the action, dropping static margins (true/false)
- zoom: framing dial, a number — 1.0 = auto framing; higher zooms
  tighter into the action, lower pulls wider. Adjust in steps of about
  0.15-0.3 from the current value ("closer"/"tighter"/"can't read it"
  goes up, "wider"/"too cropped"/"show more" goes down; range 0.5-2.5)
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
 "zoom": null, "look": null, "keep_audio": null, "music": null,
 "music_bpm": null, "new_music_mix": false,
 "reply": "<one short line telling the creator what you'll change>"}}

Change only what the feedback implies — when in doubt, change less.
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
