"""Telegram bot entry point — step 4: approve/redo loop.

Python's job: Telegram I/O, allow-list, downloads, session state, running
the edit pipeline (queued, in a worker thread), validating every parameter
change. Claude Code's job (subscription OAuth): watching frames to write
the caption, writing the TikTok post copy, and interpreting free-text
redo feedback into setting changes.
"""

import asyncio
import contextlib
import html
import logging
import os

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from ..analysis import probe
from ..caption import check_caption
from ..cli import edit
from ..judge import (
    claude_available,
    interpret_feedback,
    suggest_caption,
    suggest_captions,
    suggest_post,
)
from .config import BotConfig, is_allowed, load_config
from .pipeline import (
    delivery_name,
    friendly_progress,
    sweep_workdir,
)
from .session import (
    EditSession,
    apply_updates,
    cleanup_files,
    fallback_updates,
    tweak_updates,
    validate_updates,
)

log = logging.getLogger("tokcut.bot")

APPROVE = "approve"
REDO = "redo"
TWEAK = "tweak:"
# setup-phase (pre-render picker) callbacks
SETCAP = "setcap:"   # pick caption idea i
OWNCAP = "owncap"    # prompt to type a caption
NOCAP = "nocap"      # bake no caption
OPT = "opt:"         # toggle a setup option (reuses tweak_updates keys)
RENDER = "render"    # leave setup, render the first take
VERDICT_KEYBOARD = InlineKeyboardMarkup([[
    InlineKeyboardButton("✅ Approve", callback_data=APPROVE),
    InlineKeyboardButton("🔁 Redo", callback_data=REDO),
]])


def _short(text: str, n: int = 26) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def format_post_kit(post: dict) -> str:
    """The paste-ready TikTok caption body (description + hashtags).

    Returned bare (no heading) so it can be sent inside a copyable block.
    """
    desc = (post.get("description") or "").strip()
    tags = " ".join(post.get("hashtags") or [])
    return "\n\n".join(part for part in (desc, tags) if part)


def setup_text(session: EditSession) -> str:
    """The pre-render setup summary shown above the picker keyboard."""
    p = session.params
    if not session.vertical:
        cap = "🖥️ landscape — no baked caption (overlay your own)"
    elif session.caption:
        cap = f"caption: “{session.caption}”"
    else:
        cap = "caption: none"
    length = "auto" if p.target is None else f"~{p.target:.0f}s"
    return (
        "🎬 *Setup* — pick what you want, then tap *Render*.\n"
        f"{cap}\n"
        f"cold open {'on' if p.hook else 'off'} · length {length} · "
        f"zoom {'on' if p.crop else 'off'} · look "
        f"{'on' if p.look else 'off'} · music {p.music_style or 'muted'}")


def setup_keyboard(session: EditSession) -> InlineKeyboardMarkup:
    """Pre-render picker: caption choice (vertical) + the option toggles.

    Caption ideas pre-selected with ✅; options the user picked before
    rendering — cold open (default off), length, zoom/look, music.
    """
    p = session.params
    rows: list[list[InlineKeyboardButton]] = []

    def opt(label: str, key: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(label, callback_data=OPT + key)

    # one rule everywhere: a leading ✅ marks the choice that is active
    # right now; everything else shows its own icon. No empty checkboxes.
    def pick(active: bool, icon: str, name: str) -> str:
        return ("✅ " if active else icon + " ") + name

    if session.vertical:
        for i, idea in enumerate(session.caption_choices[:3]):
            # full-width row — show the whole caption, not a 26-char clip
            rows.append([InlineKeyboardButton(
                pick(session.caption == idea, "💬", _short(idea, 58)),
                callback_data=SETCAP + str(i))])
        own = bool(session.caption
                   and session.caption not in session.caption_choices)
        rows.append([
            InlineKeyboardButton(pick(own, "✍️", "Type my own"),
                                 callback_data=OWNCAP),
            InlineKeyboardButton(pick(not session.caption, "🚫", "No caption"),
                                 callback_data=NOCAP)])

    # booleans spell the state out in words (ON ✅ / off) — no cryptic box
    def toggle(icon: str, name: str, on: bool,
               key: str) -> InlineKeyboardButton:
        return opt(f"{icon} {name}: " + ("ON ✅" if on else "off"), key)

    rows.append([toggle("🪝", "Cold open", p.hook, "hook"),
                 toggle("🔍", "Zoom", p.crop, "crop")])
    rows.append([toggle("✨", "Look", p.look, "look"),
                 opt("⚡ Shorter", "shorter")])
    rows.append([opt("🐢 Longer", "longer"),
                 opt(pick(not p.music_style, "🔇", "Mute"), "nomusic")])
    rows.append([opt(pick(p.music_style == "phonk", "🥁", "Phonk"), "phonk"),
                 opt(pick(p.music_style == "synthwave", "🎹", "Synth"),
                     "synthwave")])
    rows.append([InlineKeyboardButton("🎬 Render", callback_data=RENDER)])
    return InlineKeyboardMarkup(rows)


def redo_keyboard(session: EditSession) -> InlineKeyboardMarkup:
    """Quick-tap tweaks; free text always works as well.

    Laid out two buttons per row — three-wide rows get clipped on the
    right edge on iPhone, so every row holds at most two.
    """
    p = session.params

    def btn(label: str, key: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(label, callback_data=TWEAK + key)

    buttons = [
        btn("⚡ Shorter", "shorter"), btn("🐢 Longer", "longer"),
        btn("🔎 Tighter", "tighter"), btn("🔭 Wider", "wider"),
        btn("🪝 Cold open " + ("off" if p.hook else "on"), "hook"),
        btn("🔍 Zoom " + ("off" if p.crop else "on"), "crop"),
        btn("✨ Look " + ("off" if p.look else "on"), "look"),
    ]
    if session.vertical:  # vertical exports can carry a caption
        buttons += [btn("✍️ New caption", "newcaption"),
                    btn("🎨 Next style", "style")]
    # music section
    buttons += [
        btn("🥁 Phonk", "phonk"), btn("🎹 Synthwave", "synthwave"),
        btn("🔥 Faster beat", "faster"), btn("🧊 Slower beat", "slower"),
        btn("🎲 New mix", "remix"), btn("🔇 No music", "nomusic"),
    ]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    return InlineKeyboardMarkup(rows)


def _user_id(update) -> int | None:
    user = update.effective_user
    return user.id if user else None


def _session(context: ContextTypes.DEFAULT_TYPE,
             chat_id: int) -> EditSession | None:
    return context.application.bot_data["sessions"].get(chat_id)


async def start(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: BotConfig = context.application.bot_data["config"]
    if not is_allowed(_user_id(update), cfg.allowed_user_id):
        return
    await update.message.reply_text(
        "🎬 Hey! I'm *tokcut* — your pocket TikTok editor.\n\n"
        "Send me a clip *as a file* 📎 and I'll send back a ready-to-post "
        "edit. That's it — /help has the details.",
        parse_mode="Markdown",
    )


async def help_cmd(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: BotConfig = context.application.bot_data["config"]
    if not is_allowed(_user_id(update), cfg.allowed_user_id):
        return
    await update.message.reply_text(
        "📖 *How to use tokcut*\n\n"
        "*1. Send a clip — always as a file* 📎 → File\n"
        "Plain video messages get recompressed by Telegram and the "
        "quality is ruined before I see it.\n\n"
        "*2. Set it up*\n"
        "Claude watches the clip and proposes captions. In the setup "
        "screen you:\n"
        "• tap a caption idea, *type your own*, or *🚫 No caption*\n"
        "• flip the options you want — cold open (off by default), "
        "length, zoom, look, music\n"
        "• tap *🎬 Render* when ready.\n"
        "📱 Vertical → 1080x1920. 🖥️ Landscape → native resolution, no "
        "baked caption (ideas are sent to copy instead).\n"
        "🔇 Exports are *muted* — add a trending sound in the app.\n\n"
        "*3. Review the take*\n"
        "📋 It arrives with paste-ready TikTok copy (blurb + hashtags).\n"
        "✅ *Approve* — done; working files are cleaned up.\n"
        "🔁 *Redo* — tap a quick tweak or *type what to change*: "
        "_\"shorter\", \"zoom in tighter\", \"add phonk music\"_.\n\n"
        "*Commands*\n"
        "/status — render queue, current take, disk\n"
        "/start — short hello\n"
        "/help — this",
        parse_mode="Markdown",
    )


async def status_cmd(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: BotConfig = context.application.bot_data["config"]
    if not is_allowed(_user_id(update), cfg.allowed_user_id):
        return
    lock: asyncio.Lock = context.application.bot_data["render_lock"]
    session = _session(context, update.message.chat_id)
    lines = ["🎛️ *tokcut status*",
             f"render: {'🔴 busy' if lock.locked() else '🟢 idle'}"]
    if session is None:
        lines.append("session: none — send a clip 🎬")
    else:
        lines.append(f"session: take {session.revision} · "
                     f"`{session.summary()}`")
        if session.awaiting_feedback:
            lines.append("✍️ waiting for your redo feedback")
    try:
        total = sum(
            os.path.getsize(os.path.join(cfg.workdir, f))
            for f in os.listdir(cfg.workdir))
        lines.append(f"workdir: {total / 1048576:.0f} MB")
    except OSError:
        pass
    await update.message.reply_text("\n".join(lines),
                                    parse_mode="Markdown")


async def _post_init(app: Application) -> None:
    # registers the ☰ command menu next to the input field
    await app.bot.set_my_commands([
        BotCommand("status", "render queue, current take, disk"),
        BotCommand("help", "full guide: formats, captions, redo tweaks"),
        BotCommand("start", "short hello"),
    ])


async def _send_caption_ideas(ideas_msg, subject: str,
                              ideas: list[str]) -> None:
    """Edit the placeholder into the tap-to-copy caption-ideas message.

    Uses HTML (not Markdown): `<code>` spans are tap-to-copy on mobile,
    and HTML escaping is well-defined (only & < >) so a caption with
    `_ * [ ` ` in it can't break the message — the Markdown version did,
    and the failure was misreported as "Claude couldn't come up with
    ideas". Falls back to plain text if the formatted send still fails,
    so the ideas are never lost.
    """
    body = (f"👀 Claude saw: {html.escape(subject)}\n\n"
            "💡 Caption ideas — tap one to copy it:\n"
            + "\n".join(f"▫️ <code>{html.escape(c)}</code>"
                        for c in ideas[:3]))
    try:
        await ideas_msg.edit_text(body, parse_mode="HTML")
    except Exception as exc:  # noqa: BLE001 — never lose the ideas to format
        log.warning("ideas formatting failed, sending plain: %s", exc)
        plain = (f"👀 Claude saw: {subject}\n\n"
                 "💡 Caption ideas (copy one):\n"
                 + "\n".join(f"▫️ {c}" for c in ideas[:3]))
        await ideas_msg.edit_text(plain)


async def _claude_caption(msg, dest: str,
                          avoid: list[str] | None = None
                          ) -> tuple[str, str]:
    """Ask Claude for (caption, subject); empty caption on failure."""
    try:
        duration = (await asyncio.to_thread(probe, dest))["duration"]
        caption, subject = await asyncio.to_thread(
            suggest_caption, dest, duration, avoid)
        return caption, subject
    except Exception as exc:  # noqa: BLE001 — judgment is best-effort
        log.warning("caption judgment failed: %s", exc)
        await msg.reply_text("😅 Claude couldn't make sense of this one — "
                             "falling back to the filename.")
        return "", ""


@contextlib.asynccontextmanager
async def _render_guard(workdir: str, lock: asyncio.Lock):
    """Hold the render lock (sequential renders — parallel x265 OOMs)
    with a `.rendering` marker file for the CI deploy drain: a service
    restart mid-encode kills the take, so deploys wait for the marker
    to disappear before restarting (see ci.yml)."""
    marker = os.path.join(workdir, ".rendering")
    try:
        with open(marker, "w") as mf:
            mf.write(str(os.getpid()))
    except OSError:
        marker = ""
    try:
        async with lock:
            yield
    finally:
        if marker:
            with contextlib.suppress(OSError):
                os.remove(marker)


async def _render_and_deliver(msg, context: ContextTypes.DEFAULT_TYPE,
                              session: EditSession) -> None:
    """Render the session's current state and deliver with the keyboard."""
    cfg: BotConfig = context.application.bot_data["config"]
    lock: asyncio.Lock = context.application.bot_data["render_lock"]
    if lock.locked():
        await msg.reply_text("🚦 One render at a time — you're next in "
                             "line.")

    async with _render_guard(cfg.workdir, lock):
        session.revision += 1
        rev = session.revision
        if session.caption:
            tag = f": “{session.caption}”"
        elif session.vertical:
            tag = " (no caption)"
        else:
            tag = " (landscape, no caption)"
        status = await msg.reply_text(f"🎞️ Take {rev}, rolling{tag}")
        loop = asyncio.get_running_loop()
        progress: list[str] = []

        def notify(line: str) -> None:
            # called from the worker thread — marshal back to the loop.
            # Full pipeline lines go to the log; chat gets the short,
            # human version only.
            log.info("edit[r%d]: %s", rev, line)
            human = friendly_progress(line)
            if human is None:
                return
            progress.append(human)
            text = f"🎞️ Take {rev}\n" + "\n".join(progress[-6:])
            asyncio.run_coroutine_threadsafe(
                status.edit_text(text[:4000]), loop)

        p = session.params
        base = os.path.splitext(os.path.basename(session.source))[0]
        out = os.path.join(cfg.workdir, f"{base}_tokcut_r{rev}.mp4")
        try:
            await asyncio.to_thread(
                edit, session.source, session.caption,
                output=out,
                target=p.target if p.target is not None else "auto",
                style=p.style,
                caption_pos=p.caption_pos,
                hook=p.hook,
                crop_enabled=p.crop,
                zoom=p.zoom,
                look_enabled=p.look,
                keep_audio=p.keep_audio,
                music="__auto__" if p.music_style else None,
                music_style=p.music_style or "synthwave",
                music_bpm=p.music_bpm,
                music_seed=p.music_seed,
                preset=cfg.preset,
                on_progress=notify)
        except Exception as exc:  # noqa: BLE001 — report, keep bot alive
            log.exception("edit failed")
            session.revision -= 1
            await msg.reply_text(f"💥 The edit fell over: {exc}")
            return
        session.outputs.append(out)  # tracked for cleanup on approve

        post_kit = ""
        if cfg.claude_judge and claude_available():
            await status.edit_text(
                f"✍️ Take {rev} is cut — writing the TikTok post copy…")
            try:
                duration = (await asyncio.to_thread(probe, out))["duration"]
                post = await asyncio.to_thread(
                    suggest_post, out, duration, session.caption)
                post_kit = format_post_kit(post)
            except Exception as exc:  # noqa: BLE001 — best-effort
                log.warning("post copy failed: %s", exc)

        session.history.append(f"r{rev}: {session.summary()}")
        size_mb = os.path.getsize(out) / 1048576
        await status.edit_text(
            f"📤 Sending take {rev} your way ({size_mb:.1f} MB)…")
        if session.caption:
            doc_caption = f"🎬 Take {rev} · “{session.caption}”"
        elif session.vertical:
            doc_caption = f"🎬 Take {rev} · no caption (clean export)"
        else:
            doc_caption = f"🎬 Take {rev} · 🖥️ landscape, add your caption"
        if not p.music_style and not p.keep_audio:
            doc_caption += "\n🔇 Muted — add a trending sound in TikTok."
        with open(out, "rb") as fh:
            await msg.reply_document(
                document=fh,
                filename=delivery_name(session.file_name, rev),
                caption=doc_caption[:1024],
                reply_markup=VERDICT_KEYBOARD,
                # muted MP4s get auto-classified as "GIF" animations —
                # force Telegram to treat it as a plain file
                disable_content_type_detection=True,
                read_timeout=600,
                write_timeout=600,
            )
        # Send the TikTok copy as its OWN message in a code block: the
        # document caption isn't selectable on iOS, but a <pre> block gets
        # a one-tap copy button in Telegram mobile.
        if post_kit:
            await msg.reply_text(
                "📋 Caption for TikTok — tap to copy:\n"
                f"<pre>{html.escape(post_kit)}</pre>",
                parse_mode="HTML")


async def on_clip(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: BotConfig = context.application.bot_data["config"]
    uid = _user_id(update)
    if not is_allowed(uid, cfg.allowed_user_id):
        log.warning("ignoring clip from unauthorized user %s", uid)
        return

    msg = update.message
    file_obj = msg.video or msg.document
    if file_obj is None:
        return

    # A new clip abandons any unapproved session — clear its files now,
    # before the download (re-sent files reuse the same dest path).
    old = context.application.bot_data["sessions"].pop(msg.chat_id, None)
    if old is not None:
        cleanup_files(old)

    status = await msg.reply_text("📥 Grabbing your clip…")
    os.makedirs(cfg.workdir, exist_ok=True)
    file_name = getattr(file_obj, "file_name", "") or ""
    suffix = os.path.splitext(file_name)[1]
    dest = os.path.join(
        cfg.workdir, f"{file_obj.file_unique_id}{suffix or '.mp4'}")
    try:
        tg_file = await context.bot.get_file(file_obj.file_id)
        await tg_file.download_to_drive(dest)
    except Exception as exc:  # noqa: BLE001 — surface any download failure
        log.exception("download failed")
        hint = (
            f"Files over {cfg.max_file_mb} MB exceed the standard Bot API "
            "cap — set TOKCUT_BOT_API_URL to a local Bot API server (see "
            "docs/BOT.md)." if not cfg.local_mode else
            f"The local Bot API server caps files at {cfg.max_file_mb} MB."
        )
        await status.edit_text(
            f"⚠️ Couldn't download that file: {exc}\n{hint}")
        return

    try:
        src = await asyncio.to_thread(probe, dest)
    except Exception as exc:  # noqa: BLE001 — not a video / corrupt file
        log.exception("probe failed")
        await status.edit_text(f"⚠️ That doesn't look like a video: {exc}")
        return

    if src["duration"] < 2.0:
        await status.edit_text(
            "🖼️ That looks like a photo or a blink of a clip — send a "
            "video at least a few seconds long.")
        return

    if msg.video is not None:
        # sent as a video, not a file: Telegram already recompressed it
        await msg.reply_text(
            f"🗜️ Heads up — you sent this as a *video*, so Telegram "
            f"crushed it to {src['w']}x{src['h']} before I got it. "
            "I'll edit it anyway, but for full quality send clips as a "
            "*file*: 📎 → File.",
            parse_mode="Markdown")

    # No auto-render: Claude proposes captions, then the creator picks the
    # caption + options in a setup screen and taps Render (defaults: no cold
    # open, no caption).
    vertical = src["w"] <= src["h"]
    ideas: list[str] = []
    subject = ""
    if cfg.claude_judge and claude_available():
        await status.edit_text(
            "👀 Claude is watching your clip for caption ideas…")
        try:
            ideas, subject = await asyncio.to_thread(
                suggest_captions, dest, src["duration"])
        except Exception as exc:  # noqa: BLE001 — judgment is best-effort
            log.warning("caption ideas failed: %s", exc)

    session = EditSession(source=dest, file_name=file_name, caption="",
                          subject=subject, vertical=vertical,
                          caption_choices=ideas)
    session.params.target = cfg.default_target
    # a caption typed with the upload pre-selects it (vertical only)
    upload_caption = (msg.caption or "").strip()
    if vertical and upload_caption and not check_caption(upload_caption):
        session.caption = upload_caption
    context.application.bot_data["sessions"][msg.chat_id] = session

    saw = f"👀 Claude saw: {subject}\n" if subject else ""
    await status.edit_text((saw + "Set it up below 👇").strip())
    if not vertical and ideas:
        # landscape bakes no caption — offer the ideas as tap-to-copy text
        await _send_caption_ideas(
            await msg.reply_text("💡 caption ideas…"), subject, ideas)
    await msg.reply_text(setup_text(session), parse_mode="Markdown",
                         reply_markup=setup_keyboard(session))


async def on_button(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: BotConfig = context.application.bot_data["config"]
    query = update.callback_query
    await query.answer()
    if not is_allowed(_user_id(update), cfg.allowed_user_id):
        return
    session = _session(context, query.message.chat_id)
    if session is None:
        await query.message.reply_text("🤷 No clip in progress — send me "
                                       "one to get rolling.")
        return

    if query.data == APPROVE:
        # Session is done: the approved render (and the original) already
        # live in Telegram, so the working copies can go.
        context.application.bot_data["sessions"].pop(
            query.message.chat_id, None)
        removed, freed = cleanup_files(session)
        await query.edit_message_reply_markup(reply_markup=None)
        note = (f"\n🧹 Swept up {removed} working files "
                f"({freed / 1048576:.0f} MB freed)." if removed else "")
        await query.message.reply_text(
            "🎉 That's a wrap — post it! 🚀\n"
            "🔇 Muted export: add a trending sound in the TikTok app."
            + note)
    elif query.data == REDO:
        session.awaiting_feedback = True
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(
            f"🎬 Take {session.revision + 1} — tap a tweak, or just type "
            "what should change in your own words:",
            reply_markup=redo_keyboard(session))
    elif query.data.startswith(TWEAK):
        session.awaiting_feedback = False
        raw = tweak_updates(query.data.removeprefix(TWEAK), session.params)
        if not raw:
            await query.message.reply_text(
                "🤷 That tweak isn't available here.")
            return
        await _apply_and_render(query.message, context, session, raw)
    elif query.data.startswith(SETCAP):
        idx = int(query.data.removeprefix(SETCAP))
        if 0 <= idx < len(session.caption_choices):
            cand = session.caption_choices[idx]
            if check_caption(cand):
                await query.message.reply_text(
                    "⚠️ that caption might get flagged — pick another or "
                    "type your own.")
            else:
                session.caption = cand
                await _refresh_setup(query, session)
    elif query.data == OWNCAP:
        await query.message.reply_text(
            "✍️ Type your caption and send it as a message.")
    elif query.data == NOCAP:
        session.caption = ""
        await _refresh_setup(query, session)
    elif query.data.startswith(OPT):
        raw = tweak_updates(query.data.removeprefix(OPT), session.params)
        apply_updates(session, validate_updates(raw))
        await _refresh_setup(query, session)
    elif query.data == RENDER:
        session.phase = "review"
        await query.edit_message_reply_markup(reply_markup=None)
        await _render_and_deliver(query.message, context, session)


async def _refresh_setup(query, session: EditSession) -> None:
    """Re-draw the setup message in place after a caption/option change."""
    with contextlib.suppress(Exception):  # "message not modified" is fine
        await query.edit_message_text(
            setup_text(session), parse_mode="Markdown",
            reply_markup=setup_keyboard(session))


async def on_feedback(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: BotConfig = context.application.bot_data["config"]
    if not is_allowed(_user_id(update), cfg.allowed_user_id):
        return
    msg = update.message
    session = _session(context, msg.chat_id)
    if session is None:
        await msg.reply_text(
            "🤷 No clip in progress — send me one (as a file 📎) and "
            "then just tell me what to change.")
        return

    feedback = msg.text.strip()

    if session.phase == "setup":
        # in the setup screen a text message is the caption the creator typed
        if not session.vertical:
            await msg.reply_text(
                "🖥️ Landscape clips bake no caption — set the options and "
                "tap 🎬 Render.")
            return
        bad = check_caption(feedback)
        for warning in bad:
            await msg.reply_text(f"⚠️ caption check: {warning}")
        if bad:
            return
        session.caption = feedback
        await msg.reply_text(
            f"✍️ caption set: “{feedback}”\nTweak more or tap 🎬 Render.",
            reply_markup=setup_keyboard(session))
        return

    session.awaiting_feedback = False
    session.history.append(f"feedback: {feedback}")

    raw: dict = {}
    reply_note = ""
    if cfg.claude_judge and claude_available():
        await msg.reply_text("🧠 Translating that into editor moves…")
        try:
            raw = await asyncio.to_thread(
                interpret_feedback, feedback, session.summary(),
                session.history)
            reply_note = str(raw.get("reply", ""))
        except Exception as exc:  # noqa: BLE001 — fall back below
            log.warning("feedback interpretation failed: %s", exc)
    if not raw:
        raw = fallback_updates(feedback, session.params)
        if not raw:
            await msg.reply_text(
                "😅 I couldn't turn that into a setting (and Claude is "
                "offline). Try “shorter”, “longer”, “tighter” or "
                "“wider”, or rephrase it.")
            return

    await _apply_and_render(msg, context, session, raw, reply_note)


async def _apply_and_render(msg, context: ContextTypes.DEFAULT_TYPE,
                            session: EditSession, raw: dict,
                            reply_note: str = "") -> None:
    """Validate raw updates, apply them, and render the next take.

    Shared by free-text feedback (Claude-interpreted) and the quick-tap
    tweak buttons — validate_updates stays the hard gate for both.
    """
    updates = validate_updates(raw)

    if updates.pop("regenerate_caption", False) and "caption" not in updates:
        await msg.reply_text("✍️ Writing a fresh caption…")
        avoid = [*session.past_captions, session.caption]
        new_caption, _ = await _claude_caption(msg, session.source, avoid)
        if new_caption:
            updates["caption"] = new_caption

    if "caption" in updates:
        bad = check_caption(updates["caption"])
        for warning in bad:
            await msg.reply_text(f"⚠️ caption check: {warning}")
        if bad:
            updates.pop("caption")

    changes = apply_updates(session, updates)
    if not changes:
        if updates:
            await msg.reply_text(
                "🤝 It's already set that way — the take wouldn't "
                "change. Tell me something else to adjust.")
        else:
            await msg.reply_text(
                "🤷 I couldn't map that onto an editor setting — try "
                "different wording.")
        return

    note = f"{reply_note}\n" if reply_note else ""
    await msg.reply_text(note + "🔧 Dialing in: " + ", ".join(changes))
    await _render_and_deliver(msg, context, session)


def build_application(cfg: BotConfig) -> Application:
    # Uploading a multi-MB rendered clip blows past the default 5s write
    # timeout, so give media transfers room; downloads need a long read
    # timeout too. connect/pool stay short to fail fast on real outages.
    builder = (
        Application.builder()
        .token(cfg.telegram_token)
        .post_init(_post_init)
        .connect_timeout(20.0)
        .read_timeout(120.0)
        .write_timeout(120.0)
        .media_write_timeout(600.0)
        .pool_timeout(20.0)
    )
    if cfg.local_mode:
        # Self-hosted telegram-bot-api: 2 GB files, and downloads resolve to
        # local paths instead of HTTP (server shares this filesystem).
        builder = (
            builder
            .base_url(cfg.bot_api_base_url)
            .base_file_url(cfg.bot_api_base_file_url)
            .local_mode(True)
        )
    app = builder.build()
    app.bot_data["config"] = cfg
    app.bot_data["render_lock"] = asyncio.Lock()
    app.bot_data["sessions"] = {}
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(
        MessageHandler(filters.VIDEO | filters.Document.ALL, on_clip))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, on_feedback))
    return app


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    cfg = load_config()
    # a crash can leave the deploy-drain marker behind; no render can be
    # running at startup, so clear it
    with contextlib.suppress(OSError):
        os.remove(os.path.join(cfg.workdir, ".rendering"))
    # restarts orphan the workdir (sessions are in-memory only) — reclaim
    # it so abandoned originals don't pile up on a small box
    removed, freed = sweep_workdir(cfg.workdir)
    if removed:
        log.info("startup sweep: removed %d orphaned files (%.0f MB)",
                 removed, freed / 1048576)
    app = build_application(cfg)
    api = (f"local Bot API ({cfg.bot_api_base_url}, ≤2 GB)"
           if cfg.local_mode else "cloud Bot API (≤50 MB)")
    log.info("tokcut bot starting (user=%s, workdir=%s, api=%s)",
             cfg.allowed_user_id, cfg.workdir, api)
    app.run_polling()


if __name__ == "__main__":
    main()
