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

from pyrogram import filters, types

from Elevenyts import app, config, db, lang, queue, tune
from Elevenyts.helpers import _autoplay as autoplay
from Elevenyts.helpers import Track, buttons, thumb


@app.on_message(filters.command(["queue", "playing", "cqueue", "cplaying"]) & filters.group & ~app.bl_users)
@lang.language()
async def _queue_func(_, m: types.Message):
    try:
        await m.delete()
    except Exception:
        pass
    
    # Check for channel play mode
    is_channel = m.command[0].lower() in ["cqueue", "cplaying"]
    chat_id = m.chat.id
    
    if is_channel:
        channel_id = await db.get_cmode(m.chat.id)
        if channel_id is None:
            return await m.reply_text("Channel play is not enabled. Use /channelplay to enable.")
        chat_id = channel_id
    
    if not await db.get_call(chat_id):
        return await m.reply_text("Nothing is playing.")

    _reply = await m.reply_text("Fetching queue...")

    # Work on a copy so reading the queue can never change the real queue.
    _queue = list(queue.get_queue(chat_id))
    if not _queue:
        return await _reply.edit_text("Nothing is playing.")

    _media = _queue[0]
    _thumb = (
        await thumb.generate(_media)
        if isinstance(_media, Track)
        else config.DEFAULT_THUMB
    )
    _text = f"Now Playing:\n{_media.title}\nDuration: {_media.duration}\nRequested by: {_media.user}"

    # Up to 14 upcoming songs, each gets its own "Play Now" button.
    upcoming = _queue[1:15]

    # Nothing queued but autoplay already picked the next song: show it
    # too, with its own green Play Now button.
    if not upcoming and autoplay.is_enabled(chat_id):
        cached = tune._ap_cache.get(chat_id)
        if cached and cached[1].file_path:
            upcoming = [cached[1]]
            _text += "\n\nAutoplay next:"
            _text += f"\n1. {cached[1].title} ({cached[1].duration})"
            upcoming_shown = True
        else:
            upcoming_shown = False
    else:
        upcoming_shown = False

    if upcoming and not upcoming_shown:
        _text += "\n\nUpcoming:"
        for i, media in enumerate(upcoming, start=1):
            _text += f"\n{i}. {media.title} ({media.duration})"

    _playing = await db.playing(chat_id)
    await _reply.edit_media(
        media=types.InputMediaPhoto(
            media=_thumb,
            caption=_text,
        ),
        reply_markup=buttons.queue_markup(
            chat_id,
            "",
            _playing,
            upcoming=upcoming,
        ),
    )
