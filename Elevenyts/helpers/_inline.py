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
from pyrogram import types
from pyrogram.enums import ButtonStyle

from Elevenyts import app, config, lang
from Elevenyts.helpers import _autoplay as autoplay


# ----------------------------------------------------------
# FONT
# Every button label goes through _f(). It turns normal English
# letters/digits into bold sans-serif letters (clear and readable).
# To change the font for ALL buttons, only edit this one function.
# ----------------------------------------------------------
def _build_font_map() -> dict:
    mapping = {}
    for i in range(26):
        mapping[chr(65 + i)] = chr(0x1D5D4 + i)  # A-Z
        mapping[chr(97 + i)] = chr(0x1D5EE + i)  # a-z
    for i in range(10):
        mapping[chr(48 + i)] = chr(0x1D7EC + i)  # 0-9
    return mapping


_FONT = _build_font_map()


def _f(text: str) -> str:
    return "".join(_FONT.get(ch, ch) for ch in text)


# ----------------------------------------------------------
# SYMBOLS (plain text symbols, not emoji)
# ----------------------------------------------------------
S_GEM = "\u27E1"      # ⟡
S_STAR = "\u2726"     # ✦
S_DIAMOND = "\u2756"  # ❖
S_FLOWER = "\u2740"   # ❀
S_MOON = "\u263E"     # ☾
S_ON = "\u25C9"       # ◉
S_OFF = "\u25CB"      # ○
S_PLAY = "\u25B7"     # ▷
S_DOT = "\u00B7"      # ·
S_BACK = "\u00AB"     # «


class Inline:
    def __init__(self):
        self.ikm = types.InlineKeyboardMarkup
        self.ikb = types.InlineKeyboardButton

    def cancel_dl(self, text) -> types.InlineKeyboardMarkup:
        return self.ikm([[self.ikb(text=text, callback_data=f"cancel_dl", style=ButtonStyle.PRIMARY)]])

    def _autoplay_button(self, chat_id: int) -> types.InlineKeyboardButton:
        on = autoplay.is_enabled(chat_id)
        label = f"{S_ON if on else S_OFF} {_f('Autoplay')} {S_DOT} {_f('ON' if on else 'OFF')}"
        return self.ikb(text=label, callback_data=f"controls autoplay {chat_id}")

    def controls(
        self,
        chat_id: int,
        status: str = None,
        timer: str = None,
        remove: bool = False,
    ) -> types.InlineKeyboardMarkup:
        keyboard = []
        if status:
            keyboard.append(
                [self.ikb(
                    text=status, callback_data=f"controls status {chat_id}")]
            )
        elif timer:
            keyboard.append(
                [self.ikb(
                    text=timer, callback_data=f"controls status {chat_id}")]
            )

        if not remove:
            # Autoplay ON/OFF (replaces the old -15s / +15s seek row)
            keyboard.append([self._autoplay_button(chat_id)])

            # Main control buttons row
            keyboard.append(
                [
                    self.ikb(
                        text="▷", callback_data=f"controls resume {chat_id}"),
                    self.ikb(
                        text="II", callback_data=f"controls pause {chat_id}"),
                    self.ikb(
                        text="↻", callback_data=f"controls replay {chat_id}"),
                    self.ikb(
                        text="‣‣I", callback_data=f"controls skip {chat_id}"),
                    self.ikb(
                        text="▢", callback_data=f"controls stop {chat_id}"),
                ]
            )
        return self.ikm(keyboard)

    def help_markup(
        self, _lang: dict, back: bool = False
    ) -> types.InlineKeyboardMarkup:
        """Create help menu with categorized buttons."""
        if back:
            rows = [
                [
                    self.ikb(text=f"{S_BACK} {_f('Back')}", callback_data="help_main"),
                ]
            ]
        else:
            # Help menu with categorized buttons (3 per row)
            rows = [
                [
                    self.ikb(text=_f("Admins"), callback_data="help_admins"),
                    self.ikb(text=_f("Auth"), callback_data="help_auth"),
                    self.ikb(text=_f("Broadcast"), callback_data="help_broadcast"),
                ],
                [
                    self.ikb(text=_f("BL-Chat"), callback_data="help_blchat"),
                    self.ikb(text=_f("BL-User"), callback_data="help_bluser"),
                    self.ikb(text=_f("G-Ban"), callback_data="help_gban"),
                ],
                [
                    self.ikb(text=_f("Loop"), callback_data="help_loop"),
                    self.ikb(text=_f("Play"), callback_data="help_play"),
                    self.ikb(text=_f("Queue"), callback_data="help_queue"),
                ],
                [
                    self.ikb(text=_f("Seek"), callback_data="help_seek"),
                    self.ikb(text=_f("Shuffle"), callback_data="help_shuffle"),
                    self.ikb(text=_f("Ping"), callback_data="help_ping"),
                ],
                [
                    self.ikb(text=_f("Stats"), callback_data="help_stats"),
                    self.ikb(text=_f("Sudo"), callback_data="help_sudo"),
                    self.ikb(text=_f("Maintenance"), callback_data="help_maintenance"),
                ],
                [
                    self.ikb(text=_f("Tag All"), callback_data="tagall_info"),
                    self.ikb(text=_f("Autoplay"), callback_data="autoplay_info"),
                ],
                [
                    self.ikb(text=f"{S_BACK} {_f('Back')}", callback_data="start"),
                ]
            ]
        return self.ikm(rows)

    def ping_markup(self, text: str) -> types.InlineKeyboardMarkup:
        return self.ikm([
            [
                self.ikb(text=f"{S_FLOWER} {_f('Channel')}", url=config.SUPPORT_CHANNEL),
                self.ikb(text=f"{S_STAR} {_f('Support')}", url=config.SUPPORT_CHAT),
            ],
            [
                self.ikb(
                    text=f"{S_GEM} {_f('Add Me To Your Group')} {S_GEM}",
                    url=f"https://t.me/{app.username}?startgroup=true",
                ),
            ]
        ])

    def play_queued(
        self, chat_id: int, item_id: str, _text: str
    ) -> types.InlineKeyboardMarkup:
        return self.ikm(
            [
               [
    self.ikb(
        text="▷", callback_data=f"controls resume {chat_id}"),
    self.ikb(
        text="II", callback_data=f"controls pause {chat_id}"),
    self.ikb(
        text="↻", callback_data=f"controls replay {chat_id}"),
    self.ikb(
        text="‣‣I", callback_data=f"controls skip {chat_id}"),
    self.ikb(
        text="▢", callback_data=f"controls stop {chat_id}"),
               ]
            ]
        )

    def queue_markup(
        self,
        chat_id: int,
        _text: str = "",
        playing: bool = True,
        upcoming: list = None,
    ):
        """Buttons under /queue.

        When `upcoming` (list of queued songs) is given, one "Play Now"
        button is made per song and nothing else. Without it, the old
        single pause/resume button is returned (kept for compatibility).
        """
        if upcoming is not None:
            buttons = []
            for idx, item in enumerate(upcoming, start=1):
                item_id = getattr(item, "id", None)
                if not item_id:
                    continue
                data = f"controls force {chat_id} {item_id}"
                if len(data.encode()) > 64:  # Telegram callback_data limit
                    continue
                buttons.append(
                    self.ikb(
                        text=f"{S_PLAY} {_f('Play Now')} {_f(str(idx))}",
                        callback_data=data,
                        style=ButtonStyle.SUCCESS,
                    )
                )
            if not buttons:
                return None
            rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
            return self.ikm(rows)

        _action = "pause" if playing else "resume"
        return self.ikm(
            [[self.ikb(
                text=_text, callback_data=f"controls {_action} {chat_id} q", style=ButtonStyle.SUCCESS)]]
        )

    def settings_markup(
        self, lang: dict, admin_only: bool, language: str, chat_id: int
    ) -> types.InlineKeyboardMarkup:
        return self.ikm(
            [
                [
                    self.ikb(
                        text=lang["play_mode"] + " ➜",
                        callback_data=f"controls status {chat_id}",
                        style=ButtonStyle.PRIMARY,
                    ),
                    self.ikb(text=admin_only, callback_data="playmode", style=ButtonStyle.SUCCESS),
                ],
            ]
        )

    def start_key(
        self, lang: dict, private: bool = False
    ) -> types.InlineKeyboardMarkup:
        # Only "Take Me Home" is blue. The other buttons have no style
        # (plain/default colour). Symbols are used instead of emoji.
        rows = [
            [
                self.ikb(
                    text=f"{S_GEM} {_f('Take Me Home')} {S_GEM}",
                    url=f"https://t.me/{app.username}",
                    style=ButtonStyle.PRIMARY,
                ),
            ],
            [
                self.ikb(
                    text=f"{S_DIAMOND} {_f('Dev')}",
                    url="https://t.me/pixieiii",
                ),
                self.ikb(
                    text=f"{S_FLOWER} {_f('Support')}",
                    url="https://t.me/nightbloomsgc",
                ),
            ],
            [
                self.ikb(
                    text=f"{S_MOON} {_f('Updates')}",
                    url="https://t.me/pixiiela",
                ),
                self.ikb(
                    text=f"{S_STAR} {_f('Help')}",
                    callback_data="help",
                ),
            ],
        ]
        return self.ikm(rows)

    def yt_key(self, link: str) -> types.InlineKeyboardMarkup:
        return self.ikm(
            [
                [
                    self.ikb(text=f"{S_DIAMOND} {_f('Copy Link')}", copy_text=link),
                    self.ikb(text=f"{S_PLAY} {_f('Open In YouTube')}", url=link),
                ],
            ]
            )
