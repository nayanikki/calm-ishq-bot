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
"""Per-chat autoplay state.

Kept free of any Elevenyts imports on purpose, so buttons.py, calls.py and
the plugins can all import it without circular-import trouble.

NOTE: everything here lives in memory. If the bot restarts (or the Render
free instance spins down), autoplay goes back to OFF for every chat.
"""

# After this many songs in one voice-chat session (autoplay ON), the bot
# says goodbye and leaves the voice chat.
LIMIT = 20

_enabled: dict = {}       # chat_id -> bool
_count: dict = {}         # chat_id -> songs played this session
_last_counted: dict = {}  # chat_id -> id of the last song that was counted
_played: dict = {}        # chat_id -> set of video ids already played
_last_track: dict = {}    # chat_id -> last Track/Media that started playing


def is_enabled(chat_id: int) -> bool:
    return _enabled.get(chat_id, False)


def set_enabled(chat_id: int, value: bool) -> None:
    value = bool(value)
    was_on = _enabled.get(chat_id, False)
    _enabled[chat_id] = value
    if value and not was_on:
        # Start counting fresh. If a song is already playing it counts as 1.
        _count[chat_id] = 1 if chat_id in _last_track else 0


def toggle(chat_id: int) -> bool:
    set_enabled(chat_id, not is_enabled(chat_id))
    return is_enabled(chat_id)


def note_played(chat_id: int, media) -> None:
    """Call every time a song starts playing in a chat."""
    vid = getattr(media, "id", None)
    _last_track[chat_id] = media
    if vid:
        _played.setdefault(chat_id, set()).add(vid)
    # Repeating the same song (loop / replay) is not counted again.
    if vid is None or _last_counted.get(chat_id) != vid:
        _count[chat_id] = _count.get(chat_id, 0) + 1
        _last_counted[chat_id] = vid


def mark_played(chat_id: int, video_id: str) -> None:
    if video_id:
        _played.setdefault(chat_id, set()).add(video_id)


def was_played(chat_id: int, video_id: str) -> bool:
    return video_id in _played.get(chat_id, set())


def get_last(chat_id: int):
    return _last_track.get(chat_id)


def get_count(chat_id: int) -> int:
    return _count.get(chat_id, 0)


def limit_reached(chat_id: int) -> bool:
    return is_enabled(chat_id) and get_count(chat_id) >= LIMIT


def reset_session(chat_id: int) -> None:
    """Forget the current voice-chat session (the ON/OFF setting stays)."""
    _count.pop(chat_id, None)
    _last_counted.pop(chat_id, None)
    _played.pop(chat_id, None)
    _last_track.pop(chat_id, None)
