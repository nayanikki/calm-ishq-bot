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
import asyncio

from pyrogram import filters
from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import FloodWait
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from Elevenyts import app

# Cute animal / flower emoji only — cycled through for each tagged member.
# Written as \U escapes (plain ASCII text) so copy/paste can't corrupt them.
TAG_EMOJIS = [
    "\U0001F338",  # cherry blossom
    "\U0001F337",  # tulip
    "\U0001F339",  # rose
    "\U0001F33A",  # hibiscus
    "\U0001F33B",  # sunflower
    "\U0001F33C",  # blossom
    "\U0001F490",  # bouquet
    "\U0001F331",  # seedling
    "\U0001F343",  # leaf
    "\U0001F43C",  # panda
    "\U0001F430",  # rabbit face
    "\U0001F407",  # rabbit
    "\U0001F98B",  # butterfly
    "\U0001F424",  # baby chick
    "\U0001F425",  # front-facing chick
    "\U0001F99A",  # peacock
    "\U0001F428",  # koala
]

# How many mentions to put in each message before starting a new one —
# this keeps groups of members tagged separately, message after message,
# instead of everyone crammed into a single message.
MENTIONS_PER_MESSAGE = 5

# Tracks which chats currently have a /tagall running, so /canceltagall
# can find and stop it. Keyed by chat_id -> True while running.
_running_tags: dict[int, bool] = {}

TAGALL_INFO_TEXT = (
    "\U0001F338 <b>Tag All — how it works</b>\n\n"
    "<b>/tagall</b>\n"
    "Tags every member of the group. Each person shows up as a cute "
    "flower/animal emoji you can tap to open their profile.\n\n"
    "<b>/tagall your message here</b>\n"
    "Same as above, but your message is repeated above the emoji in "
    "every batch.\n\n"
    "Members are tagged in small batches (5 per message), one message "
    "after another, so the group doesn't get one giant wall of text. "
    "Each batch shows which numbers were just tagged, e.g. "
    "<b>Tagged: 1-5</b>, <b>Tagged: 6-10</b>, and so on. A final "
    "\u2705 <b>Tagging complete!</b> message shows the total once "
    "everyone has been tagged.\n\n"
    "<b>/canceltagall</b>\n"
    "Stops a tagall that's currently in progress. Send this any time "
    "while it's running and it will finish the batch it's on, then "
    "stop instead of continuing to the next group of members.\n\n"
    "Only group admins/owner can use either command."
)


async def _is_group_admin(message: Message) -> bool:
    """Return True if the message sender is an admin/creator of this chat."""
    try:
        member = await app.get_chat_member(message.chat.id, message.from_user.id)
    except Exception:
        return False
    return member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)


@app.on_message(
    filters.command(["tagall"])
    & filters.group
    & ~app.bl_users
)
async def tagall_command(_, m: Message) -> None:
    """Tag every member of the group, admin-only. Each mention is shown
    as a cute emoji you can tap to open that member's profile. The
    custom text (if given) repeats on every batch message, followed by
    a "Tagged: X-Y" range, and a final summary once everyone is done.
    Can be stopped early with /canceltagall."""

    if not m.from_user:
        return

    if not await _is_group_admin(m):
        return await m.reply_text(
            "\u274c Only group admins can use this command."
        )

    if _running_tags.get(m.chat.id):
        return await m.reply_text(
            "\u26a0\ufe0f A tagall is already running in this chat. "
            "Use /canceltagall to stop it first."
        )

    custom_text = ""
    if len(m.command) > 1:
        custom_text = m.text.split(None, 1)[1]

    await m.reply_text(
        "Tagging everyone, please wait... (use /canceltagall to stop)"
    )

    mentions = []
    emoji_index = 0

    try:
        async for member in app.get_chat_members(m.chat.id):
            user = member.user
            if not user or user.is_bot or user.is_deleted:
                continue

            emoji = TAG_EMOJIS[emoji_index % len(TAG_EMOJIS)]
            emoji_index += 1

            mentions.append(f'<a href="tg://user?id={user.id}">{emoji}</a>')
    except Exception as e:
        return await m.reply_text(f"Could not fetch member list: {e}")

    if not mentions:
        return await m.reply_text("No members found to tag.")

    total = len(mentions)
    _running_tags[m.chat.id] = True

    try:
        tagged_count = 0
        for i in range(0, total, MENTIONS_PER_MESSAGE):
            if not _running_tags.get(m.chat.id):
                await m.reply_text(
                    f"\u26d4 Tagging cancelled.\n"
                    f"<b>Total tagged before stopping:</b> {tagged_count}"
                )
                return

            chunk = mentions[i:i + MENTIONS_PER_MESSAGE]
            start = i + 1
            end = i + len(chunk)

            parts = []
            if custom_text:
                parts.append(custom_text)
            parts.append(" ".join(chunk))
            parts.append(f"<b>Tagged: {start}-{end}</b>")
            text = "\n\n".join(parts)

            while True:
                try:
                    await m.reply_text(text)
                    break
                except FloodWait as e:
                    await asyncio.sleep(e.value)

            tagged_count = end
            await asyncio.sleep(1)

        await m.reply_text(
            f"\u2705 Tagging complete!\n<b>Total tagged:</b> {total}"
        )
    finally:
        _running_tags.pop(m.chat.id, None)


@app.on_message(
    filters.command(["canceltagall"])
    & filters.group
    & ~app.bl_users
)
async def cancel_tagall_command(_, m: Message) -> None:
    """Stop a /tagall that's currently running in this chat. Admin-only."""

    if not m.from_user:
        return

    if not await _is_group_admin(m):
        return await m.reply_text(
            "\u274c Only group admins can use this command."
        )

    if not _running_tags.get(m.chat.id):
        return await m.reply_text("There's no tagall running right now.")

    _running_tags[m.chat.id] = False
    await m.reply_text("\U0001F6D1 Stopping tagall...")


@app.on_callback_query(filters.regex("^tagall_info$"))
async def tagall_info_callback(_, cq: CallbackQuery) -> None:
    """Shown when the TAG ALL button in the help menu is tapped."""
    back_button = InlineKeyboardMarkup(
        [[InlineKeyboardButton(text="\u2b05 Back", callback_data="help_main")]]
    )
    try:
        if cq.message.photo:
            await cq.message.edit_caption(TAGALL_INFO_TEXT, reply_markup=back_button)
        else:
            await cq.message.edit_text(TAGALL_INFO_TEXT, reply_markup=back_button)
    except Exception:
        pass
    await cq.answer()
