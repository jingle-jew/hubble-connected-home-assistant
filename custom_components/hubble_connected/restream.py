"""Model-scoped RTSP normalization through Home Assistant's go2rtc server."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from hashlib import sha256
from typing import Protocol

from .rtsp import HubbleRtspEndpoint

_GO2RTC_RTSP_HOST = "127.0.0.1"
_GO2RTC_RTSP_PORT = 18554


class HubbleRestreamError(Exception):
    """A normalized restream could not be registered."""


class Go2RtcStreamsClient(Protocol):
    """Subset of go2rtc-client used by the integration."""

    async def add(self, name: str, sources: str | list[str]) -> None:
        """Add or replace one named stream."""


type DeleteStream = Callable[[str], Awaitable[None]]


class HubbleRtspRestreamManager:
    """Create stable, credential-free RTSP outputs for malformed camera media."""

    def __init__(
        self,
        streams: Go2RtcStreamsClient,
        delete_stream: DeleteStream,
    ) -> None:
        self._streams = streams
        self._delete_stream = delete_stream
        self._sources: dict[str, str] = {}
        self._names: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def async_get_endpoint(
        self, key: str, source: HubbleRtspEndpoint
    ) -> HubbleRtspEndpoint:
        """Return a stable RTSP endpoint with normalized H.264 metadata."""
        name = self._stream_name(key)
        ffmpeg_source = self._ffmpeg_source(source)
        async with self._lock:
            if self._sources.get(key) != ffmpeg_source:
                try:
                    await self._streams.add(name, ffmpeg_source)
                except Exception as err:  # noqa: BLE001
                    raise HubbleRestreamError(
                        f"Unable to register normalized stream: {type(err).__name__}"
                    ) from err
                self._sources[key] = ffmpeg_source
                self._names[key] = name
        return HubbleRtspEndpoint(
            host=_GO2RTC_RTSP_HOST,
            port=_GO2RTC_RTSP_PORT,
            path=f"/{name}",
        )

    async def async_close(self) -> None:
        """Remove all streams registered by this manager."""
        async with self._lock:
            names = tuple(self._names.values())
            self._sources.clear()
            self._names.clear()
        errors: list[Exception] = []
        for name in names:
            try:
                await self._delete_stream(name)
            except Exception as err:  # noqa: BLE001
                errors.append(err)
        if errors:
            raise HubbleRestreamError(
                f"Unable to remove {len(errors)} normalized stream(s)"
            ) from errors[0]

    @staticmethod
    def _stream_name(key: str) -> str:
        digest = sha256(key.encode()).hexdigest()[:16]
        return f"hubble_connected_normalized_{digest}"

    @staticmethod
    def _ffmpeg_source(source: HubbleRtspEndpoint) -> str:
        return f"ffmpeg:{source.url}#video=copy#audio=copy#query=log_level=debug"
