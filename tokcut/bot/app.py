"""Telegram bot entry point — step 4: approve/redo loop.

Python's job: Telegram I/O, allow-list, downloads, session state, running
the edit pipeline (queued, in a worker thread), validating every parameter
change. Claude Code's job (subscription OAuth): watching frames to write
the caption, reviewing the rendered output, and interpreting free-text
redo feedback into setting changes.
"""

import asyncio
import logging
import os

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
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
    review_output,
    suggest_caption,
)
from .config import BotConfig, is_allowed, load_config
from .pipeline import derive_caption
from .session import (
    EditSession,
    apply_updates,
    cleanup_files,
    fallback_updates,
    validate_updates,
)

log = logging.getLogger("tokcut.bot")

APPROVE = "approve"
REDO = "redo"
VERDICT_KEYBOARD = InlineKeyboardMarkup([[
    InlineKeyboardButton("✅ Approve", callback_data=APPROVE),
    InlineKeyboardButton("🔁 Redo", callback_data=REDO),
]])


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
        "Send me a clip *as a file* 📎 (plain video messages get squashed "
        "by Telegram) and I'll cut the boring bits, speed-ramp the rest, "
        "and drop a caption where it won't cover the action.\n\n"
        "✍️ Add a message caption to choose the on-video text — or let "
        "Claude watch the clip and write one.",
        parse_mode="Markdown",
    )


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


async def _render_and_deliver(msg, context: ContextTypes.DEFAULT_TYPE,
                              session: EditSession) -> None:
    """Render the session's current state and deliver with the keyboard."""
    cfg: BotConfig = context.application.bot_data["config"]
    lock: asyncio.Lock = context.application.bot_data["render_lock"]
    if lock.locked():
        await msg.reply_text("🚦 One render at a time — you're next in "
                             "line.")

    async with lock:  # renders are sequential: parallel x265 OOMs the box
        session.revision += 1
        rev = session.revision
        status = await msg.reply_text(
            f"🎞️ Take {rev}, rolling: “{session.caption}”")
        loop = asyncio.get_running_loop()
        progress: list[str] = []

        def notify(line: str) -> None:
            # called from the worker thread — marshal back to the loop
            progress.append(line)
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
                target=p.target,
                style=p.style,
                caption_pos=p.caption_pos,
                hook=p.hook,
                crop_enabled=p.crop,
                keep_audio=p.keep_audio,
                music="__auto__" if p.music_style else None,
                music_style=p.music_style or "synthwave",
                on_progress=notify)
        except Exception as exc:  # noqa: BLE001 — report, keep bot alive
            log.exception("edit failed")
            session.revision -= 1
            await msg.reply_text(f"💥 The edit fell over: {exc}")
            return
        session.outputs.append(out)  # tracked for cleanup on approve

        review_line = ""
        if cfg.claude_judge and claude_available():
            await status.edit_text(
                f"🧐 Take {rev} is cut — sending it to the director…")
            try:
                duration = (await asyncio.to_thread(probe, out))["duration"]
                review = await asyncio.to_thread(
                    review_output, out, duration, session.caption)
                if review["verdict"] == "approve":
                    review_line = f"🧐 Director: ✅ {review['notes']}"
                else:
                    issues = "\n".join(f"• {i}" for i in review["issues"])
                    review_line = (f"🧐 Director: 🔁 would tweak:\n{issues}\n"
                                   f"({review['notes']})")
            except Exception as exc:  # noqa: BLE001 — best-effort
                log.warning("output review failed: %s", exc)

        session.history.append(f"r{rev}: {session.summary()}")
        size_mb = os.path.getsize(out) / 1048576
        await status.edit_text(
            f"📤 Sending take {rev} your way ({size_mb:.1f} MB)…")
        doc_caption = f"🎬 Take {rev} · “{session.caption}”"
        if not p.music_style and not p.keep_audio:
            doc_caption += "\n🔇 Muted — add a trending sound in TikTok."
        if review_line:
            doc_caption += f"\n\n{review_line}"
        with open(out, "rb") as fh:
            await msg.reply_document(
                document=fh,
                filename=os.path.basename(out),
                caption=doc_caption[:1024],
                reply_markup=VERDICT_KEYBOARD,
                read_timeout=600,
                write_timeout=600,
            )


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

    caption = (msg.caption or "").strip()
    subject = ""
    if not caption and cfg.claude_judge and claude_available():
        await status.edit_text("👀 Claude is watching your clip to write "
                               "a caption…")
        caption, subject = await _claude_caption(msg, dest)
        if caption:
            await status.edit_text(
                f"👀 Claude saw: {subject}\n✍️ Caption: “{caption}”")
    if not caption:
        caption = derive_caption(msg.caption, file_name)
    for warning in check_caption(caption):
        await msg.reply_text(f"⚠️ caption check: {warning}")

    session = EditSession(source=dest, file_name=file_name,
                          caption=caption, subject=subject)
    session.params.target = cfg.default_target
    context.application.bot_data["sessions"][msg.chat_id] = session

    await _render_and_deliver(msg, context, session)


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
            f"🎬 Take {session.revision + 1} — what should change? "
            "Plain words work:\n"
            "• “shorter and punchier”\n"
            "• “different caption” / “caption at the top”\n"
            "• “yellow caption” / “white on black caption”\n"
            "• “no cold open”\n"
            "• “add phonk music”")


async def on_feedback(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: BotConfig = context.application.bot_data["config"]
    if not is_allowed(_user_id(update), cfg.allowed_user_id):
        return
    msg = update.message
    session = _session(context, msg.chat_id)
    if session is None or not session.awaiting_feedback:
        return  # not in a redo conversation — ignore chatter

    feedback = msg.text.strip()
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
        raw = fallback_updates(feedback, session.params.target)
        if not raw:
            await msg.reply_text(
                "😅 I couldn't turn that into a setting (and Claude is "
                "offline). Try “shorter” or “longer”, or rephrase it.")
            session.awaiting_feedback = True
            return

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
        await msg.reply_text(
            "🤷 That didn't change anything — tap Redo and try different "
            "wording.")
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
    app = build_application(cfg)
    api = (f"local Bot API ({cfg.bot_api_base_url}, ≤2 GB)"
           if cfg.local_mode else "cloud Bot API (≤50 MB)")
    log.info("tokcut bot starting (user=%s, workdir=%s, api=%s)",
             cfg.allowed_user_id, cfg.workdir, api)
    app.run_polling()


if __name__ == "__main__":
    main()
