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

import os
import re
import glob
import time
import yt_dlp
import random
import asyncio
import aiohttp
from dataclasses import replace
from pathlib import Path
from typing import Optional, Union

from pyrogram import enums, types
from py_yt import Playlist, VideosSearch
from Elevenyts import config, logger
from Elevenyts.helpers import Track, utils


class YouTube:
    def __init__(self):
        """Initialize YouTube handler with configuration and caching."""
        self.base = "https://www.youtube.com/watch?v="
        self.cookies = []
        self.checked = False
        self.warned = False

        # PRIMARY provider — tried first.
        self.api_url = config.API_URL
        self.api_key = config.API_KEY

        # FALLBACK provider — tried only if the primary fails on every
        # retry, or isn't configured at all. Leave blank to disable.
        self.fallback_api_url = config.FALLBACK_API_URL
        self.fallback_api_key = config.FALLBACK_API_KEY

        self.enable_api = config.ENABLE_API
        self.enable_cookies_fallback = config.ENABLE_COOKIES_FALLBACK
        self.api_timeout = config.API_TIMEOUT
        self.api_stream_timeout = config.API_STREAM_TIMEOUT

        # How many times to retry a SINGLE provider before moving on
        # (to the fallback provider, or to cookies).
        self.api_max_attempts = 2
        self.api_retry_delay = 2  # seconds between retries

        # A real full-length audio file is well over this size even at
        # low bitrates. Providers sometimes return HTTP 200 with a tiny
        # error/placeholder payload instead of actual audio — anything
        # under this is treated as a failed download, not a success.
        self.min_valid_file_bytes = 200 * 1024  # 200 KB

        # Regular expression to match YouTube URLs
        self.regex = re.compile(
            r"(https?://)?(www\.|m\.|music\.)?"
            r"(youtube\.com/(watch\?v=|shorts/|live/|embed/|playlist\?list=)|youtu\.be/)"
            r"([A-Za-z0-9_-]{11}|PL[A-Za-z0-9_-]+)([&?][^\s]*)?"
        )

        # Cache search results (10 minute TTL)
        self.search_cache = {}
        self._download_semaphore = asyncio.Semaphore(5)
        self._max_video_height = config.VIDEO_MAX_HEIGHT

        # Log configuration
        logger.info("=" * 50)
        logger.info("📹 YouTube Handler Initialized")
        logger.info(f"🎵 API Priority: {'ENABLED' if self.enable_api else 'DISABLED'}")
        if self.enable_api:
            logger.info(f"🔗 Primary API URL: {self.api_url or '(not set)'}")
            if self.api_key:
                masked_key = self.api_key[:8] + "..." if len(self.api_key) > 8 else "***"
                logger.info(f"🔑 Primary API Key: {masked_key}")
            else:
                logger.warning("⚠️ No primary API key configured!")
            if self.fallback_api_url:
                logger.info(f"🔗 Fallback API URL: {self.fallback_api_url}")
                if self.fallback_api_key:
                    masked_fallback = self.fallback_api_key[:8] + "..." if len(self.fallback_api_key) > 8 else "***"
                    logger.info(f"🔑 Fallback API Key: {masked_fallback}")
                else:
                    logger.warning("⚠️ Fallback API URL set but no fallback API key configured!")
            else:
                logger.info("🔗 Fallback API: (not configured)")
        logger.info(f"🍪 Cookies Fallback: {'ENABLED' if self.enable_cookies_fallback else 'DISABLED'}")
        logger.info("=" * 50)

    def _locate_download_file(self, video_id: str, video: bool = False) -> Optional[str]:
        """Locate any completed download file for a video id."""
        pattern = f"downloads/{video_id}*"
        candidates = sorted([
            path for path in glob.glob(pattern)
            if not path.endswith((".part", ".ytdl", ".info.json", ".temp"))
        ])

        video_exts = {".mp4", ".mkv", ".webm", ".mov"}
        audio_exts = {".m4a", ".webm", ".opus", ".mp3", ".ogg", ".wav", ".flac"}

        if video:
            for path in candidates:
                if os.path.isdir(path):
                    continue
                if Path(path).suffix.lower() in video_exts:
                    return path
        else:
            for path in candidates:
                if os.path.isdir(path):
                    continue
                if Path(path).suffix.lower() in audio_exts:
                    return path

        for path in candidates:
            if os.path.isdir(path):
                continue
            return path
        return None

    def get_cookies(self):
        """Get random cookie file from cookies directory.

        Re-scans the cookies folder every time self.cookies is empty so
        that newly added cookie files are picked up without a bot
        restart (previously this only scanned once, ever).
        """
        if not self.checked or not self.cookies:
            cookies_dir = "Elevenyts/cookies"
            self.cookies = []
            if os.path.exists(cookies_dir):
                for file in os.listdir(cookies_dir):
                    if file.endswith(".txt"):
                        self.cookies.append(file)
            self.checked = True

        if not self.cookies:
            if not self.warned:
                self.warned = True
                logger.warning("🍪 Cookies are missing; downloads might fail.")
            return None

        cookie_file = f"Elevenyts/cookies/{random.choice(self.cookies)}"
        logger.debug(f"Using cookie file: {cookie_file}")
        return cookie_file

    async def save_cookies(self, urls: list[str]) -> None:
        """Save cookies from URLs to files."""
        logger.info("🍪 Saving cookies from urls...")
        saved_count = 0

        # Create cookies directory if not exists
        cookies_dir = Path("Elevenyts/cookies")
        cookies_dir.mkdir(parents=True, exist_ok=True)

        for url in urls:
            try:
                # Generate unique filename
                path = cookies_dir / f"cookie{random.randint(10000, 99999)}.txt"

                # Convert to raw URL if needed
                if "pastebin.com" in url:
                    link = url.replace("pastebin.com", "pastebin.com/raw")
                elif "batbin.me" in url:
                    link = url.replace("batbin.me", "batbin.me/raw")
                else:
                    link = url

                async with aiohttp.ClientSession() as session:
                    async with session.get(link, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                        if resp.status != 200:
                            logger.error(f"❌ Cookie download failed: HTTP {resp.status} from {url}")
                            continue

                        content = await resp.read()
                        if not content or len(content) < 50:
                            logger.error(f"❌ Cookie file empty or invalid from {url}")
                            continue

                        # Save cookie file
                        with open(path, "wb") as fw:
                            fw.write(content)

                        if path.exists() and path.stat().st_size > 0:
                            saved_count += 1
                            cookie_filename = path.name
                            if cookie_filename not in self.cookies:
                                self.cookies.append(cookie_filename)
                            logger.info(f"✅ Saved: {cookie_filename} ({len(content)} bytes)")

            except asyncio.TimeoutError:
                logger.error(f"❌ Cookie download timeout from {url}")
            except Exception as e:
                logger.error(f"❌ Cookie download error from {url}: {e}")

        self.checked = True

        if saved_count > 0:
            logger.info(f"✅ Cookies saved successfully! ({saved_count} file(s))")
        else:
            logger.error("❌ No cookies saved! Check COOKIE_URL in .env.")

    def _build_api_request(self, api_url: str, api_key: str, video_id: str, download_type: str):
        """
        Build the (endpoint, params, headers) for providers that expose
        a direct binary /download-style endpoint (used for Sparrow and
        Shrutibots, and as a generic fallback for any other provider).
        OneGrab/Fallen is handled separately (see _download_via_onegrab)
        because it returns JSON metadata first, not a binary payload on
        a single /download route. Yuki API uses a direct /stream route
        (see _download_via_yukiapi).
        """
        host = api_url.lower()

        if "shrutibots" in host:
            # Shrutibots' own reference client: GET /download with
            # exactly url, type, api_key as query params, no headers,
            # response streamed directly as the binary file.
            endpoint = f"{api_url}/download"
            params = {"url": video_id, "type": download_type, "api_key": api_key}
            headers = {}
            return endpoint, params, headers

        if "sparrow" in host:
            # Sparrow publishes GET /download but not its exact param
            # names, so the id/key are sent under common aliases —
            # extra params most APIs simply ignore.
            endpoint = f"{api_url}/download"
            params = {
                "url": video_id,
                "query": video_id,
                "id": video_id,
                "type": download_type,
                "format": download_type,
                "api_key": api_key,
                "key": api_key,
            }
            headers = {"Authorization": f"Bearer {api_key}"}
            return endpoint, params, headers

        # Generic contract — used for any provider not recognized above.
        endpoint = f"{api_url}/download"
        params = {"url": video_id, "type": download_type, "api_key": api_key}
        headers = {"Authorization": f"Bearer {api_key}"}
        return endpoint, params, headers

    @staticmethod
    def _extract_stream_link(payload) -> Optional[str]:
        """Look for a stream/download URL in a few common JSON shapes."""
        if not isinstance(payload, dict):
            return None
        for key in ("url", "download_url", "downloadUrl", "link", "stream_url", "streamUrl", "cdnurl"):
            value = payload.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value
        for wrapper in ("result", "data"):
            nested = payload.get(wrapper)
            if isinstance(nested, dict):
                for key in ("url", "download_url", "downloadUrl", "link", "stream_url", "streamUrl", "cdnurl"):
                    value = nested.get(key)
                    if isinstance(value, str) and value.startswith("http"):
                        return value
        return None

    @staticmethod
    def _looks_like_text_error(data: bytes) -> bool:
        """Sniff a chunk of bytes to see if it's actually text/JSON/HTML
        (an error body or a webpage) rather than binary audio/video
        data. Real MP3/M4A files start with binary frame headers,
        never with '{', '[', '<', or a run of plain ASCII text."""
        if not data:
            return True
        sample = data[:64].lstrip()
        if not sample:
            return True
        if sample[0:1] in (b"{", b"[", b"<"):
            return True
        try:
            sample.decode("ascii")
            return True
        except UnicodeDecodeError:
            return False

    async def _save_binary_response(self, response, file_path: str, download_type: str, video_id: str) -> Optional[str]:
        """Stream an aiohttp response body to disk with progress logging.

        After writing, the file is sanity-checked two ways: it must be
        well over self.min_valid_file_bytes, AND its first bytes must
        not look like text/JSON/HTML. If either check fails (a provider
        serving an error page, a Telegram preview page, an expired-link
        placeholder, or a tiny preview clip with HTTP 200), the file is
        deleted and treated as a failed download so the caller retries
        or falls through to the next provider/cookies, instead of
        silently handing the player a corrupt or non-audio file.
        """
        logger.info(f"📥 Downloading {download_type} via API for {video_id}...")

        content_length = response.headers.get('content-length')
        if content_length:
            file_size_mb = int(content_length) / (1024 * 1024)
            logger.info(f"📦 File size: {file_size_mb:.2f} MB")

        downloaded = 0
        last_log = 0
        first_chunk: Optional[bytes] = None
        with open(file_path, "wb") as f:
            async for chunk in response.content.iter_chunked(65536):
                if first_chunk is None:
                    first_chunk = chunk
                f.write(chunk)
                downloaded += len(chunk)

                if downloaded - last_log >= 5 * 1024 * 1024:
                    progress_mb = downloaded / (1024 * 1024)
                    if content_length:
                        total_mb = int(content_length) / (1024 * 1024)
                        percent = (downloaded / int(content_length)) * 100
                        logger.info(f"📊 Progress: {progress_mb:.1f}/{total_mb:.1f} MB ({percent:.1f}%)")
                    else:
                        logger.info(f"📊 Downloaded: {progress_mb:.1f} MB")
                    last_log = downloaded

        if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
            logger.error("❌ API download failed: file is empty or not created")
            if os.path.exists(file_path):
                os.remove(file_path)
            return None

        actual_size = os.path.getsize(file_path)

        if first_chunk and self._looks_like_text_error(first_chunk):
            try:
                logger.error(
                    f"❌ API returned text/JSON/HTML instead of audio for {video_id} "
                    f"({actual_size} bytes). Content preview: {first_chunk[:200]!r}"
                )
            except Exception:
                logger.error(f"❌ API returned non-audio content for {video_id} ({actual_size} bytes)")
            os.remove(file_path)
            return None

        if actual_size < self.min_valid_file_bytes:
            logger.error(
                f"❌ API returned a suspiciously small file "
                f"({actual_size} bytes) for {video_id} — treating as failed "
                f"(likely a preview clip or error payload, not the full track)."
            )
            os.remove(file_path)
            return None

        file_size_mb = actual_size / (1024 * 1024)
        logger.info(f"✅ [API SUCCESS] Downloaded: {file_path} ({file_size_mb:.2f} MB)")
        return file_path

    async def _download_binary(self, url: str, file_path: str, headers: dict, download_type: str, video_id: str) -> Optional[str]:
        """Download a binary file from a direct link (used when the API
        response is a JSON envelope pointing at the actual file instead
        of streaming the binary directly)."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=self.api_stream_timeout),
                ) as response:
                    if response.status != 200:
                        logger.error(f"Stream link returned status {response.status}")
                        return None
                    return await self._save_binary_response(response, file_path, download_type, video_id)
        except Exception as e:
            logger.error(f"❌ Failed to download stream link for {video_id}: {e}")
            return None

    async def _download_via_onegrab(self, api_url: str, api_key: str, link: str, video: bool, file_path: str, video_id: str) -> Optional[str]:
        """
        OneGrab/Fallen-family flow:
        GET /api/track?url=<youtube_url>&video=true|false
        -> JSON with a "cdnurl" field holding the actual file link
        -> download that link to disk.

        NOTE: this provider's cdnurl has been observed to sometimes be
        a Telegram (t.me) link, which does not serve raw file bytes
        over a plain GET — it returns Telegram's HTML preview page
        instead. The binary/text sniff in _save_binary_response catches
        this and treats it as a failure rather than corrupting the
        downloaded file.
        """
        endpoint = f"{api_url}/api/track"
        params = {
            "url": link,
            "video": str(video).lower(),
            "api_key": api_key,
            "key": api_key,
            "apikey": api_key,
        }
        headers = {"Authorization": f"Bearer {api_key}", "X-API-Key": api_key} if api_key else {}

        logger.info(f"Calling API: {endpoint}")

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    endpoint,
                    params=params,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=self.api_timeout),
                ) as response:
                    logger.info(f"API response status: {response.status}")

                    if response.status != 200:
                        try:
                            error_text = await response.text()
                            logger.error(f"API returned status {response.status}: {error_text[:200]}")
                        except Exception:
                            logger.error(f"API returned status {response.status}")
                        return None

                    try:
                        payload = await response.json(content_type=None)
                    except Exception as e:
                        logger.error(f"Failed to parse JSON response from API: {e}")
                        return None
        except asyncio.TimeoutError:
            logger.error(f"⏰ API timeout for {video_id} after {self.api_timeout} seconds")
            return None
        except aiohttp.ClientError as e:
            logger.error(f"🌐 API client error for {video_id}: {e}")
            return None

        cdn_url = payload.get("cdnurl") if isinstance(payload, dict) else None
        if not cdn_url:
            logger.error(f"API response had no cdnurl: {payload}")
            return None

        download_type = "video" if video else "audio"
        return await self._download_binary(cdn_url, file_path, {}, download_type, video_id)

    async def _download_via_yukiapi(self, api_url: str, api_key: str, link: str, video: bool, file_path: str, video_id: str) -> Optional[str]:
        """
        Yuki API flow (music.yukiapi.site) — single-step direct stream:
        GET /stream/{video_id}?key=<api_key>&type=audio|video
        -> binary media stream, saved to disk directly.

        NOTE: this replaces an earlier two-step /download -> token ->
        /stream flow that was unreliable (the server would sometimes
        reject its own freshly-issued token as "invalid or expired"),
        causing random mid-session failures. The single-step /stream
        call with just the api_key removes that failure point entirely.
        """
        download_type = "video" if video else "audio"
        stream_endpoint = f"{api_url}/stream/{video_id}"
        stream_params = {"key": api_key, "type": download_type}
        stream_headers = {
            "X-API-Key": api_key,
            "Authorization": f"Bearer {api_key}",
        }

        logger.info(f"Calling API: {stream_endpoint}")

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    stream_endpoint,
                    params=stream_params,
                    headers=stream_headers,
                    timeout=aiohttp.ClientTimeout(total=self.api_stream_timeout),
                ) as response:
                    logger.info(f"API stream response status: {response.status}")

                    if response.status != 200:
                        try:
                            error_text = await response.text()
                            logger.error(f"API stream returned status {response.status}: {error_text[:200]}")
                        except Exception:
                            logger.error(f"API stream returned status {response.status}")
                        return None

                    return await self._save_binary_response(response, file_path, download_type, video_id)
        except asyncio.TimeoutError:
            logger.error(f"⏰ API stream timeout for {video_id} after {self.api_stream_timeout} seconds")
            return None
        except aiohttp.ClientError as e:
            logger.error(f"🌐 API stream client error for {video_id}: {e}")
            return None

    async def _download_via_generic(self, api_url: str, api_key: str, video_id: str, download_type: str, file_path: str) -> Optional[str]:
        """Binary /download-style flow used for Shrutibots, Sparrow, and
        any other unrecognized provider (see _build_api_request)."""
        endpoint, params, headers = self._build_api_request(api_url, api_key, video_id, download_type)
        logger.info(f"Calling API: {endpoint}")

        async with aiohttp.ClientSession() as session:
            async with session.get(
                endpoint,
                params=params,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=self.api_stream_timeout),
            ) as response:
                logger.info(f"API response status: {response.status}")

                if response.status != 200:
                    try:
                        error_text = await response.text()
                        logger.error(f"API returned status {response.status}: {error_text[:200]}")
                    except Exception:
                        logger.error(f"API returned status {response.status}")
                    return None

                content_type = (response.headers.get("content-type") or "").lower()

                if "application/json" in content_type:
                    try:
                        payload = await response.json(content_type=None)
                    except Exception as e:
                        logger.error(f"Failed to parse JSON response from API: {e}")
                        return None

                    stream_link = self._extract_stream_link(payload)
                    if not stream_link:
                        logger.error(f"API JSON response had no recognizable stream/download link: {payload}")
                        return None

                    return await self._download_binary(stream_link, file_path, headers, download_type, video_id)

                return await self._save_binary_response(response, file_path, download_type, video_id)

    async def _try_provider(self, api_url: str, api_key: str, link: str, video: bool, file_path: str, video_id: str, download_type: str, label: str) -> Optional[str]:
        """
        Try ONE provider (identified by api_url/api_key), retrying it
        up to self.api_max_attempts times before giving up on it. Does
        not touch any other provider — the caller decides what to try
        next if this returns None.
        """
        if not api_url or not api_key:
            return None

        host = api_url.lower()
        is_onegrab = "onegrab" in host or "fallenapi" in host
        is_yukiapi = "yukiapi" in host

        for attempt in range(1, self.api_max_attempts + 1):
            try:
                if attempt > 1:
                    logger.info(f"🔁 [{label} RETRY {attempt}/{self.api_max_attempts}] Retrying for {video_id}")
                else:
                    logger.info(f"🚀 [{label}] Requesting {video_id} (type: {download_type})")

                if is_onegrab:
                    result = await self._download_via_onegrab(api_url, api_key, link, video, file_path, video_id)
                elif is_yukiapi:
                    result = await self._download_via_yukiapi(api_url, api_key, link, video, file_path, video_id)
                else:
                    result = await self._download_via_generic(api_url, api_key, video_id, download_type, file_path)

                if result:
                    return result

            except asyncio.TimeoutError:
                logger.error(f"⏰ [{label}] Timeout for {video_id} after {self.api_stream_timeout} seconds")
            except aiohttp.ClientError as e:
                logger.error(f"🌐 [{label}] Client error for {video_id}: {e}")
            except Exception as e:
                logger.error(f"❌ [{label}] Download failed for {video_id}: {type(e).__name__}: {e}")

            if attempt < self.api_max_attempts:
                await asyncio.sleep(self.api_retry_delay)

        return None

    async def download_via_api(self, link: str, video: bool = False) -> Optional[str]:
        """
        Download audio/video using the configured music APIs.

        Order: PRIMARY provider (API_URL/API_KEY, retried
        api_max_attempts times) -> FALLBACK provider
        (FALLBACK_API_URL/FALLBACK_API_KEY, also retried), only if the
        primary is unset or fails completely. Cookies (in download())
        remain the final backup after both.

        Args:
            link: YouTube URL or video ID
            video: True for video download, False for audio download

        Returns:
            Path to downloaded file or None if failed
        """
        if not self.enable_api:
            logger.info("API is disabled in config (ENABLE_API is False)")
            return None

        # Extract video ID from URL
        if "v=" in link:
            video_id = link.split("v=")[-1].split("&")[0]
        elif "youtu.be" in link:
            video_id = link.split("/")[-1].split("?")[0]
        else:
            video_id = link

        if not video_id or len(video_id) < 3:
            logger.debug(f"Invalid video ID: {video_id}")
            return None

        DOWNLOAD_DIR = "downloads"
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)

        # Set file extension based on type
        file_ext = ".mp4" if video else ".mp3"
        file_path = os.path.join(DOWNLOAD_DIR, f"{video_id}{file_ext}")

        # Check if already downloaded
        if os.path.exists(file_path) and os.path.getsize(file_path) >= self.min_valid_file_bytes:
            logger.debug(f"File already exists: {file_path}")
            return file_path

        download_type = "video" if video else "audio"

        # PRIMARY
        if self.api_url and self.api_key:
            result = await self._try_provider(
                self.api_url, self.api_key, link, video, file_path, video_id, download_type, "PRIMARY"
            )
            if result:
                return result
            logger.warning(f"⚠️ [PRIMARY FAILED] {video_id}, trying fallback API...")
        else:
            logger.info("Primary API_URL/API_KEY not configured, skipping to fallback API")

        # FALLBACK
        if self.fallback_api_url and self.fallback_api_key:
            result = await self._try_provider(
                self.fallback_api_url, self.fallback_api_key, link, video, file_path, video_id, download_type, "FALLBACK"
            )
            if result:
                return result
            logger.warning(f"⚠️ [FALLBACK FAILED] {video_id}")
        else:
            logger.debug("No fallback API configured")

        return None

    async def download_via_cookies(self, video_id: str, video: bool = False) -> Optional[str]:
        """
        Download audio/video using yt-dlp with cookies (Fallback Method).

        Args:
            video_id: YouTube video ID
            video: True for video download, False for audio download

        Returns:
            Path to downloaded file or None if failed
        """
        if not self.enable_cookies_fallback:
            logger.debug("Cookies fallback is disabled in config")
            return None

        url = self.base + video_id
        filename_pattern = f"downloads/{video_id}"

        # Check existing files
        existing_files = [
            f for f in glob.glob(f"{filename_pattern}.*")
            if not f.endswith('.part')
        ]

        if video:
            video_candidates = [
                f for f in existing_files
                if Path(f).suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"}
            ]
            if video_candidates:
                logger.debug(f"Found existing video file: {video_candidates[0]}")
                return video_candidates[0]
        else:
            audio_candidates = [
                f for f in existing_files
                if Path(f).suffix.lower() in {".m4a", ".webm", ".opus", ".mp3", ".ogg", ".wav", ".flac"}
            ]
            if audio_candidates:
                logger.debug(f"Found existing audio file: {audio_candidates[0]}")
                return audio_candidates[0]

            container_fallbacks = [
                f for f in existing_files
                if Path(f).suffix.lower() in {".mp4", ".mkv", ".mov"}
            ]
            if container_fallbacks:
                logger.debug(f"Found existing container file: {container_fallbacks[0]}")
                return container_fallbacks[0]

        # Create downloads directory
        downloads_dir = Path("downloads")
        if not downloads_dir.exists():
            try:
                downloads_dir.mkdir(parents=True, exist_ok=True)
                logger.info("📁 Created downloads directory")
            except Exception as e:
                logger.error(f"❌ Cannot create downloads directory: {e}")
                return None

        async with self._download_semaphore:
            cookie = self.get_cookies()
            base_opts = {
                "outtmpl": "downloads/%(id)s.%(ext)s",
                "quiet": False,
                "verbose": True,
                "noplaylist": True,
                "geo_bypass": True,
                "no_warnings": True,
                "overwrites": False,
                "nocheckcertificate": True,
                "continuedl": True,
                "noprogress": True,
                "concurrent_fragment_downloads": 4,
                "http_chunk_size": 524288,
                "socket_timeout": 30,
                "retries": 2,
                "fragment_retries": 2,
                "extractor_retries": 5,
                "sleep_interval_requests": 1,
                # Fix: without spoofing the player client, YouTube often
                # serves cloud/datacenter IPs (Render, Heroku, etc.) a
                # restricted format list that doesn't match
                # "bestaudio/best", causing "Requested format is not
                # available" even though cookies are valid. Trying
                # extra clients (tv, android) beyond mweb/web sometimes
                # bypasses the SABR-only restriction.
                "extractor_args": {
                    "youtube": {
                        "player_client": ["mweb", "web", "tv", "android"],
                    },
                    "youtubepot-bgutilscript": {"server_home": "/root/bgutil-ytdlp-pot-provider/server"},
                },
            }

            if video:
                height_filter = ""
                if self._max_video_height and self._max_video_height > 0:
                    height_filter = f"[height<={self._max_video_height}]"
                format_chain = (
                    f"bestvideo[ext=mp4]{height_filter}+bestaudio[ext=m4a]/"
                    f"bestvideo{height_filter}+bestaudio/"
                    "bestvideo+bestaudio/best"
                )
                ydl_opts = {
                    **base_opts,
                    "format": format_chain,
                    "merge_output_format": "mp4",
                    "postprocessors": [
                        {
                            "key": "FFmpegVideoConvertor",
                            "preferedformat": "mp4",
                        }
                    ],
                }
            else:
                ydl_opts = {
                    **base_opts,
                    "format": "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best",
                    "postprocessors": [],
                }

            ydl_opts_cookie = {
                **ydl_opts,
                "cookiefile": cookie,
            }

            def _download(ydl_runtime_opts):
                ydl_instance = None
                try:
                    ydl_instance = yt_dlp.YoutubeDL(ydl_runtime_opts)
                    info = ydl_instance.extract_info(url, download=True)
                    if not info:
                        logger.error(f"❌ Failed to extract info for {video_id}")
                        return None

                    time.sleep(0.5)
                    located = self._locate_download_file(video_id, video=video)
                    if located:
                        logger.info(f"✅ Download completed: {located}")
                        return located

                    logger.error(f"❌ Download completed but file not found for: {video_id}")
                    return None
                except Exception as ex:
                    logger.warning(f"⚠️ Download error for {video_id}: {ex}")
                    recovered = self._locate_download_file(video_id, video=video)
                    if recovered:
                        logger.info(f"✅ Recovered existing file: {recovered}")
                        return recovered
                    return None
                finally:
                    if ydl_instance:
                        try:
                            ydl_instance.close()
                        except Exception:
                            pass

            logger.info(f"🍪 [COOKIES FALLBACK] Downloading {video_id} with cookies...")
            result = await asyncio.to_thread(_download, ydl_opts_cookie)

            if result:
                logger.info(f"✅ [COOKIES SUCCESS] Downloaded: {result}")
            else:
                logger.warning(f"⚠️ [COOKIES FAILED] Could not download {video_id}")

            return result

    def valid(self, url: str) -> bool:
        """Check if URL is a valid YouTube URL."""
        return bool(re.match(self.regex, url))

    def url(self, message_1: types.Message) -> Union[str, None]:
        """Extract YouTube URL from message."""
        messages = [message_1]
        link = None

        if message_1.reply_to_message:
            messages.append(message_1.reply_to_message)

        for message in messages:
            text = message.text or message.caption or ""

            if message.entities:
                for entity in message.entities:
                    if entity.type == enums.MessageEntityType.URL:
                        link = text[entity.offset: entity.offset + entity.length]
                        break

            if message.caption_entities:
                for entity in message.caption_entities:
                    if entity.type == enums.MessageEntityType.TEXT_LINK:
                        link = entity.url
                        break

        if link:
            # Remove tracking parameters
            return link.split("&si")[0].split("?si")[0]
        return None

    async def search(self, query: str, m_id: int) -> Track | None:
        """Search for a song on YouTube."""
        cache_key = query
        current_time = asyncio.get_running_loop().time()

        # Check cache
        if cache_key in self.search_cache:
            cached_result, cache_timestamp = self.search_cache[cache_key]
            if current_time - cache_timestamp < 600:  # 10 minutes TTL
                fresh = replace(cached_result)
                fresh.message_id = m_id
                fresh.file_path = None
                fresh.user = None
                fresh.time = 0
                fresh.video = False
                return fresh

        try:
            _search = VideosSearch(query, limit=1)
            results = await _search.next()
        except Exception as e:
            logger.warning(f"⚠️ YouTube search failed for '{query}': {e}")
            return None

        if results and results["result"]:
            data = results["result"][0]
            duration = data.get("duration")
            is_live = duration is None or duration == "LIVE"

            track = Track(
                id=data.get("id"),
                channel_name=data.get("channel", {}).get("name"),
                duration=duration if not is_live else "LIVE",
                duration_sec=0 if is_live else utils.to_seconds(duration),
                message_id=m_id,
                title=data.get("title")[:25],
                thumbnail=data.get("thumbnails", [{}])[-1].get("url").split("?")[0],
                url=data.get("link"),
                view_count=data.get("viewCount", {}).get("short"),
                is_live=is_live,
            )

            # Cache result
            self.search_cache[cache_key] = (track, current_time)

            # Clean old cache entries
            if len(self.search_cache) > 100:
                oldest_key = min(self.search_cache.keys(),
                                 key=lambda k: self.search_cache[k][1])
                del self.search_cache[oldest_key]

            return replace(track)
        return None

    async def playlist(self, limit: int, user: str, url: str) -> list[Track]:
        """Extract tracks from a YouTube playlist."""
        try:
            plist = await Playlist.get(url)
            tracks = []

            if not plist or "videos" not in plist or not plist["videos"]:
                return []

            for data in plist["videos"][:limit]:
                try:
                    thumbnails = data.get("thumbnails", [])
                    thumbnail_url = ""
                    if thumbnails and len(thumbnails) > 0:
                        thumbnail_url = thumbnails[-1].get("url", "").split("?")[0]

                    link = data.get("link", "")
                    if "&list=" in link:
                        link = link.split("&list=")[0]

                    track = Track(
                        id=data.get("id", ""),
                        channel_name=data.get("channel", {}).get("name", ""),
                        duration=data.get("duration", "0:00"),
                        duration_sec=utils.to_seconds(data.get("duration", "0:00")),
                        title=(data.get("title", "Unknown")[:25]),
                        thumbnail=thumbnail_url,
                        url=link,
                        user=user,
                        view_count="",
                    )
                    tracks.append(track)
                except Exception as e:
                    logger.warning(f"Failed to parse playlist item: {e}")
                    continue

            return tracks
        except KeyError as e:
            raise Exception(f"Failed to parse playlist. YouTube may have changed their structure.")
        except Exception as e:
            logger.error(f"Playlist extraction error: {e}")
            raise

    async def download(self, video_id: str, is_live: bool = False, video: bool = False) -> Optional[str]:
        """
        Download audio/video from YouTube.

        PRIORITY: Primary API -> Fallback API -> Cookies

        Args:
            video_id: YouTube video ID
            is_live: Whether it's a live stream
            video: True for video download, False for audio download

        Returns:
            Path to downloaded file or None if failed
        """
        # For live streams, only cookies method works
        if is_live:
            logger.info(f"🔴 Live stream detected for {video_id}, using cookies method...")
            cookie = self.get_cookies()
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "cookiefile": cookie,
                "format": "bestaudio/best",
                "noplaylist": True,
                "socket_timeout": 20,
                "extractor_retries": 5,
                "sleep_interval_requests": 1,
                "extractor_args": {
                    "youtube": {
                        "player_client": ["mweb", "web", "tv", "android"],
                    },
                    "youtubepot-bgutilscript": {"server_home": "/root/bgutil-ytdlp-pot-provider/server"},
                },
            }

            def _extract_url():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    try:
                        info = ydl.extract_info(self.base + video_id, download=False)
                        if not info:
                            return None

                        direct = info.get("url")
                        if direct:
                            return direct

                        for fmt in info.get("formats", []):
                            if fmt.get("acodec") != "none" and fmt.get("url"):
                                return fmt["url"]

                        return info.get("manifest_url")
                    except Exception as ex:
                        logger.error(f"Live stream extraction failed: {ex}")
                        return None

            try:
                stream_url = await asyncio.wait_for(asyncio.to_thread(_extract_url), timeout=35)
                if stream_url:
                    logger.info(f"✅ Live stream URL extracted for {video_id}")
                return stream_url
            except asyncio.TimeoutError:
                logger.error(f"Live stream URL extraction timed out for {video_id}")
                return None

        # ------------------------------------------------------------
        # ALWAYS ENTER API HANDLER
        # ------------------------------------------------------------

        logger.info(
            f"🎯 [PRIORITY 1] Trying API "
            f"download for {video_id}"
        )

        result = await self.download_via_api(
            self.base + video_id,
            video=video
        )

        if result:
            logger.info(
                f"✅ [SUCCESS] Downloaded "
                f"via API: {video_id}"
            )
            return result

        logger.warning(
            f"⚠️ [ALL APIs FAILED] {video_id}, "
            f"trying cookies fallback..."
        )

        # ============================================================
        # COOKIES FALLBACK
        # ============================================================

        if self.enable_cookies_fallback:
            logger.info(
                f"🍪 [PRIORITY 3] Trying cookies "
                f"download for {video_id}"
            )

            result = await self.download_via_cookies(
                video_id,
                video=video
            )

            if result:
                logger.info(
                    f"✅ [SUCCESS] Downloaded "
                    f"via cookies: {video_id}"
                )
                return result

            logger.error(
                f"❌ [COOKIES FAILED] "
                f"Could not download {video_id}"
            )

        # ============================================================
        # EVERYTHING FAILED
        # ============================================================

        logger.error(
            f"❌ [FAILED] All download methods "
            f"failed for {video_id}"
        )

        return None
