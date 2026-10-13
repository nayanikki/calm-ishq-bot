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
import logging
import os
import random

import aiohttp
from ntgcalls import ConnectionNotFound, TelegramServerError
from py_yt import VideosSearch
from pyrogram import enums, errors
from pyrogram.errors import MessageIdInvalid
from pyrogram.types import InputMediaPhoto, Message
from pytgcalls import PyTgCalls, exceptions, types
from pytgcalls.pytgcalls_session import PyTgCallsSession

from Elevenyts import app, config, db, lang, logger, preload, queue, userbot, yt
from Elevenyts.helpers import Media, Track, buttons, thumb, utils
from Elevenyts.helpers import _autoplay as autoplay

# Suppress pytgcalls harmless errors (library bugs - not critical)


class PyTgCallsErrorFilter(logging.Filter):
    def filter(self, record):
        # Filter out UpdateGroupCall errors
        if 'UpdateGroupCall' in record.getMessage():
            return False
        # Filter out ConnectionNotFound errors (happens when call ends but updates still arrive)
        if 'Connection with chat id' in record.getMessage() and 'not found' in record.getMessage():
            return False
        return True


logging.getLogger('pyrogram.dispatcher').addFilter(PyTgCallsErrorFilter())


class TgCall(PyTgCalls):
    def __init__(self):
        self.clients = []
        self._play_next_locks = {}  # Lock to prevent concurrent play_next calls per chat
        self._stream_end_cache = {}  # Cache to prevent duplicate stream end processing
        self._ap_locks = {}          # per-chat lock for autoplay picking
        self._ap_cache = {}          # chat_id -> (seed_video_id, prefetched Track)
        self._ap_tasks = {}          # chat_id -> background prefetch task
        self._transition = {}        # chat_id -> time we last left/joined the VC ourselves

    async def _edit_media_with_retry(self, message: Message, media_obj: InputMediaPhoto, reply_markup):
        """Edit media with basic FloodWait handling."""
        try:
            return await message.edit_media(media=media_obj, reply_markup=reply_markup)
        except errors.FloodWait as fw:
            await asyncio.sleep(fw.value + 1)
            try:
                return await message.edit_media(media=media_obj, reply_markup=reply_markup)
            except Exception:
                return None
        except errors.MessageNotModified:
            return None
        except Exception:
            return None

    async def _send_photo_with_retry(self, chat_id: int, photo, caption: str, reply_markup):
        """Send photo with FloodWait handling."""
        try:
            return await app.send_photo(
                chat_id=chat_id,
                photo=photo,
                caption=caption,
                reply_markup=reply_markup,
            )
        except errors.FloodWait as fw:
            await asyncio.sleep(fw.value + 1)
            try:
                return await app.send_photo(
                    chat_id=chat_id,
                    photo=photo,
                    caption=caption,
                    reply_markup=reply_markup,
                )
            except Exception:
                return None
        except Exception:
            return None

    async def pause(self, chat_id: int) -> bool:
        client = await db.get_assistant(chat_id)
        try:
            await client.pause(chat_id)
            await db.playing(chat_id, paused=True)
            return True
        except (ConnectionNotFound, exceptions.NotInCallError):
            await db.playing(chat_id, paused=False)
            await db.remove_call(chat_id)
            queue.clear(chat_id)
            logger.warning(
                f"Pause requested but assistant not in call for {chat_id}, syncing state")
            return False
        except Exception as e:
            await db.playing(chat_id, paused=False)
            logger.error(f"Pause failed for {chat_id}: {e}")
            return False

    async def resume(self, chat_id: int) -> bool:
        client = await db.get_assistant(chat_id)
        try:
            await client.resume(chat_id)
            await db.playing(chat_id, paused=False)
            return True
        except (ConnectionNotFound, exceptions.NotInCallError):
            await db.playing(chat_id, paused=False)
            await db.remove_call(chat_id)
            queue.clear(chat_id)
            logger.warning(
                f"Resume requested but assistant not in call for {chat_id}, syncing state")
            return False
        except Exception as e:
            logger.error(f"Resume failed for {chat_id}: {e}")
            return False

    async def stop(self, chat_id: int) -> None:
        client = await db.get_assistant(chat_id)

        # The voice-chat session is over: reset the autoplay song counter.
        # (The autoplay ON/OFF setting itself is kept.)
        autoplay.reset_session(chat_id)
        self._ap_cache.pop(chat_id, None)
        task = self._ap_tasks.pop(chat_id, None)
        if task and not task.done():
            task.cancel()

        # Cancel any active preload tasks when stopping
        try:
            await preload.cancel_preload(chat_id)
        except Exception as e:
            logger.debug(f"Error cancelling preload for {chat_id}: {e}")

        try:
            queue.clear(chat_id)
            await db.remove_call(chat_id)
        except Exception as e:
            logger.warning(f"Error clearing queue/call for {chat_id}: {e}")

        try:
            await client.leave_call(chat_id, close=False)
            # Small delay to let group call state stabilize after leaving
            await asyncio.sleep(0.5)
        except (ConnectionNotFound, exceptions.NotInCallError):
            # Expected: userbot is not in a call
            pass
        except Exception as e:
            # Only log unexpected errors
            error_msg = str(e).lower()
            if not any(ignore in error_msg for ignore in [
                "not in a call",
                "not in the group call",
                "groupcall_forbidden",
                "no active group call",
                "call was already stopped",
                "call already disconnected"
            ]):
                logger.warning(f"Error leaving call for {chat_id}: {e}")

    async def play_media(
        self,
        chat_id: int,
        message: Message | None,
        media: Media | Track,
        seek_time: int = 0,
        message_chat_id: int = None,
    ) -> None:
        """Play media in voice chat.

        Args:
            chat_id: Where to stream audio (could be channel in channel play mode)
            message: Message to edit/delete (if any)
            media: Media object to play
            seek_time: Position to seek to (seconds)
            message_chat_id: Where to send control messages (group chat in channel play mode)
                           If None, messages go to same chat as audio (chat_id)
        """
        client = await db.get_assistant(chat_id)
        _lang = await lang.get_lang(chat_id)

        # Determine where messages should go:
        # - If message_chat_id provided (channel play): send to group
        # - Otherwise: send to same chat as audio
        target_chat_for_messages = message_chat_id if message_chat_id else chat_id

        # Generate thumbnail only if THUMB_GEN is enabled, otherwise use default
        if config.THUMB_GEN and isinstance(media, Track):
            _thumb = await thumb.generate(media)
        else:
            _thumb = config.DEFAULT_THUMB

        if not media.file_path:
            if message:
                return await message.edit_text(_lang["error_no_file"].format(config.SUPPORT_CHAT))
            else:
                logger.error(f"No file path for media in {chat_id}")
                return

        # Validate chat_id - check if it's a valid channel/group
        try:
            chat = await app.get_chat(chat_id)
            if chat.type not in [enums.ChatType.SUPERGROUP, enums.ChatType.GROUP, enums.ChatType.CHANNEL]:
                logger.error(f"Invalid chat type for {chat_id}: {chat.type}")
                if message:
                    await message.edit_text("❌ Can only play in groups/channels.")
                return
            # For channels, verify assistant is member
            if chat.type == enums.ChatType.CHANNEL:
                # Get the userbot (Pyrogram client) to access .me attribute
                userbot_client = await db.get_client(chat_id)
                if not userbot_client:
                    logger.error(f"No userbot client available for {chat_id}")
                    if message:
                        await message.edit_text("❌ No assistant available.")
                    return

                try:
                    assistant_member = await app.get_chat_member(chat_id, userbot_client.me.id)
                    if assistant_member.status == enums.ChatMemberStatus.BANNED:
                        logger.error(f"Assistant banned in channel {chat_id}")
                        if message:
                            await message.edit_text("❌ Assistant is banned in this channel.")
                        # Disable channel play
                        await db.set_cmode(chat_id, None)
                        return
                except errors.RPCError as e:
                    if "CHANNEL_INVALID" in str(e) or "USER_NOT_PARTICIPANT" in str(e):
                        logger.error(
                            f"Assistant not in channel {chat_id}: {e}")
                        if message:
                            await message.edit_text(
                                "❌ <b>Assistant not in channel!</b>\n\n"
                                f"<blockquote>Please add @{userbot_client.me.username} to the channel as admin with voice chat permissions.</blockquote>"
                            )
                        # Disable channel play
                        await db.set_cmode(chat_id, None)
                        return
        except errors.RPCError as e:
            if "CHANNEL_INVALID" in str(e):
                logger.error(f"Invalid channel {chat_id}: {e}")
                if message:
                    await message.edit_text("❌ Invalid channel. Disabling channel play.")
                await db.set_cmode(chat_id, None)  # Disable channel play
                return
            raise

        # Configure audio stream with optimized buffering for lag-free playback
        # PERFORMANCE FIX: Increased buffers prevent stuttering/lagging during playback
        if seek_time > 1:
            # Seeking: Still need buffers but skip to position first
            ffmpeg_params = f"-ss {seek_time} -probesize 10M -analyzeduration 5M -rtbufsize 5M -fflags +genpts+igndts"
        else:
            # Normal playback with aggressive buffering:
            # - probesize 10M: Large input buffer (prevents underruns)
            # - analyzeduration 5M: Analyze more data (better format detection)
            # - rtbufsize 5M: Real-time buffer (crucial for network streams)
            # - fflags +genpts+igndts: Generate PTS, ignore DTS (smooth playback)
            # - sync ext: External sync (reduces A/V desync)
            ffmpeg_params = "-probesize 10M -analyzeduration 5M -rtbufsize 5M -fflags +genpts+igndts -sync ext"

        is_video = getattr(media, "video", False)
        video_flags = (
            types.MediaStream.Flags.AUTO_DETECT
            if is_video
            else types.MediaStream.Flags.IGNORE
        )

        stream = types.MediaStream(
            media_path=media.file_path,
            audio_parameters=types.AudioQuality.STUDIO,
            audio_flags=types.MediaStream.Flags.REQUIRED,
            video_flags=video_flags,
            ffmpeg_parameters=ffmpeg_params,
        )

        self._transition[chat_id] = asyncio.get_event_loop().time()
        try:
            call = await client.get_call(chat_id)
            if call:
                logger.debug(
                    f"Already connected to {chat_id}, leaving before reconnecting...")
                await client.leave_call(chat_id, close=False)
        except (ConnectionNotFound, exceptions.NotInCallError):
            pass
        except Exception as e:
            logger.debug(f"Error checking connection state for {chat_id}: {e}")

        max_retries = 3
        retry_delay = 1

        try:
            for attempt in range(max_retries):
                try:
                    await client.play(
                        chat_id=chat_id,
                        stream=stream,
                        config=types.GroupCallConfig(auto_start=True),
                    )
                    break
                except (exceptions.NoActiveGroupCall, errors.RPCError) as e:
                    error_msg = str(e)
                    if "GROUPCALL_INVALID" in error_msg or "GROUPCALL" in error_msg or isinstance(e, exceptions.NoActiveGroupCall):
                        if attempt < max_retries - 1:
                            logger.debug(
                                f"Group call transitioning for {chat_id}, retrying in {retry_delay}s... (attempt {attempt + 1}/{max_retries})")
                            await asyncio.sleep(retry_delay)
                            continue
                        else:
                            raise
                    else:
                        raise
                except Exception as e:
                    error_msg = str(e).lower()
                    if "cannot be initialized more than once" in error_msg or "connection" in error_msg:
                        if attempt < max_retries - 1:
                            logger.debug(
                                f"Connection error for {chat_id}, leaving and retrying... (attempt {attempt + 1}/{max_retries})")
                            try:
                                await client.leave_call(chat_id, close=False)
                                await asyncio.sleep(retry_delay)
                            except Exception:
                                pass
                            continue
                        else:
                            raise
                    else:
                        raise

            self._transition[chat_id] = asyncio.get_event_loop().time()
            if seek_time:
                media.time = seek_time
            else:
                media.time = 1

            if not seek_time:
                await db.add_call(chat_id)
                # Autoplay bookkeeping: song counter + "already played" list
                autoplay.note_played(chat_id, media)
                self._autoplay_prefetch(chat_id)
                if getattr(media, "user", None) == "Autoplay":
                    asyncio.create_task(self._autoplay_log(chat_id, media))
                text = _lang["play_media"].format(
                    media.url,
                    media.title,
                    media.duration,
                    media.user,
                )
                if not media.is_live and media.duration_sec:
                    import time as time_module
                    played = media.time
                    duration = media.duration_sec
                    bar_length = 12
                    if duration == 0:
                        percentage = 0
                    else:
                        percentage = min((played / duration) * 100, 100)
                    filled = int(round(bar_length * percentage / 100))
                    timer_bar = "━" * filled + "𐙚" + \
                        "─" * (bar_length - filled)
                    if duration >= 3600:
                        played_time = time_module.strftime(
                            '%H:%M:%S', time_module.gmtime(played))
                        total_time = time_module.strftime(
                            '%H:%M:%S', time_module.gmtime(duration))
                    else:
                        played_time = time_module.strftime(
                            '%M:%S', time_module.gmtime(played))
                        total_time = time_module.strftime(
                            '%M:%S', time_module.gmtime(duration))
                    timer_text = f"{played_time} {timer_bar} {total_time}"
                    keyboard = buttons.controls(chat_id, timer=timer_text)
                else:
                    keyboard = buttons.controls(chat_id)

                if message:
                    try:
                        await message.delete()
                    except Exception:
                        pass

                sent_photo = await self._send_photo_with_retry(
                    chat_id=target_chat_for_messages,
                    photo=_thumb,
                    caption=text,
                    reply_markup=keyboard,
                )
                if sent_photo:
                    media.message_id = sent_photo.id

                try:
                    asyncio.create_task(
                        preload.start_preload(chat_id, count=2))
                except Exception as e:
                    logger.debug(f"Error starting preload for {chat_id}: {e}")
        except FileNotFoundError:
            if message:
                try:
                    await message.edit_text(_lang["error_no_file"].format(config.SUPPORT_CHAT))
                except Exception:
                    pass
            await self.play_next(chat_id)
        except exceptions.NoActiveGroupCall:
            await self.stop(chat_id)
            if message:
                try:
                    await message.edit_text(_lang["error_vc_disabled"])
                except Exception:
                    pass
        except errors.RPCError as e:
            error_str = str(e)

            if any(x in error_str for x in ["CHAT_ADMIN_REQUIRED", "phone.CreateGroupCall", "GROUPCALL_FORBIDDEN", "GROUPCALL_CREATE_FORBIDDEN", "VOICE_MESSAGES_FORBIDDEN"]):
                await self.stop(chat_id)
                if message:
                    try:
                        await message.edit_text(_lang["error_vc_disabled"])
                    except Exception:
                        pass
            elif "GROUPCALL_INVALID" in error_str or "GROUPCALL" in error_str:
                await self.stop(chat_id)
                if message:
                    try:
                        await message.edit_text(_lang["error_no_call"])
                    except Exception:
                        pass
            else:
                logger.error(f"RPC error in play_media for {chat_id}: {e}")
                await self.stop(chat_id)
        except exceptions.NoAudioSourceFound:
            if message:
                try:
                    await message.edit_text(_lang["error_no_audio"])
                except Exception:
                    pass
            await self.play_next(chat_id)
        except (ConnectionNotFound, TelegramServerError):
            await self.stop(chat_id)
            if message:
                try:
                    await message.edit_text(_lang["error_tg_server"])
                except Exception:
                    pass
        except TimeoutError as e:
            error_msg = str(e)
            logger.warning(
                f"⏱️ Timeout joining voice chat {chat_id}: {error_msg}")
            await self.stop(chat_id)
            if message:
                try:
                    await message.edit_text(
                        "⏱️ <b>Connection timed out!</b>\n\n"
                        "<blockquote>Failed to join voice chat. Please check your network and try again.</blockquote>"
                    )
                except Exception:
                    pass
            await asyncio.sleep(2)
            await self.play_next(chat_id)
        except Exception as e:
            logger.error(
                f"Unexpected error in play_media for {chat_id}: {e}", exc_info=True)
            await self.stop(chat_id)
            if message:
                try:
                    await message.edit_text(f"❌ Playback error: {str(e)[:100]}")
                except Exception:
                    pass

    async def replay(self, chat_id: int) -> None:
        try:
            if not await db.get_call(chat_id):
                return

            message_chat_id = None
            try:
                chat = await app.get_chat(chat_id)
                if chat.type == enums.ChatType.CHANNEL:
                    group_id = await db.get_group_for_channel(chat_id)
                    if group_id:
                        message_chat_id = group_id
            except Exception:
                pass

            media = queue.get_current(chat_id)
            _lang = await lang.get_lang(chat_id)
            target_chat = message_chat_id if message_chat_id else chat_id
            msg = await app.send_message(chat_id=target_chat, text=_lang["play_again"])
            await self.play_media(chat_id, msg, media, message_chat_id=message_chat_id)
        except Exception as e:
            logger.error(f"Error in replay for {chat_id}: {e}", exc_info=True)

    async def seek_stream(self, chat_id: int, seconds: int) -> bool:
        """Seek to a specific position in the current stream."""
        try:
            if not await db.get_call(chat_id):
                return False

            media = queue.get_current(chat_id)
            if not media or media.is_live:
                return False

            client = await db.get_assistant(chat_id)
            _lang = await lang.get_lang(chat_id)

            message_chat_id = None
            try:
                chat = await app.get_chat(chat_id)
                if chat.type == enums.ChatType.CHANNEL:
                    group_id = await db.get_group_for_channel(chat_id)
                    if group_id:
                        message_chat_id = group_id
            except Exception:
                pass

            media.time = seconds

            target_chat = message_chat_id if message_chat_id else chat_id

            try:
                msg = await app.get_messages(target_chat, media.message_id)
            except Exception:
                msg = None

            if not msg:
                _lang = await lang.get_lang(chat_id)
                msg = await app.send_message(chat_id=target_chat, text=_lang["seeking"])

            await self.play_media(chat_id, msg, media, seek_time=seconds, message_chat_id=message_chat_id)
            return True
        except Exception as e:
            logger.warning(f"Seek stream failed for {chat_id}: {e}")
            return False

    # ------------------------------------------------------------------
    # AUTOPLAY HELPERS
    # ------------------------------------------------------------------
    async def _autoplay_limit_leave(self, chat_id: int, target_chat: int) -> None:
        """Autoplay session hit the song limit: say goodbye and leave the VC."""
        text = (
            "<blockquote>"
            f"\u2726 {autoplay.LIMIT} songs played \u2726\n"
            "Autoplay session finished, leaving the voice chat now.\n"
            "Send /play whenever you want to start again."
            "</blockquote>"
        )
        try:
            await app.send_message(chat_id=target_chat, text=text)
        except Exception as e:
            logger.debug(f"Could not send autoplay limit message in {chat_id}: {e}")
        await self.stop(chat_id)

    async def _autoplay_log(self, chat_id: int, media) -> None:
        """Tell the logger group that this song is playing via autoplay."""
        try:
            if not await db.is_logger() or chat_id == app.logger:
                return
            try:
                chat = await app.get_chat(chat_id)
                chat_name = chat.title or str(chat_id)
            except Exception:
                chat_name = str(chat_id)
            text = (
                f"<u>{app.name} \u1d00\u1d1c\u1d1b\u1d0f\u1d18\u029f\u1d00\u028f \u029f\u1d0f\u0262</u>\n\n"
                f"<b>Chat:</b> <code>{chat_id}</code> | {chat_name}\n"
                f"<b>Mode:</b> Autoplay \U0001F3B2\n\n"
                f"<b>This song is playing via autoplay</b>\n"
                f"<b>Title:</b> {media.title}\n"
                f"<b>Duration:</b> {media.duration} min"
            )
            await app.send_message(chat_id=app.logger, text=text)
        except Exception as e:
            logger.debug(f"Autoplay log failed for {chat_id}: {e}")

    async def _autoplay_mix(self, video_id: str) -> list:
        """YouTube's own "Mix" for a video (same list YouTube Music uses for
        its radio): songs with the same mood / language / artist style."""
        url = ("https://www.youtube.com/youtubei/v1/next"
               "?key=AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8")
        body = {
            "context": {"client": {
                "clientName": "WEB", "clientVersion": "2.20240814.00.00",
                "hl": "en", "gl": "IN",
            }},
            "videoId": video_id,
            "playlistId": f"RD{video_id}",
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, json=body, timeout=aiohttp.ClientTimeout(total=12)
                ) as resp:
                    if resp.status != 200:
                        logger.info(f"Autoplay mix HTTP {resp.status} for {video_id}")
                        return []
                    data = await resp.json(content_type=None)
        except Exception as e:
            logger.info(f"Autoplay mix failed for {video_id}: {e}")
            return []

        try:
            items = (data["contents"]["twoColumnWatchNextResults"]
                     ["playlist"]["playlist"]["contents"])
        except Exception:
            return []

        out = []
        for it in items:
            r = it.get("playlistPanelVideoRenderer")
            if not r:
                continue
            try:
                vid = r["videoId"]
                title = (r.get("title") or {}).get("simpleText") or "Unknown"
                length = (r.get("lengthText") or {}).get("simpleText")
                chan = ((r.get("shortBylineText") or {}).get("runs") or [{}])[0].get("text")
                thumbs = (r.get("thumbnail") or {}).get("thumbnails") or [{}]
                out.append({
                    "id": vid, "title": title, "duration": length,
                    "channel": chan,
                    "thumbnail": (thumbs[-1].get("url") or "").split("?")[0],
                })
            except Exception:
                continue
        return out

    def _autoplay_accept(self, chat_id: int, anchor, vid, title, duration) -> bool:
        """Is this search/mix result a good next song?"""
        if not vid or autoplay.was_played(chat_id, vid):
            return False
        if autoplay.is_same_song(chat_id, title):
            return False            # same song, other upload (lyrics, audio...)
        if autoplay.is_junk(title):
            return False            # jukebox / mashup / 1 hour / shorts ...
        if not duration or duration == "LIVE":
            return False
        try:
            sec = utils.to_seconds(duration)
        except Exception:
            return False
        if sec < 90 or sec > min(config.DURATION_LIMIT, 600):
            return False
        if anchor and autoplay.wrong_vibe(
            getattr(anchor, "title", ""), getattr(anchor, "channel_name", ""), title
        ):
            return False            # sad song -> no party song
        return True

    async def _autoplay_candidates(self, chat_id: int) -> list:
        last = autoplay.get_last(chat_id)
        anchor = autoplay.get_anchor(chat_id)
        if not last:
            return []

        raw = []   # (id, title, duration, channel, thumbnail)
        # 1) YouTube Mix of the song that just played, then of the song the
        #    user originally chose (keeps the mood from drifting away).
        for src in (last, anchor):
            if src is None:
                continue
            for m in await self._autoplay_mix(src.id):
                raw.append(m)
            if len(raw) >= 8:
                break

        found, seen = [], set()

        def collect(items):
            for m in items:
                vid = m["id"]
                if vid in seen:
                    continue
                if self._autoplay_accept(chat_id, anchor, vid, m["title"], m["duration"]):
                    seen.add(vid)
                    found.append(m)

        collect(raw)

        # 2) Fallback: mood + language search (never the song's own name,
        #    so we don't get its lyric video / reuploads).
        if len(found) < 3:
            title = getattr(anchor, "title", "") or ""
            chan = getattr(anchor, "channel_name", "") or ""
            for q in autoplay.mood_queries(title, chan):
                try:
                    res = (await VideosSearch(q, limit=15).next()).get("result", [])
                except Exception as e:
                    logger.debug(f"Autoplay search failed for '{q}': {e}")
                    continue
                items = []
                for r in res:
                    th = r.get("thumbnails") or [{}]
                    items.append({
                        "id": r.get("id"), "title": r.get("title") or "Unknown",
                        "duration": r.get("duration"),
                        "channel": (r.get("channel") or {}).get("name"),
                        "thumbnail": (th[-1].get("url") or "").split("?")[0],
                    })
                collect(items)
                if len(found) >= 6:
                    break

        tracks = []
        for m in found:
            try:
                sec = utils.to_seconds(m["duration"])
            except Exception:
                continue
            tracks.append(Track(
                id=m["id"],
                channel_name=m.get("channel"),
                duration=m["duration"],
                duration_sec=sec,
                message_id=0,
                title=(m["title"] or "Unknown")[:25],
                thumbnail=m.get("thumbnail"),
                url=f"https://www.youtube.com/watch?v={m['id']}",
                is_live=False,
            ))
        return tracks

    async def _autoplay_pick(self, chat_id: int):
        """Find + fully download a related song. Does NOT touch the queue."""
        candidates = await self._autoplay_candidates(chat_id)
        if not candidates:
            return None

        # Mix order is already "most related first": keep the best few in
        # play but shuffle them a little so it is not always the same one.
        head = candidates[:5]
        random.shuffle(head)
        candidates = head + candidates[5:]

        for track in candidates[:6]:
            track.user = "Autoplay"
            try:
                track.file_path = await yt.download(
                    track.id, is_live=False, video=False
                )
            except Exception as e:
                logger.warning(f"Autoplay download error for {track.id}: {e}")
                track.file_path = None

            # Never try this song again this session, good or bad.
            autoplay.mark_played(chat_id, track.id, track.title)

            if track.file_path and os.path.exists(track.file_path):
                seed = autoplay.get_last(chat_id)
                logger.info(
                    f"🎲 Autoplay: '{getattr(seed, 'title', '?')}' -> "
                    f"'{track.title}' ({track.id}) in {chat_id}"
                )
                return track

        logger.info(f"Autoplay found nothing playable for {chat_id}")
        return None

    async def _autoplay_get(self, chat_id: int):
        """Next autoplay song: prefetched one if it matches the song that
        just played, otherwise pick a fresh one."""
        lock = self._ap_locks.setdefault(chat_id, asyncio.Lock())
        async with lock:
            if not autoplay.is_enabled(chat_id):
                return None
            seed = autoplay.get_last(chat_id)
            if not seed:
                return None
            cached = self._ap_cache.get(chat_id)
            if cached and cached[0] == seed.id:
                track = cached[1]
                if track.file_path and os.path.exists(track.file_path):
                    return track
            self._ap_cache.pop(chat_id, None)
            try:
                track = await self._autoplay_pick(chat_id)
            except Exception as e:
                logger.warning(f"Autoplay pick crashed for {chat_id}: {e}", exc_info=True)
                track = None
            if track:
                self._ap_cache[chat_id] = (seed.id, track)
            return track

    def _autoplay_prefetch(self, chat_id: int) -> None:
        """While a song plays, already find + download the next autoplay song
        so there is no silence / failure at the moment the song ends."""
        if not autoplay.is_enabled(chat_id) or autoplay.limit_reached(chat_id):
            return
        task = self._ap_tasks.get(chat_id)
        if task and not task.done():
            return
        try:
            self._ap_tasks[chat_id] = asyncio.create_task(self._autoplay_get(chat_id))
        except Exception as e:
            logger.debug(f"Autoplay prefetch not started for {chat_id}: {e}")

    async def _autoplay_take(self, chat_id: int):
        """Queue is empty: get the next autoplay song and make it the
        (only) item of the queue. Retries once before giving up."""
        for attempt in range(2):
            track = await self._autoplay_get(chat_id)
            if track:
                self._ap_cache.pop(chat_id, None)
                queue.clear(chat_id)
                queue.add(chat_id, track)
                return track
            if not autoplay.is_enabled(chat_id):
                return None
            await asyncio.sleep(2)
        return None

    async def _resolve_playable(self, chat_id: int, media):
        """Make sure `media` has a downloaded file. If a song can't be
        downloaded, SKIP it (and try the next / autoplay) instead of
        leaving the voice chat."""
        for _ in range(8):
            if media is None:
                media = await self._autoplay_take(chat_id)
                if media is None:
                    return None
            if media.file_path and os.path.exists(media.file_path):
                return media
            try:
                media.file_path = await yt.download(
                    media.id,
                    is_live=getattr(media, "is_live", False),
                    video=getattr(media, "video", False),
                )
            except Exception as e:
                logger.warning(f"Download error for {media.id}: {e}")
                media.file_path = None
            if media.file_path:
                return media
            logger.warning(f"Skipping '{getattr(media, 'title', media.id)}' - download failed")
            media = queue.get_next(chat_id)   # drops the failed one
        return None

    async def play_next(self, chat_id: int) -> None:
        if chat_id not in self._play_next_locks:
            self._play_next_locks[chat_id] = asyncio.Lock()

        lock = self._play_next_locks[chat_id]

        if lock.locked():
            logger.info(
                f"play_next already running for {chat_id}, skipping duplicate call")
            return

        async with lock:
            try:
                if not await db.get_call(chat_id):
                    return

                message_chat_id = None
                try:
                    chat = await app.get_chat(chat_id)
                    if chat.type == enums.ChatType.CHANNEL:
                        group_id = await db.get_group_for_channel(chat_id)
                        if group_id:
                            message_chat_id = group_id
                except Exception:
                    pass

                target_chat = message_chat_id if message_chat_id else chat_id

                loop_mode = await db.get_loop(chat_id)

                if loop_mode == 1:
                    media = queue.get_current(chat_id)
                    if media:
                        _lang = await lang.get_lang(chat_id)
                        try:
                            msg = await app.send_message(chat_id=target_chat, text=_lang["play_again"])
                            await self.play_media(chat_id, msg, media, message_chat_id=message_chat_id)
                        except errors.ChannelPrivate:
                            logger.warning(
                                f"Bot removed from {chat_id}, cleaning up")
                            try:
                                await self.leave_call(chat_id)
                            except (AttributeError, Exception) as leave_ex:
                                logger.debug(
                                    f"Could not leave call for {chat_id}: {leave_ex}")
                            await db.rm_chat(chat_id)
                        return

                # Autoplay session limit: after LIMIT songs, say goodbye and leave.
                if autoplay.limit_reached(chat_id):
                    await self._autoplay_limit_leave(chat_id, target_chat)
                    return

                media = queue.get_next(chat_id)

                if not media and loop_mode == 10:
                    all_items = queue.get_all(chat_id)
                    if all_items:
                        first_track = all_items[0]
                        _lang = await lang.get_lang(chat_id)
                        try:
                            msg = await app.send_message(chat_id=target_chat, text="🔁 Looping queue...")
                            if not first_track.file_path:
                                is_live = getattr(
                                    first_track, 'is_live', False)
                                first_track.file_path = await yt.download(
                                    first_track.id,
                                    is_live=is_live,
                                    video=getattr(first_track, 'video', False),
                                )
                            first_track.message_id = msg.id
                            await self.play_media(chat_id, msg, first_track, message_chat_id=message_chat_id)
                        except errors.ChannelPrivate:
                            logger.warning(
                                f"Bot removed from {chat_id}, cleaning up")
                            await self.leave_call(chat_id)
                            await db.rm_chat(chat_id)
                        return

                try:
                    if media and media.message_id:
                        await app.delete_messages(
                            chat_id=chat_id,
                            message_ids=media.message_id,
                            revoke=True,
                        )
                        media.message_id = 0
                except Exception as e:
                    logger.debug(
                        f"Could not delete previous message in {chat_id}: {e}")

                # Next song: queue -> autoplay. Download failures are skipped,
                # they never make the bot leave the voice chat.
                media = await self._resolve_playable(chat_id, media)

                if not media:
                    if autoplay.is_enabled(chat_id):
                        try:
                            await app.send_message(
                                chat_id=target_chat,
                                text="<blockquote>\u2726 Autoplay could not find a playable song right now. "
                                     "Send /play to continue.</blockquote>",
                            )
                        except Exception:
                            pass
                    elif config.AUTO_END:
                        _lang = await lang.get_lang(chat_id)
                        try:
                            await app.send_message(
                                chat_id=chat_id,
                                text=_lang.get(
                                    "auto_end", "✅ Queue finished. Stream ended automatically.")
                            )
                        except Exception as e:
                            logger.debug(
                                f"Could not send auto_end message in {chat_id}: {e}")
                    return await self.stop(chat_id)

                _lang = await lang.get_lang(chat_id)
                msg = None

                try:
                    msg = await app.send_message(chat_id=target_chat, text=_lang["play_next"])
                except errors.FloodWait as fw:
                    # Do not block playback on UI flood waits; continue without message.
                    logger.warning(
                        f"FloodWait in play_next for {chat_id}: skipping status message ({fw.value}s)")
                    msg = None
                except errors.ChannelPrivate:
                    logger.warning(f"Bot removed from {chat_id}, cleaning up")
                    await self.leave_call(chat_id)
                    await db.rm_chat(chat_id)
                    return
                except Exception as e:
                    logger.error(
                        f"Failed to send play_next message for {chat_id}: {e}")
                    msg = None

                media.message_id = msg.id if msg else 0
                if msg:
                    await self.play_media(chat_id, msg, media, message_chat_id=message_chat_id)
                else:
                    logger.info(
                        f"Playing next track for {chat_id} without message update")
                    await self.play_media(chat_id, None, media, message_chat_id=message_chat_id)

                try:
                    asyncio.create_task(
                        preload.start_preload(chat_id, count=2))
                except Exception as e:
                    logger.debug(
                        f"Error starting preload after play_next for {chat_id}: {e}")
            except Exception as e:
                logger.error(
                    f"Error in play_next for {chat_id}: {e}", exc_info=True)
                try:
                    await self.stop(chat_id)
                except Exception:
                    pass

    async def ping(self) -> float:
        pings = [client.ping for client in self.clients]
        return round(sum(pings) / len(pings), 2)

    async def decorators(self, client: PyTgCalls) -> None:
        for client in self.clients:
            @client.on_update()
            async def update_handler(_, update: types.Update) -> None:
                if isinstance(update, types.StreamEnded):
                    if update.stream_type == types.StreamEnded.Type.AUDIO:
                        chat_id = update.chat_id
                        current_time = asyncio.get_event_loop().time()

                        if chat_id in self._stream_end_cache:
                            if current_time - self._stream_end_cache[chat_id] < 2.0:
                                return

                        self._stream_end_cache[chat_id] = current_time

                        self._stream_end_cache = {
                            cid: t for cid, t in self._stream_end_cache.items()
                            if current_time - t < 5.0
                        }

                        await self.play_next(chat_id)
                elif isinstance(update, types.ChatUpdate):
                    # Ignore the LEFT_GROUP echo of our own leave+rejoin when
                    # switching songs (it used to kill the next song).
                    if update.status == types.ChatUpdate.Status.LEFT_GROUP:
                        t0 = self._transition.get(update.chat_id, 0)
                        if asyncio.get_event_loop().time() - t0 < 8 and queue.get_current(update.chat_id):
                            return
                    if update.status in [
                        types.ChatUpdate.Status.KICKED,
                        types.ChatUpdate.Status.LEFT_GROUP,
                        types.ChatUpdate.Status.CLOSED_VOICE_CHAT,
                    ]:
                        await self.stop(update.chat_id)

    async def boot(self) -> None:
        PyTgCallsSession.notice_displayed = True
        for ub in userbot.clients:
            client = PyTgCalls(ub, cache_duration=100)
            await client.start()
            self.clients.append(client)
            await self.decorators(client)
        logger.info("📞 PyTgCalls client(s) started.")
