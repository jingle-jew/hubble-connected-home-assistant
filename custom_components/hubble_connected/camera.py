"""Camera entity for Hubble Connected."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.components.stream import Stream
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HubbleConfigEntry
from .const import (
    DIRECT_RTSP_PASSWORD,
    DIRECT_RTSP_PATH,
    DIRECT_RTSP_USERNAME,
    DOMAIN,
)
from .orbweb import (
    OrbwebLanMappingPool,
    OrbwebProtocolError,
    OrbwebRendezvousError,
)
from .restream import HubbleRestreamError, HubbleRtspRestreamManager
from .rtsp import HubbleRtspEndpoint, stream_options_for_backend

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass,
    entry: HubbleConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Hubble Connected camera entity."""
    entities: list[HubbleConnectedCamera] = []
    direct_hosts = entry.runtime_data.direct_rtsp_endpoints
    for spec in entry.runtime_data.local_camera_specs:
        endpoint = direct_hosts.get(spec.host)
        if endpoint is not None:
            entities.append(
                HubbleConnectedCamera(
                    endpoint,
                    stream_key=spec.host,
                    name=spec.name,
                    unique_id=f"{spec.host}:camera",
                    device_identifier=f"camera:{spec.host}",
                    backend="local_rtsp",
                )
            )

    orbweb_mappings = entry.runtime_data.orbweb_mappings
    if orbweb_mappings is not None:
        for binding in entry.runtime_data.orbweb_streams:
            if binding.key in orbweb_mappings.hosts:
                entities.append(
                    HubbleConnectedCamera(
                        None,
                        stream_key=binding.key,
                        name=binding.name,
                        unique_id=binding.unique_id,
                        device_identifier=binding.device_identifier,
                        backend="orbweb_lan",
                        orbweb_mappings=orbweb_mappings,
                        rtsp_restream=(
                            entry.runtime_data.rtsp_restream
                            if binding.key
                            in entry.runtime_data.rtsp_normalization_keys
                            else None
                        ),
                    )
                )

    if entities:
        async_add_entities(entities)


class HubbleConnectedCamera(Camera):
    """Standard RTSP stream exposed locally or through Orbweb."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(
        self,
        endpoint: HubbleRtspEndpoint | None,
        *,
        stream_key: str,
        name: str,
        unique_id: str,
        device_identifier: str,
        backend: str,
        orbweb_mappings: OrbwebLanMappingPool | None = None,
        rtsp_restream: HubbleRtspRestreamManager | None = None,
    ) -> None:
        """Initialize the camera and Home Assistant camera internals."""
        Camera.__init__(self)
        self.stream_options.update(stream_options_for_backend(backend))
        self._endpoint = endpoint
        self._backend = backend
        self._orbweb_mappings = orbweb_mappings
        self._rtsp_restream = rtsp_restream
        self._orbweb_refresh_task: asyncio.Task[None] | None = None
        self._orbweb_stream_callback_installed = False
        self._stream_key = stream_key
        self._attr_unique_id = unique_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_identifier)},
            name=name,
            manufacturer="Hubble Connected / Motorola",
            model="Hubble LAN camera",
        )

    @property
    def use_stream_for_stills(self) -> bool:
        """Use the RTSP stream to generate preview images."""
        return True

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a still frame from the Home Assistant stream worker."""
        stream = self.stream or await self.async_create_stream()
        if stream is None:
            return None
        return await stream.async_get_image(
            width=width,
            height=height,
            wait_for_next_keyframe=False,
        )

    async def stream_source(self) -> str | None:
        """Return the RTSP source consumed by Home Assistant/go2rtc."""
        if self._backend == "orbweb_lan":
            if self._orbweb_mappings is None:
                raise RuntimeError("Orbweb camera has no mapping provider")
            try:
                mapping = await self._orbweb_mappings.async_get_stable_mapping(
                    self._stream_key
                )
            except (
                EOFError,
                OSError,
                TimeoutError,
                OrbwebProtocolError,
                OrbwebRendezvousError,
            ) as err:
                self._endpoint = None
                _LOGGER.debug(
                    "Orbweb LAN stream is temporarily unavailable: %s",
                    type(err).__name__,
                )
                return None
            source_endpoint = HubbleRtspEndpoint(
                host=mapping.host,
                port=mapping.port,
                path=DIRECT_RTSP_PATH,
                username=DIRECT_RTSP_USERNAME,
                password=DIRECT_RTSP_PASSWORD,
            )
            self._endpoint = await self._async_normalize_endpoint(source_endpoint)
        if self._endpoint is None:
            raise RuntimeError("Hubble camera has no RTSP endpoint")
        return self._endpoint.url

    async def async_create_stream(self) -> Stream | None:
        """Create the HA stream and attach dynamic Orbweb recovery."""
        stream = await super().async_create_stream()
        if (
            self._backend == "orbweb_lan"
            and stream is not None
            and not self._orbweb_stream_callback_installed
        ):
            stream.set_update_callback(self._async_handle_stream_update)
            self._orbweb_stream_callback_installed = True
        return stream

    @callback
    def _async_handle_stream_update(self) -> None:
        """Refresh an expired dynamic mapping when HA marks it unavailable."""
        self.async_write_ha_state()
        stream = self.stream
        if stream is None or stream.available or self._orbweb_refresh_task is not None:
            return
        self._orbweb_refresh_task = self.hass.async_create_task(
            self._async_refresh_orbweb_source(),
            name=f"hubble_orbweb_refresh_{self.entity_id}",
        )

    async def _async_refresh_orbweb_source(self) -> None:
        """Replace an expired loopback endpoint and fast-restart HA stream."""
        try:
            if self._orbweb_mappings is None:
                return
            mapping = await self._orbweb_mappings.async_get_stable_mapping(
                self._stream_key
            )
            source_endpoint = HubbleRtspEndpoint(
                host=mapping.host,
                port=mapping.port,
                path=DIRECT_RTSP_PATH,
                username=DIRECT_RTSP_USERNAME,
                password=DIRECT_RTSP_PASSWORD,
            )
            endpoint = await self._async_normalize_endpoint(source_endpoint)
            self._endpoint = endpoint
            if self.stream is not None:
                self.stream.update_source(endpoint.url)
        except (
            EOFError,
            OSError,
            TimeoutError,
            OrbwebProtocolError,
            OrbwebRendezvousError,
        ) as err:
            _LOGGER.debug(
                "Orbweb LAN stream refresh failed temporarily: %s",
                type(err).__name__,
            )
        finally:
            self._orbweb_refresh_task = None

    async def _async_normalize_endpoint(
        self, endpoint: HubbleRtspEndpoint
    ) -> HubbleRtspEndpoint:
        """Normalize malformed 3667 media without changing other camera paths."""
        if self._rtsp_restream is None:
            return endpoint
        try:
            return await self._rtsp_restream.async_get_endpoint(
                self._stream_key, endpoint
            )
        except HubbleRestreamError as err:
            _LOGGER.warning("Hubble 3667 media normalization unavailable: %s", err)
            return endpoint

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Expose the non-secret stream backend."""
        return {
            "backend": self._backend,
            "media_path": (
                "go2rtc_normalized" if self._rtsp_restream is not None else "direct"
            ),
        }
