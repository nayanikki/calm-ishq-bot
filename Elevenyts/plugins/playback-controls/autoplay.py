# ==========================================================
# Copyright (c) 2026 ArtistBots
# All Rights Reserved.
#
# Project      : ArtistBots API Telegram Music Bot
# Powered By   : Artist
# Type         : API Based Telegram Music Bot
#
# Bot          : @ArtistApibot
# Channel      : https://t.me/artistbots
# GitHub       : https://github.com/elevenyts
#
# Unauthorized copying, modification, or redistribution
# of this source code without permission is prohibited.
# ==========================================================
from pyrogram import filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from Elevenyts import app, db
from Elevenyts.helpers import _autoplay as autoplay

AUTOPLAY_INFO_TEXT = (
    "\u2726 <b>Autoplay - how it works</b>\n\n"
    "When autoplay is ON and the queue runs out, the bot automatically "
    "finds a related song and keeps playing, so the music never stops.\n\n"
    "\u2022 If someone adds a song with /play, that song plays next, and "
    "after it the bot goes back to picking related songs.\n"
    "\u2022 If a picked song can't be downloaded, the bot tries another one.\n"
    f"\u2022 After <b>{autoplay.LIMIT} songs</b> the bot sends a message and "
    "leaves the voice chat.\n\n"
    "<b>/autoplay</b> - show current status\n"
    "<b>/autoplay on</b> - turn autoplay ON\n"
    "<b>/autoplay off</b> - turn autoplay OFF\n\n"
    "You can also use the Autoplay button under the now-playing message.\n\n"
    "Only group admins and authorized users can change autoplay."
)


async def _can_manage(chat_id: int, user_id: int) -> bool:
    """Sudo users, authorized users and group admins."""
    if user_id in app.sudoers:
        return True
    if await db.is_auth(chat_id, user_id):
        return True
    admins = await db.get_admins(chat_id)
    return user_id in admins


@app.on_message(
    filters.command(["autoplay"])
    & filters.group
    & ~app.bl_users
)
async def autoplay_command(_, m: Message) -> None:
    """/autoplay [on|off] - admins and authorized users only."""
    if not m.from_user:
        return

    try:
        await m.delete()
    except Exception:
        pass

    chat_id = m.chat.id

    if not await _can_manage(chat_id, m.from_user.id):
        return await m.reply_text(
            "\u274c Only admins and authorized users can use this command."
        )

    arg = m.command[1].lower() if len(m.command) > 1 else None

    if arg in ("on", "enable"):
        autoplay.set_enabled(chat_id, True)
        return await m.reply_text(
            "\u2705 <b>Autoplay is now ON.</b>\n"
            f"The bot keeps playing related songs and leaves after "
            f"{autoplay.LIMIT} songs."
        )

    if arg in ("off", "disable"):
        autoplay.set_enabled(chat_id, False)
        return await m.reply_text("\u2705 <b>Autoplay is now OFF.</b>")

    status = "ON" if autoplay.is_enabled(chat_id) else "OFF"
    await m.reply_text(
        f"\u2726 <b>Autoplay status:</b> {status}\n\n"
        "<b>/autoplay on</b> - turn ON\n"
        "<b>/autoplay off</b> - turn OFF"
    )


@app.on_callback_query(filters.regex("^autoplay_info$"))
async def autoplay_info_callback(_, cq: CallbackQuery) -> None:
    """Shown when the Autoplay button in the help menu is tapped."""
    back_button = InlineKeyboardMarkup(
        [[InlineKeyboardButton(text="\u00ab Back", callback_data="help_main")]]
    )
    try:
        if cq.message.photo:
            await cq.message.edit_caption(AUTOPLAY_INFO_TEXT, reply_markup=back_button)
        else:
            await cq.message.edit_text(AUTOPLAY_INFO_TEXT, reply_markup=back_button)
    except Exception:
        pass
    await cq.answer()
