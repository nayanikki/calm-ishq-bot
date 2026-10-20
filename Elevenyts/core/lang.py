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
import html
import json
from functools import wraps
from pathlib import Path

from Elevenyts import app, db, logger

# Supported language codes and their display names
lang_codes = {
    "en": "English",  # English language
}


class Language:
    """
    Language class for managing multilingual support using JSON language files.
    """

    def __init__(self):
        """Initialize the language system and load all translation files."""
        self.lang_codes = lang_codes
        # Directory containing translation files
        self.lang_dir = Path("Elevenyts/locales")
        self.languages = self.load_files()  # Load all language files into memory
        self._localized = {}  # (lang_code, bot name) -> dict with {bot} filled in

    def _with_bot_name(self, lang_code: str) -> dict:
        """Return the language dict with every {bot} replaced by this bot's
        name (so the same code shows the right name for each client)."""
        base = self.languages[lang_code]
        name = getattr(app, "name", None)
        if not name:
            return base
        key = (lang_code, name)
        cached = self._localized.get(key)
        if cached is None:
            # escape for HTML parse mode and for the str.format() that
            # callers run on these strings afterwards
            safe = html.escape(name).replace("{", "{{").replace("}", "}}")
            cached = {
                k: (v.replace("{bot}", safe) if isinstance(v, str) else v)
                for k, v in base.items()
            }
            self._localized[key] = cached
        return cached

    def load_files(self):
        """Load all language JSON files from the locales directory."""
        languages = {}
        for lang_code in self.lang_codes.keys():
            lang_file = self.lang_dir / \
                f"{lang_code}.json"  # Path to language file
            if lang_file.exists():
                with open(lang_file, "r", encoding="utf-8") as file:
                    languages[lang_code] = json.load(
                        file)  # Load translations into dict
        logger.info(f"🌐 Loaded languages: {', '.join(languages.keys())}")
        return languages

    async def get_lang(self, chat_id: int) -> dict:
        """Get the translation dictionary for a specific chat."""
        return self._with_bot_name("en")  # Return the translation dictionary

    def language(self):
        def decorator(func):
            @wraps(func)
            async def wrapper(*args, **kwargs):
                fallen = next(
                    (
                        arg
                        for arg in args
                        if hasattr(arg, "chat") or hasattr(arg, "message")
                    ),
                    None,
                )

                if hasattr(fallen, "chat"):
                    chat = fallen.chat
                elif hasattr(fallen, "message"):
                    chat = fallen.message.chat

                if chat.id in db.blacklisted:
                    try:
                        await chat.leave()
                    except Exception:
                        pass
                    return

                lang_code = "en"
                lang_dict = self._with_bot_name(lang_code)

                setattr(fallen, "lang", lang_dict)
                return await func(*args, **kwargs)

            return wrapper

        return decorator
