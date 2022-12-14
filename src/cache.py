import json
import logging
import os
from typing import Any, Dict, Optional

from redis import asyncio as aioredis
from redis.exceptions import ConnectionError

from .api import song

log = logging.getLogger(__name__)


class Cache:
    def __init__(self, redis_host: str = "redis://localhost"):
        self.redis_host = redis_host
        self.redis_conn = aioredis.from_url(redis_host)

        self._have_pinged: bool = False
        self._is_using_redis: bool = True

        # Cache to use if we cannot make a connection to Redis.
        self._cache_fallback: Dict[str, bytes] = {}

    async def _do_redis_ping(self) -> None:
        if not self._have_pinged:
            try:
                await self.redis_conn.ping()

                self._is_using_redis = True
                log.info("Successfully connected to the Redis host")
            except ConnectionError:
                self._is_using_redis = False
                log.warning(
                    "Failed to connect to the Redis host, using fallback cache system"
                )

            self._have_pinged = True

    async def get(self, key: str) -> Optional[bytes]:
        await self._do_redis_ping()

        if self._is_using_redis:
            return await self.redis_conn.get(key)
        else:
            return self._cache_fallback.get(key)

    async def set(self, key: str, value: Any, encoding: str = "utf-8") -> None:
        await self._do_redis_ping()

        if self._is_using_redis:
            await self.redis_conn.set(key, value)
        else:
            self._cache_fallback[key] = str(value).encode(encoding)


_cache = Cache(os.getenv("REDIS_HOST", "redis://localhost"))


async def set_empty_from_info(media_info: dict, scan_start: int = 0) -> None:
    """
    Add any unidentified song to the Redis cache.

    Args:
        media_info (dict): Data we get from `YoutubeDL.extract_info`.
        scan_start (int): Timestamp (in seconds) at which the audio was scanned from.

    Returns:
        None.
    """
    key_format = [media_info["extractor_key"], media_info["id"], str(scan_start)]
    key_string = "-".join(key_format)

    await _cache.set(key_string, "{}")


async def set_from_info(media_info: dict, song_info: dict, scan_start: int = 0) -> None:
    """
    Add any identified song to the Redis cache.

    Args:
        media_info (dict): Data we get from `YoutubeDL.extract_info`.
        song_info (dict): Data we get from `Shazam.recognize_song`.
        scan_start (int): Timestamp (in seconds) at which the audio was scanned from.

    Returns:
        None.
    """
    key_format = [media_info["extractor_key"], media_info["id"], str(scan_start)]
    key_string = "-".join(key_format)

    value_data = song.create(song_info)
    value_encoded = json.dumps(value_data, separators=(",", ":"))

    await _cache.set(key_string, value_encoded)


async def get_from_info(media_info: dict, scan_start: int = 0) -> Optional[song.Song]:
    """
    Get any identified song from the Redis cache.

    Args:
        media_info (dict): Data we get from `YoutubeDL.extract_info`.
        scan_start (int): Timestamp (in seconds) at which the audio was scanned from.

    Returns:
        Any identified songs, or none.
    """
    key_format = [media_info["extractor_key"], media_info["id"], str(scan_start)]
    key_string = "-".join(key_format)

    data = await _cache.get(key_string)

    if data is None:
        return

    data_decoded = json.loads(data.decode("utf-8"))

    return song.Song(**data_decoded)
