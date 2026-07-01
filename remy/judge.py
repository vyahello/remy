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
You are a sharp dev-tips creator writing the ON-VIDEO caption — the hook
that burns into the first frame, the line a scroller reads in under a
second and decides to stay for.

Read (view) these frames, sampled in chronological order from one video:
{frames}

1. Work out exactly what the video TEACHES — name the precise tool / app /
   command / subject from the on-screen UI text (never invent one).
2. Write ONE caption in a tips-&-tricks / educational voice: the single
   insight or payoff that makes someone think "I need this". Frame it as a
   tip, a result, or a smarter way to do something — never a vague tease.

Angles that land (pick the one the footage supports):
- the tool + what it saves you: "btop: your whole system at a glance"
- a reframe of a chore: "stop deleting duplicate files by hand"
- a concrete capability: "find every duplicate file in one command"

Hard rules for the caption:
- max {max_chars} characters, plain text, no markdown/backticks, no
  hashtags, no surrounding quotes
- SPECIFIC and useful beats clever or hyped — name the thing, promise the
  payoff; never "check this out", "so satisfying", "game changer"
- no sensational or policy-risky wording (hack/hacking, attack, exploit,
  deauth, crack, bypass, spy, payload, steal, free wifi)
- one or two RELEVANT emoji that match the topic (e.g. 💻 🖥️ ⚡ 📊 🔥 🐧
  ⌨️ 🚀 🛠️) at the end — only if they fit; never forced

Reply with ONLY a JSON object, no other text:
{{"subject": "<what the video teaches, one line>",
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

Write it STRAIGHT, in a tips-&-tricks or educational register depending on
what the footage shows — a crisp tip when there's a clear shortcut/payoff,
a "here's how this works" teach when it's a concept or walkthrough. No
fluff, no hype, no clickbait: just the useful thing, said well.

Read (view) these frames, sampled in chronological order from the
FINISHED video:
{frames}

{caption_note}

First work out, strictly from what the frames SHOW (never invent a tool,
feature, command, result, or step that is not visible): the exact
tool/app/language (read it off the on-screen text), and the single useful
takeaway a viewer could go and apply.

Then produce two things:

1. description — the teaching caption that goes in the TikTok caption box.
   ONE punchy line, ideally ~90-130 chars (hard max ~150). It is the
   "tip" — write it like a sharp dev-tips creator, not documentation.
   Requirements:
   - PLAIN TEXT ONLY. The TikTok box does NOT render Markdown: NO
     backticks, NO asterisks, NO underscores, NO surrounding quotes.
     Write a tool/command name bare — btop, not `btop` or "btop".
   - LEAD with the payoff in plain language — what the viewer gains and
     how they'd use it. Name the tool and what it does FOR them.
     GOOD: "btop is the system monitor your terminal deserves — live CPU,
     RAM and network in one glance, no more juggling top and free 📊"
     TOO DRY (documentation, avoid): "btop is a resource monitor that
     displays CPU, memory, disks, network and processes and has a menu."
   - ONE idea only. Pick the single best takeaway; don't list every
     feature, theme or keybind. Specific beats exhaustive.
   - Real names from the frames, never invented. No vague hype ("check
     this out", "so cool", "game changer", "mind blown") and no fake
     urgency — it's a confident tip, not clickbait.
   - DO NOT paste a raw command, flag string or code snippet into the
     caption ("Try: echo ... | sd ...", "Run: btop -x"). It reads like
     documentation, breaks on TikTok's plain-text box, and the viewer is
     already watching it happen on screen. Instead close on the PAYOFF —
     what it replaces or saves them ("the find-and-replace sed always
     should've been", "one glance instead of three commands", "no more
     escaping regex"). Naming the tool bare so they can search it is fine
     (sd, btop); a full invocation is not.
   - Confident, natural English.
   - EMOJI: use one or two RELEVANT emoji that fit the topic and give the
     caption visual punch (e.g. 💻 🖥️ ⚡ 📊 📈 🔥 🛠️ 🚀 🐧 ⌨️) — place them
     naturally (after a clause or at the end), not forced. Pick ones that
     actually match the content; skip them entirely if none fit. Never
     more than two.
   - No hashtags inside the description (they go in the list below).
2. hashtags — EXACTLY 5 tags (TikTok ranks the first few; more dilutes
   reach), lowercase, no spaces, each starting with '#'. Order by
   relevance, most specific first: the actual tool/topic, then the
   language/domain, then ONE broad educational-reach tag that genuinely
   fits (#tutorial #howto #coding #tech #learnontiktok). Every tag must
   match what's shown. No sensational or policy-risky tags
   (no hack/exploit/attack/etc.). Avoid hashtags TikTok suppresses or
   has retired — including #commandline, #command, #cli — they tank
   reach; reach the same audience with #terminal #linux #programming
   instead. Prefer concrete tags (#rust, #neovim, #bash) over vague ones.

Reply with ONLY a JSON object, no other text:
{{"description": "<the precise, useful teaching caption>",
 "hashtags": ["#tag1", "#tag2", "..."]}}
"""


# A trailing "Try: <command>" / "Run: …" / "Just run: …" tail — a raw
# invocation pasted onto the end of the caption. The viewer is already
# watching it run on screen, so it's noise that reads like documentation;
# strip it as a safety net even when the prompt asks Claude not to add one.
# Requires the imperative label + a colon ("Try:", "Run:", "Just run:") so
# it can't eat ordinary prose ("run ripgrep instead", "use -s for literal
# strings") that happens to contain the verb.
_CMD_TAIL_RE = re.compile(
    r"""[\s.,;:—–-]*                       # trailing separators before tail
        \b(?:just\s+)?(?:try|run|do)(?:\s+this)?\s*:\s   # "Try:" "Just run:"
        \S.*$                              # …the command itself, to EOL
    """,
    re.IGNORECASE | re.VERBOSE,
)


def clean_description(text: str) -> str:
    """Strip Markdown the TikTok caption box would show as literal junk.

    TikTok renders the caption as plain text, so backticks / asterisks and
    wrapping quotes (which Claude sometimes adds around tool names) come
    out as garbage. Drop them; keep underscores so snake_case names like
    solarized_dark survive, and keep emoji. Also strips a trailing
    "Try: <command>" tail (see _CMD_TAIL_RE) — a raw paste the viewer is
    already watching run.
    """
    s = str(text).strip()
    s = s.replace("`", "").replace("*", "")
    s = s.strip().strip('"').strip("“”").strip("'").strip()
    # Pull any trailing emoji off, strip the command tail, reattach them —
    # so "…no escaping. Try: sd 's' '' 🦀" keeps the 🦀 but loses the paste.
    i = len(s)
    while i > 0 and (s[i - 1].isspace() or ord(s[i - 1]) > 0x2600):
        i -= 1
    core, trailing = s[:i].rstrip(), s[i:].strip()
    stripped = _CMD_TAIL_RE.sub("", core).strip()
    if stripped:  # don't let the tail eat the whole caption
        core = stripped
    s = f"{core} {trailing}".strip() if trailing else core
    return re.sub(r"[ \t]{2,}", " ", s).strip()


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
    return {"description": clean_description(reply.get("description", "")),
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
- a command-line recorder's OWN startup banner printed into the terminal
  before the demo begins — lines like a script name (record_*.sh), a
  "Recording…" / "Press q to stop" prompt, capture settings (fps, "9:16
  slice", "lanczos-scaled"), or the output file path. The real demo
  starts at the FIRST clean shell prompt after that banner;
- a long idle gap before the real app/terminal is actually used;
- trailing frames after the thing being demonstrated has closed (the app
  quit, a help dump, the recorder UI reappearing) — and a tail of EMPTY
  shell prompts after the last command's output: end on the frame that
  still shows the final result, not on a bare blinking prompt.

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


MISTAKE_PROMPT = CREATOR_CONTEXT + """
You are a TikTok editor cleaning up a LIVE-CODING / terminal screen
recording before it ships. Each frame is labelled with its timestamp in
seconds (the t…s in the filename).

Frames sampled in order from the {duration:.0f}s recording:
{frames}

Find the spots where the creator FUMBLED and the recording should jump
past them, so the final cut shows only clean, working coding. A fumble is
something the creator would be embarrassed to leave in, for example:
- a mistyped command or path, then a correction or retype of the same
  thing right after;
- an error the command threw — "command not found", "No such file or
  directory", a red error line, a stack trace / Traceback, a compiler or
  syntax error — followed by the fixed version that works;
- a wrong turn that gets undone: a file opened then closed, output that's
  abandoned, a dead end the creator backs out of.

For each fumble, report the SOURCE-second span to DELETE: from where the
mistake starts to just before the successful retry / recovery begins. Keep
the good take — cut the bad attempt and the error, not the fix.

Be CONSERVATIVE. This is the creator's real work; most of it is correct
and must stay. Only flag a span when the frames clearly show a mistake and
its recovery — never guess, never cut a command that simply takes a moment
or produces normal (non-error) output. If nothing is clearly a mistake,
return an empty list. Never flag more than half the recording.

Reply with ONLY a JSON object, no other text:
{{"cuts": [{{"start": <seconds>, "end": <seconds>, "reason": "<short>"}}]}}
"""

MISTAKE_MIN_SPAN = 0.4    # ignore spans shorter than this (sampling noise)
MISTAKE_MAX_FRACTION = 0.5  # never delete more than half the recording
MISTAKE_MAX_FRAMES = 24   # cap the sampled frames so the prompt stays cheap


def clean_cut_spans(
    reply: dict, duration: float
) -> list[tuple[float, float]]:
    """Turn Claude's {cuts:[…]} into safe, sorted source-second spans.

    Clamps each span to the clip, drops nonsense (reversed, zero-length,
    sub-MISTAKE_MIN_SPAN), merges overlaps, and caps the TOTAL removed at
    MISTAKE_MAX_FRACTION of the duration so an over-eager reply can never
    gut the video. Returns [] whenever nothing survives.
    """
    raw = reply.get("cuts", [])
    if not isinstance(raw, list):
        return []
    spans: list[tuple[float, float]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            start = float(item.get("start", 0))
            end = float(item.get("end", 0))
        except (TypeError, ValueError):
            continue
        start = max(0.0, min(start, duration))
        end = max(0.0, min(end, duration))
        if end - start >= MISTAKE_MIN_SPAN:
            spans.append((round(start, 2), round(end, 2)))
    if not spans:
        return []
    spans.sort()
    merged: list[list[float]] = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    budget = duration * MISTAKE_MAX_FRACTION
    out: list[tuple[float, float]] = []
    used = 0.0
    for a, b in merged:
        if used + (b - a) > budget:
            break  # too aggressive past here — trust the rest to the creator
        used += b - a
        out.append((a, b))
    return out


def detect_mistakes(
    video: str, duration: float
) -> list[tuple[float, float]]:
    """Find source-second spans of mistyped commands / errors to delete.

    Samples the recording densely (≈1 fps, capped at MISTAKE_MAX_FRAMES)
    and has Claude flag the fumbles — a bad command, the error it threw,
    the dead end — so the edit can drop them and ship only clean live
    coding. Best-effort: returns [] when nothing clear is found. Raises
    JudgeUnavailable / ValueError on a hard failure (the caller treats it
    as best-effort).
    """
    n = min(MISTAKE_MAX_FRAMES, max(N_FRAMES, round(duration)))
    tmp = tempfile.mkdtemp(prefix="remy_mistakes_")
    try:
        frames = extract_frames(
            video, spread_times(duration, n=n, margin=0.0), tmp)
        prompt = MISTAKE_PROMPT.format(
            frames="\n".join(frames), duration=duration)
        reply = parse_json_obj(run_claude(prompt))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return clean_cut_spans(reply, duration)


SECTION_PROMPT = CREATOR_CONTEXT + """
You are a TikTok editor writing the CHANGING on-screen captions for a
coding / build video — a short label per section that narrates what the
viewer is watching right then, like a guided walkthrough. Each frame is
labelled with its timestamp in seconds (the t…s in the filename).

Frames sampled in order from the {duration:.0f}s recording:
{frames}

Break the video into its natural steps (a handful — 4 to 7) and, for each,
write ONE short caption plus the SOURCE-second where that step begins:
- read the on-screen UI / code to name what actually happens ("Require the
  display", "Draw Hello World", "Push it to the device", "Runs on
  hardware") — never invent a step you can't see in the frames;
- each label ≤ {max_chars} characters, plain text, a crisp tips/tutorial
  voice, no hashtags / quotes / markdown;
- one or two relevant emoji only if they fit; never sensational or
  policy-risky words (hack, attack, exploit, deauth, crack, bypass, spy,
  payload, steal, free wifi);
- the FIRST step starts at 0; steps in ascending time order.

Reply with ONLY a JSON object, no other text:
{{"sections": [{{"start": <seconds>, "label": "<short caption>"}}]}}
"""

SECTION_MAX = 7          # cap the number of on-screen labels
SECTION_MAX_FRAMES = 16  # cap sampled frames so the prompt stays cheap


def clean_sections(
    reply: dict, duration: float
) -> list[tuple[float, str]]:
    """Turn Claude's {sections:[…]} into safe (source-second, label) pairs.

    Clamps each start into the clip, drops empty / over-long / policy-risky
    labels (`check_caption`), sorts by time, collapses repeats, and caps the
    count at SECTION_MAX. Returns [] when nothing usable survives so the
    caller can fall back to a single static caption.
    """
    raw = reply.get("sections", [])
    if not isinstance(raw, list):
        return []
    out: list[tuple[float, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        label = item.get("label")
        if not isinstance(label, str) or not label.strip():
            continue
        label = label.strip()
        if len(label) > MAX_CAPTION_CHARS or check_caption(label):
            continue
        try:
            start = float(item.get("start", 0))
        except (TypeError, ValueError):
            continue
        out.append((max(0.0, min(start, duration)), label))
    out.sort(key=lambda x: x[0])
    dedup: list[tuple[float, str]] = []
    for st, lbl in out:
        if dedup and dedup[-1][1] == lbl:
            continue
        dedup.append((round(st, 2), lbl))
    return dedup[:SECTION_MAX]


def detect_sections(
    video: str, duration: float
) -> list[tuple[float, str]]:
    """Label the video's natural steps for dynamic (changing) captions.

    Samples frames across the clip and has Claude break it into a handful of
    ordered steps, each a short caption + the source-second it begins. The
    caller maps those source times to output time (`analysis.caption_windows`)
    and renders one label at a time. Best-effort: returns [] on an unusable
    reply (the caller falls back to the single static caption). Raises
    JudgeUnavailable / ValueError on a hard failure.
    """
    n = min(SECTION_MAX_FRAMES, max(N_FRAMES, round(duration / 20)))
    tmp = tempfile.mkdtemp(prefix="remy_sections_")
    try:
        frames = extract_frames(
            video, spread_times(duration, n=n, margin=0.0), tmp)
        prompt = SECTION_PROMPT.format(
            frames="\n".join(frames), duration=duration,
            max_chars=MAX_CAPTION_CHARS)
        reply = parse_json_obj(run_claude(prompt))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return clean_sections(reply, duration)


MAX_HASHTAGS = 5  # TikTok ranks only the leading few — keep it tight

# Hashtags TikTok suppresses, has retired, or that draw a moderation cloud
# over the whole post — they look topical but tank reach, so never emit
# them even if Claude proposes one. Matched on the bare (lowercased,
# punctuation-stripped) tag. Keep this dev/tech-focused and conservative:
# only tags that are genuinely dead weight, never merely broad ones.
BLOCKED_HASHTAGS = frozenset({
    "commandline", "command", "cli",   # the report: #commandline is dead
    "fyp", "foryou", "foryoupage",     # spammy, ignored, can look botted
    "viral", "trending", "follow", "followme", "like4like", "follow4follow",
})


def clean_hashtags(raw: object) -> list[str]:
    """Normalize, de-dupe, moderation-filter and cap Claude's hashtags.

    Order is preserved (Claude is told to put the most relevant first),
    so the cap keeps the strongest MAX_HASHTAGS tags. Tags in
    BLOCKED_HASHTAGS or that trip the caption moderation check are dropped
    — a suppressed/flagged tag hurts reach the same way the description
    wording does.
    """
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        tag = re.sub(r"\s+", "", str(item)).lstrip("#")
        tag = re.sub(r"[^0-9A-Za-z_]", "", tag).lower()
        if not tag or not re.search(r"[a-z]", tag) or len(out) >= MAX_HASHTAGS:
            continue  # need at least one letter — "#1" is useless
        if tag in BLOCKED_HASHTAGS:
            continue  # TikTok-suppressed / spammy — drop, never substitute
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
