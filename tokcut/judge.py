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


REVIEW_PROMPT = CREATOR_CONTEXT + """
You are a professional TikTok editor reviewing a finished vertical
(1080x1920) edit before it is posted.

Read (view) these frames, sampled in chronological order from the
FINISHED video:
{frames}

{caption_note}
The first frame listed IS the video's opening (the cold-open hook).

Check, strictly:
1. Any expected caption is fully visible, legible, and not covering the
   action (skip this check when there is intentionally no caption).
2. The opening frame works as a scroll-stopping hook (action, not setup).
3. The content itself is readable/judgeable at phone size.
4. The final frame ends on something worth seeing (not a desktop/cutoff).

The editor can fix: caption text, caption position, output length,
hook on/off, auto-zoom on/off. It cannot increase source resolution or
re-record. Say "redo" ONLY for problems those controls can fix; put
source-quality limitations in "notes" instead.

Reply with ONLY a JSON object, no other text:
{{"verdict": "approve" or "redo",
 "issues": ["<each concrete problem, if any>"],
 "notes": "<one-line overall impression>"}}
"""


def review_output(video: str, duration: float, caption: str) -> dict:
    """Have Claude review the rendered output. Returns the verdict dict."""
    tmp = tempfile.mkdtemp(prefix="tokcut_review_")
    try:
        # first sample inside the opening second — that's the hook frame
        times = [min(0.4, duration / 10)] + spread_times(
            duration, n=4, margin=0.2)
        frames = extract_frames(video, times, tmp)
        caption_note = (
            f'The on-video caption should read: "{caption}"' if caption
            else "There is intentionally NO on-video caption (landscape "
                 "export — the creator overlays their own).")
        prompt = REVIEW_PROMPT.format(
            frames="\n".join(frames), caption_note=caption_note)
        reply = parse_json_obj(run_claude(prompt))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    verdict = str(reply.get("verdict", "")).lower()
    if verdict not in ("approve", "redo"):
        raise ValueError(f"unexpected verdict: {reply!r}")
    return {"verdict": verdict,
            "issues": [str(i) for i in reply.get("issues", [])],
            "notes": str(reply.get("notes", ""))}


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
- keep_audio: keep the original ambient sound (default is muted)
- music: "synthwave", "phonk", or "off" (baked-in generated music)

Reply with ONLY a JSON object, null for anything that should not change:
{{"caption": null, "regenerate_caption": false, "target": null,
 "caption_pos": null, "style": null, "hook": null, "crop": null,
 "keep_audio": null, "music": null,
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
