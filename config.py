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

from os import getenv
from typing import List
from urllib.parse import urlparse
from dotenv import load_dotenv

load_dotenv()


class Config:
    def __init__(self):
        # Telegram API
        self.API_ID: int = int(getenv("API_ID", "0"))
        self.API_HASH: str = getenv("API_HASH", "")
        self.BOT_TOKEN: str = getenv("BOT_TOKEN", "")
        self.LOGGER_ID: int = int(getenv("LOGGER_ID", "0"))
        self.OWNER_ID: int = int(getenv("OWNER_ID", "0"))

        # Database
        self.MONGO_URL: str = getenv("MONGO_DB_URI", "")

        # Limits
        self.DURATION_LIMIT: int = int(getenv("DURATION_LIMIT", "300")) * 60
        self.QUEUE_LIMIT: int = int(getenv("QUEUE_LIMIT", "30"))
        self.PLAYLIST_LIMIT: int = int(getenv("PLAYLIST_LIMIT", "20"))

        # Assistant Sessions
        self.SESSION1: str = getenv("STRING_SESSION", "")
        self.SESSION2: str = getenv("STRING_SESSION2", "")
        self.SESSION3: str = getenv("STRING_SESSION3", "")

        # Support Links
        self.SUPPORT_CHANNEL: str = getenv("SUPPORT_CHANNEL", "https://t.me/chhikuumaa")
        self.SUPPORT_CHAT: str = getenv("SUPPORT_CHAT", "https://t.me/haramkhoro_87")

        # Excluded Chats
        self.EXCLUDED_CHATS: List[int] = self._parse_excluded_chats()

        # Feature Flags
        self.AUTO_END: bool = self._str_to_bool(getenv("AUTO_END", "False"))
        self.AUTO_LEAVE: bool = self._str_to_bool(getenv("AUTO_LEAVE", "False"))
        self.THUMB_GEN: bool = self._str_to_bool(getenv("THUMB_GEN", "True"))
        self.VIDEO_PLAY: bool = self._str_to_bool(getenv("VIDEO_PLAY", "True"))
        self.VIDEO_MAX_HEIGHT: int = self._parse_video_height()

        # Music API — PRIMARY provider. Whichever API_URL/API_KEY you set
        # is tried first. Example: API_URL=https://api.onegrab.fun
        # (only the host is kept; any path like /pricing is dropped).
        self.API_URL: str = self._clean_base_url(getenv("API_URL", ""))
        self.API_KEY: str = getenv("API_KEY", "").strip()

        # Music API — FALLBACK provider. Only used if the primary API
        # (above) fails on every retry. Leave both blank to disable the
        # fallback entirely and only use cookies as backup.
        # Example: FALLBACK_API_URL=https://music.yukiapi.site
        self.FALLBACK_API_URL: str = self._clean_base_url(getenv("FALLBACK_API_URL", ""))
        self.FALLBACK_API_KEY: str = getenv("FALLBACK_API_KEY", "").strip()

        self.ENABLE_API: bool = self._str_to_bool(getenv("ENABLE_API", "True"))
        self.ENABLE_COOKIES_FALLBACK: bool = self._str_to_bool(getenv("ENABLE_COOKIES_FALLBACK", "True"))
        self.API_TIMEOUT: int = int(getenv("API_TIMEOUT", "60"))
        self.API_STREAM_TIMEOUT: int = int(getenv("API_STREAM_TIMEOUT", "300"))

        # YouTube Cookies
        self.COOKIES_URL: List[str] = self._parse_cookies()

        # Images
        self.DEFAULT_THUMB: str = getenv("DEFAULT_THUMB", "https://files.catbox.moe/t5n8d2.jpg")
        self.PING_IMG: str = getenv("PING_IMG", "https://files.catbox.moe/t5n8d2.jpg")
        self.START_IMG: str = getenv("START_IMG", "https://files.catbox.moe/4cw15b.jpg")
        self.RADIO_IMG: str = getenv("RADIO_IMG", "https://litter.catbox.moe/pi6brrhl2f82zrwv.jpg")

        # Moderation
        self.EXCLUDED_USERNAMES: List[str] = getenv("EXCLUDED_USERNAMES", "").split()

    @staticmethod
    def _clean_base_url(url: str) -> str:
        """Keep only scheme://host so a stray path (/pricing, /api) or
        trailing slash in the env var can't break API calls."""
        url = (url or "").strip()
        if not url:
            return ""
        if "://" not in url:
            url = "https://" + url
        parsed = urlparse(url)
        if not parsed.netloc:
            return ""
        return f"{parsed.scheme}://{parsed.netloc}"

    def _parse_video_height(self) -> int:
        default_height = 1080
        raw_value = getenv("VIDEO_MAX_HEIGHT", str(default_height))
        try:
            height = int(raw_value)
        except (TypeError, ValueError):
            return default_height
        if height <= 0:
            return 0
        return max(480, min(height, 2160))

    def _parse_excluded_chats(self) -> List[int]:
        excluded = getenv("EXCLUDED_CHATS", "")
        if not excluded:
            return []
        chat_ids = []
        for chat_id in excluded.split(","):
            chat_id = chat_id.strip()
            if chat_id.lstrip('-').isdigit():
                chat_ids.append(int(chat_id))
        return chat_ids

    def _parse_cookies(self) -> List[str]:
        cookie_str = getenv("COOKIE_URL", "")
        if not cookie_str:
            return []
        valid_sources = ["batbin.me", "pastebin.com", "paste.ee", "rentry.co"]
        return [url.strip() for url in cookie_str.split() if url.strip() and any(source in url for source in valid_sources)]

    @staticmethod
    def _str_to_bool(value: str) -> bool:
        return value.lower() in ("true", "1", "yes", "y", "on")

    def check(self) -> None:
        required_vars = {
            "API_ID": self.API_ID,
            "API_HASH": self.API_HASH,
            "BOT_TOKEN": self.BOT_TOKEN,
            "MONGO_DB_URI": self.MONGO_URL,
            "LOGGER_ID": self.LOGGER_ID,
            "OWNER_ID": self.OWNER_ID,
            "STRING_SESSION": self.SESSION1,
        }

        missing = [name for name, value in required_vars.items() if not value or (isinstance(value, int) and value == 0)]
        if missing:
            raise SystemExit(f"Missing required env vars: {', '.join(missing)}")

        if self.ENABLE_API and not self.API_URL:
            print("Warning: ENABLE_API is True but API_URL is not set")
        if self.ENABLE_API and not self.API_KEY:
            print("Warning: ENABLE_API is True but API_KEY is not set")
        if self.ENABLE_API and self.FALLBACK_API_URL and not self.FALLBACK_API_KEY:
            print("Warning: FALLBACK_API_URL is set but FALLBACK_API_KEY is not set")


config = Config()
