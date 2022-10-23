import asyncio
import mimetypes
import os
from tempfile import TemporaryDirectory
from typing import Optional

import aiofiles
import aiohttp
import ffmpeg
import validators
from shazamio import Shazam
from yt_dlp import YoutubeDL

from . import cache
from .api import song
from .exceptions import InvalidLinkException
from .utility import timestamp_from_extractor

_shazam = Shazam()


async def download_file(link: str, path: str) -> None:
    """Downloads a file from a URL to the given path."""
    async with aiohttp.ClientSession() as session:
        async with session.get(link) as resp:
            mime = resp.headers.get("Content-Type", "audio/mp3")
            path = path.format(ext=mimetypes.guess_extension(mime))

            async with aiofiles.open(path, "wb") as file:
                await file.write(await resp.content.read())


async def download_media(
    link: str,
    path: Optional[str] = None,
    fmt: Optional[str] = None,
    index: Optional[int] = 1,
    download: bool = True,
) -> Optional[dict]:
    """Downloads a given piece of media to a path. If no YoutubeDL information
    is found, it is downloaded directly instead."""
    loop = asyncio.get_event_loop()
    opts = {"quiet": True, "outtmpl": path, "playlist_items": str(index)}

    if fmt:
        opts["format"] = fmt

    with YoutubeDL(opts) as dl:
        return await loop.run_in_executor(
            None, lambda: dl.extract_info(link, download=download)
        )


async def find_song(
    link: str,
    timestamp: Optional[int] = None,
    duration: int = 15,
    use_cache: bool = True,
) -> Optional[song.Song]:
    """Try to find a song given a URL and timestamp."""
    if not validators.url(link):  # type: ignore
        raise InvalidLinkException

    loop = asyncio.get_event_loop()

    with TemporaryDirectory() as path_temp:
        # Download the file to the temporary path.
        data_media = await download_media(link, fmt="worstaudio/worst", download=False)

        # Some extractors have a `&t=` query parameter to denote the timestamp.
        # If we're not provided an explicit timestamp, try to get it from here
        # instead.
        if data_media and timestamp is None:
            timestamp = timestamp_from_extractor(link, data_media["extractor"])

        # Fallback value if we don't find anything from the extractor.
        if timestamp is None:
            timestamp = 0

        # Check for existing cache in Redis.
        if use_cache:
            maybe_song = await cache.get_from_info(data_media, timestamp)

            if maybe_song is not None:
                if len(maybe_song.keys()) == 0:
                    return

                return maybe_song

        file_path_audio = os.path.join(path_temp, "audio.ogg")

        def _download_media():
            ffmpeg.input(
                data_media["url"] if data_media else link,
                ss=(str(timestamp)),
                t=duration,
            ).output(file_path_audio, vn=None).run(quiet=True)

        await loop.run_in_executor(None, lambda: _download_media())

        data_shazam = await _shazam.recognize_song(file_path_audio)

        if use_cache and len(data_shazam["matches"]) == 0:
            await cache.set_empty_from_info(data_media, timestamp)
            return

        data_song = song.create(data_shazam)

        if use_cache and data_media is not None:
            await cache.set_from_info(data_media, data_shazam, timestamp)

        return data_song
