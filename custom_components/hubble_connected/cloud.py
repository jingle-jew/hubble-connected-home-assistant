"""Hubble cloud authentication and camera inventory boundary.

This module deliberately contains no Home Assistant imports.  Keeping the
vendor API at a narrow boundary makes it possible to test response parsing
without a live account and to replace the transport independently later.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, TypeVar
from urllib.parse import quote

API_BASE_URL = "https://api.hubble.in"
AUTH_PATH = "/v4/users/authentication_token.json"
CAMERAS_PATH = "/v6/devices/own.json"
SUBSCRIPTIONS_PATH = "/v2/devices/subscriptions"
REQUEST_TIMEOUT = 10
JOB_POLL_INTERVAL = 2.0
JOB_POLL_ATTEMPTS = 10

_LOGGER = logging.getLogger(__name__)

CommandValue = TypeVar("CommandValue")

APP_HEADERS = {
    "Content-Type": "application/json; charset=UTF-8",
    "X-Application-Platform": "android",
    "X-Auth-Tenant": "hubble",
    "X-Application-Id": "hubble_gcm:6.5.70",
    "User-Agent": "HubbleConnected/6.5.70/HomeAssistant",
}


class HubbleCloudError(Exception):
    """Base error for the Hubble cloud boundary."""


class HubbleCloudAuthError(HubbleCloudError):
    """The cloud rejected the account credentials or token."""


class HubbleCloudCannotConnect(HubbleCloudError):
    """The cloud endpoint could not be reached."""


class HubbleCloudProtocolError(HubbleCloudError):
    """The cloud returned an unsupported response."""


class HubbleCloudConfigError(HubbleCloudError):
    """A manually configured cloud camera identifier is invalid."""


@dataclass(frozen=True, slots=True)
class HubbleCloudSession:
    """Authenticated cloud session; the token must never enter diagnostics."""

    token: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class HubbleCloudCamera:
    """Useful, non-lossy subset of one camera inventory record."""

    cloud_id: int | None
    name: str
    registration_id: str
    mac_address: str | None
    device_model_id: int | None
    firmware_version: str | None
    status: str | None
    is_available: bool | None
    time_zone: float | None
    updated_at: str | None
    snapshot_url: str | None
    network_strength: str | None
    remote_ip: str | None
    cloud_temperature: float | None
    settings: Mapping[str, str]
    structure_root_keys: tuple[str, ...] = ()
    structure_orbweb_keys: tuple[str, ...] = ()
    structure_device_status_keys: tuple[str, ...] = ()
    structure_device_keys: tuple[str, ...] = ()
    orbweb_sid: str | None = field(default=None, repr=False)
    orbweb_password: str | None = field(default=None, repr=False)

    @property
    def has_orbweb_credentials(self) -> bool:
        """Return whether the inventory can seed an Orbweb client."""
        return bool(self.orbweb_sid and self.orbweb_password)

    @property
    def model_code(self) -> str | None:
        """Return the four-digit model family embedded in registration IDs."""
        value = self.registration_id[2:6]
        return value if len(value) == 4 and value.isdigit() else None


def parse_authentication(payload: Any) -> HubbleCloudSession:
    """Extract an authentication token from a v4 response."""
    data = _response_data(payload)
    if not isinstance(data, Mapping):
        raise HubbleCloudProtocolError("Authentication data is not an object")
    token = data.get("authentication_token")
    if not isinstance(token, str) or not token:
        raise HubbleCloudProtocolError("Authentication token is missing")
    return HubbleCloudSession(token=token)


def parse_camera_inventory(payload: Any) -> tuple[HubbleCloudCamera, ...]:
    """Parse the v6 camera list while tolerating absent optional fields."""
    data = _response_data(payload)
    if not isinstance(data, list):
        raise HubbleCloudProtocolError("Camera inventory data is not a list")

    cameras: list[HubbleCloudCamera] = []
    for item in data:
        if not isinstance(item, Mapping):
            raise HubbleCloudProtocolError("Camera inventory item is not an object")
        cameras.append(_parse_camera_record(item))
    return tuple(cameras)


def parse_camera(payload: Any) -> HubbleCloudCamera:
    """Parse the v6 response for one camera omitted from the own inventory."""
    data = _response_data(payload)
    if not isinstance(data, Mapping):
        raise HubbleCloudProtocolError("Camera data is not an object")
    return _parse_camera_record(data)


def parse_subscription_camera_ids(payload: Any) -> tuple[str, ...]:
    """Extract account device identifiers from the subscription inventory."""
    data = _response_data(payload)
    if not isinstance(data, Mapping):
        raise HubbleCloudProtocolError("Subscription inventory data is not an object")
    devices = data.get("devices")
    if not isinstance(devices, list):
        raise HubbleCloudProtocolError("Subscription device inventory is not a list")

    identifiers: list[str] = []
    seen: set[str] = set()
    for item in devices:
        if not isinstance(item, Mapping):
            raise HubbleCloudProtocolError(
                "Subscription device inventory item is not an object"
            )
        registration_id = _optional_str(item.get("registration_id"))
        if registration_id is None:
            raise HubbleCloudProtocolError(
                "Subscription device registration_id is missing"
            )
        _validate_cloud_identifier(registration_id)
        if registration_id not in seen:
            seen.add(registration_id)
            identifiers.append(registration_id)
    return tuple(identifiers)


def parse_cloud_camera_ids(value: str) -> tuple[str, ...]:
    """Parse opaque camera identifiers separated by commas or newlines."""
    if not value.strip():
        return ()
    identifiers: list[str] = []
    seen: set[str] = set()
    for raw_identifier in re.split(r"[,\n]", value):
        identifier = raw_identifier.strip()
        if not identifier:
            continue
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", identifier):
            raise HubbleCloudConfigError("Invalid cloud camera identifier")
        if identifier in seen:
            raise HubbleCloudConfigError("Duplicate cloud camera identifier")
        seen.add(identifier)
        identifiers.append(identifier)
    return tuple(identifiers)


def parse_temperature_job(payload: Any) -> float | None:
    """Return a completed temperature value, or None while the job is pending."""
    message = parse_command_job(payload)
    if message is None:
        return None
    match = re.fullmatch(
        r"value_temperature\s*:\s*(-?\d+(?:\.\d+)?)\s*",
        message,
        re.IGNORECASE,
    )
    if match is None:
        raise HubbleCloudProtocolError("Cloud temperature response is invalid")
    temperature = float(match.group(1))
    if not -50 <= temperature <= 100:
        raise HubbleCloudProtocolError("Cloud temperature is implausible")
    return temperature


def parse_integer_job(
    payload: Any, command: str, minimum: int, maximum: int
) -> int | None:
    """Return one completed integer getter value with strict range checking."""
    message = parse_command_job(payload)
    if message is None:
        return None
    match = re.fullmatch(
        rf"{re.escape(command)}\s*:\s*(-?\d+)\s*",
        message,
        re.IGNORECASE,
    )
    if match is None:
        raise HubbleCloudProtocolError(f"Cloud {command} response is invalid")
    value = int(match.group(1))
    if not minimum <= value <= maximum:
        raise HubbleCloudProtocolError(f"Cloud {command} value is out of range")
    return value


def parse_command_job(payload: Any) -> str | None:
    """Return a completed device response message, or None while pending."""
    data = _response_data(payload)
    if not isinstance(data, Mapping):
        raise HubbleCloudProtocolError("Cloud command job data is not an object")
    status = str(data.get("status", ""))
    if status == "202":
        return None
    if status != "200":
        raise HubbleCloudProtocolError(f"Cloud command job failed with {status}")
    output = _mapping(data.get("output"))
    message = _optional_str(output.get("DeviceResponseMessage"))
    if message is None:
        raise HubbleCloudProtocolError("Cloud command response is missing")
    return message


def _parse_camera_record(item: Mapping[str, Any]) -> HubbleCloudCamera:
    """Parse one camera record shared by the v1 and v6 endpoints."""
    registration_id = _optional_str(item.get("registration_id"))
    if not registration_id:
        raise HubbleCloudProtocolError("Camera registration_id is missing")

    orbweb = _mapping(item.get("orbweb"))
    device_status = _mapping(item.get("device_status"))
    environment = _mapping(device_status.get("environment"))
    detail = _mapping(device_status.get("device"))
    settings = _parse_settings(item.get("device_settings"))

    _LOGGER.info(
        "Hubble cloud camera structure: device_model_id=%s "
        "root_keys=%s orbweb_keys=%s device_status_keys=%s device_keys=%s "
        "has_mac=%s has_orbweb=%s",
        _optional_int(item.get("device_model_id")),
        sorted(str(key) for key in item),
        sorted(str(key) for key in orbweb),
        sorted(str(key) for key in device_status),
        sorted(str(key) for key in detail),
        bool(_optional_str(item.get("mac_address"))),
        bool(
            _optional_str(orbweb.get("sid"))
            and _optional_str(orbweb.get("password"))
        ),
    )

    return HubbleCloudCamera(
        cloud_id=_optional_int(item.get("id")),
        name=_optional_str(item.get("name")) or "Hubble camera",
        registration_id=registration_id,
        mac_address=_optional_str(item.get("mac_address")),
        device_model_id=_optional_int(item.get("device_model_id")),
        firmware_version=_optional_str(item.get("firmware_version")),
        status=_optional_str(item.get("status")),
        is_available=_optional_bool(item.get("is_available")),
        time_zone=_optional_float(item.get("time_zone")),
        updated_at=_optional_str(item.get("updated_at")),
        snapshot_url=_optional_str(item.get("snaps_url")),
        network_strength=_optional_str(detail.get("network_strength")),
        remote_ip=_optional_str(detail.get("remote_ip")),
        cloud_temperature=_optional_float(environment.get("temperature")),
        settings=settings,
        structure_root_keys=tuple(sorted(str(key) for key in item)),
        structure_orbweb_keys=tuple(sorted(str(key) for key in orbweb)),
        structure_device_status_keys=tuple(sorted(str(key) for key in device_status)),
        structure_device_keys=tuple(sorted(str(key) for key in detail)),
        orbweb_sid=_optional_str(orbweb.get("sid")),
        orbweb_password=_optional_str(orbweb.get("password")),
    )


class HubbleCloudClient:
    """Small async client using a Home Assistant-managed HTTP session."""

    def __init__(
        self,
        session: Any,
        base_url: str = API_BASE_URL,
        *,
        job_poll_interval: float = JOB_POLL_INTERVAL,
    ) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._job_poll_interval = job_poll_interval

    async def async_authenticate(self, login: str, password: str) -> HubbleCloudSession:
        """Exchange account credentials for a short cloud API token."""
        payload = await self._async_json_request(
            "post",
            AUTH_PATH,
            json={"login": login, "password": password},
            authentication=True,
        )
        return parse_authentication(payload)

    async def async_get_cameras(
        self, cloud_session: HubbleCloudSession
    ) -> tuple[HubbleCloudCamera, ...]:
        """Read the account's camera inventory."""
        payload = await self._async_json_request(
            "get",
            CAMERAS_PATH,
            params={
                "suppress_response_codes": "1",
                "api_key": cloud_session.token,
            },
            authentication=True,
        )
        return parse_camera_inventory(payload)

    async def async_get_camera(
        self, cloud_session: HubbleCloudSession, registration_id: str
    ) -> HubbleCloudCamera:
        """Read one owned camera even when the v6 own inventory omits it."""
        _validate_cloud_identifier(registration_id)
        payload = await self._async_json_request(
            "get",
            f"/v6/devices/{quote(registration_id, safe='')}.json",
            params={"api_key": cloud_session.token},
            authentication=True,
        )
        camera = parse_camera(payload)
        if camera.registration_id != registration_id:
            raise HubbleCloudProtocolError("Cloud returned a different camera")
        return camera

    async def async_get_subscription_camera_ids(
        self, cloud_session: HubbleCloudSession
    ) -> tuple[str, ...]:
        """Read account device IDs that the normal owned inventory can omit."""
        payload = await self._async_json_request(
            "get",
            SUBSCRIPTIONS_PATH,
            params={"api_key": cloud_session.token},
            authentication=True,
        )
        return parse_subscription_camera_ids(payload)

    async def async_get_temperature(
        self, cloud_session: HubbleCloudSession, registration_id: str
    ) -> float:
        """Read temperature through the official publish-command job API."""
        return await self._async_get_command(
            cloud_session,
            registration_id,
            "VALUE_TEMPERATURE",
            parse_temperature_job,
        )

    async def async_get_wifi_strength(
        self, cloud_session: HubbleCloudSession, registration_id: str
    ) -> int:
        """Read the camera-reported Wi-Fi quality percentage."""
        return await self._async_get_command(
            cloud_session,
            registration_id,
            "GET_WIFI_STRENGTH",
            lambda payload: parse_integer_job(
                payload, "GET_WIFI_STRENGTH", 0, 100
            ),
        )

    async def async_get_video_bitrate(
        self, cloud_session: HubbleCloudSession, registration_id: str
    ) -> int:
        """Read the current camera video bitrate in kbit/s."""
        return await self._async_get_command(
            cloud_session,
            registration_id,
            "GET_VIDEO_BITRATE",
            lambda payload: parse_integer_job(
                payload, "GET_VIDEO_BITRATE", 0, 100_000
            ),
        )

    async def _async_get_command(
        self,
        cloud_session: HubbleCloudSession,
        registration_id: str,
        command: str,
        parser: Callable[[Any], CommandValue | None],
    ) -> CommandValue:
        """Publish one read-only getter and wait for its asynchronous job."""
        _validate_cloud_identifier(registration_id)
        payload = await self._async_json_request(
            "post",
            f"/v1/devices/{quote(registration_id, safe='')}/publish_command.json",
            params={"api_key": cloud_session.token},
            json={
                "api_key": cloud_session.token,
                "command": command,
                "attributes": None,
            },
            authentication=True,
        )
        job_id = _parse_publish_job(payload)
        for _attempt in range(JOB_POLL_ATTEMPTS):
            await asyncio.sleep(self._job_poll_interval)
            job_payload = await self._async_json_request(
                "get",
                f"/v1/jobs/{quote(job_id, safe='')}",
                params={"api_key": cloud_session.token},
                authentication=True,
            )
            value = parser(job_payload)
            if value is not None:
                return value
        raise HubbleCloudCannotConnect("Cloud command job timed out")

    async def _async_json_request(
        self,
        method: str,
        path: str,
        *,
        authentication: bool,
        **kwargs: Any,
    ) -> Any:
        try:
            request = getattr(self._session, method)
            async with request(
                f"{self._base_url}{path}",
                headers=APP_HEADERS,
                timeout=REQUEST_TIMEOUT,
                **kwargs,
            ) as response:
                payload = await response.json(content_type=None)
                status = int(response.status)
        except (OSError, TimeoutError) as err:
            raise HubbleCloudCannotConnect(str(err)) from err
        except (TypeError, ValueError) as err:
            raise HubbleCloudProtocolError("Cloud response is not valid JSON") from err
        except Exception as err:
            raise HubbleCloudCannotConnect(
                f"Cloud request failed: {type(err).__name__}"
            ) from err

        if status in {401, 403}:
            raise HubbleCloudAuthError("Cloud credentials or token were rejected")
        if not 200 <= status < 300:
            if authentication and _looks_like_auth_failure(payload):
                raise HubbleCloudAuthError("Cloud credentials or token were rejected")
            raise HubbleCloudCannotConnect(f"Cloud returned HTTP {status}")
        if authentication and _looks_like_auth_failure(payload):
            raise HubbleCloudAuthError("Cloud credentials or token were rejected")
        return payload


def _response_data(payload: Any) -> Any:
    if not isinstance(payload, Mapping):
        raise HubbleCloudProtocolError("Cloud response is not an object")
    return payload.get("data")


def _parse_publish_job(payload: Any) -> str:
    if not isinstance(payload, Mapping):
        raise HubbleCloudProtocolError("Cloud command response is not an object")
    response = _mapping(payload.get("responsePojo"))
    if str(response.get("status", "")) != "202":
        raise HubbleCloudProtocolError("Cloud command was not accepted")
    job_id = _optional_str(payload.get("id"))
    if job_id is None:
        raise HubbleCloudProtocolError("Cloud command job id is missing")
    _validate_cloud_identifier(job_id)
    return job_id


def _validate_cloud_identifier(value: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", value):
        raise HubbleCloudConfigError("Invalid cloud identifier")


def _looks_like_auth_failure(payload: Any) -> bool:
    if not isinstance(payload, Mapping):
        return False
    status = str(payload.get("status", ""))
    message = str(payload.get("message", "")).lower()
    return status in {"401", "403"} or "auth" in message or "credential" in message


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _parse_settings(value: Any) -> Mapping[str, str]:
    if not isinstance(value, list):
        return {}
    settings: dict[str, str] = {}
    for item in value:
        if not isinstance(item, Mapping):
            continue
        key = _optional_str(item.get("key"))
        setting_value = _optional_str(item.get("value"))
        if key is not None and setting_value is not None:
            settings[key] = setting_value
    return settings
