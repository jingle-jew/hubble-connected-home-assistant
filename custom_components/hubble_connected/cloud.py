"""Hubble cloud authentication and camera inventory boundary.

This module deliberately contains no Home Assistant imports.  Keeping the
vendor API at a narrow boundary makes it possible to test response parsing
without a live account and to replace the transport independently later.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

API_BASE_URL = "https://api.hubble.in"
AUTH_PATH = "/v4/users/authentication_token.json"
CAMERAS_PATH = "/v6/devices/own.json"
REQUEST_TIMEOUT = 10

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
    orbweb_sid: str | None = field(repr=False)
    orbweb_password: str | None = field(repr=False)

    @property
    def has_orbweb_credentials(self) -> bool:
        """Return whether the inventory can seed an Orbweb client."""
        return bool(self.orbweb_sid and self.orbweb_password)


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
        registration_id = _optional_str(item.get("registration_id"))
        if not registration_id:
            raise HubbleCloudProtocolError("Camera registration_id is missing")

        orbweb = _mapping(item.get("orbweb"))
        device_status = _mapping(item.get("device_status"))
        environment = _mapping(device_status.get("environment"))
        detail = _mapping(device_status.get("device"))
        settings = _parse_settings(item.get("device_settings"))

        cameras.append(
            HubbleCloudCamera(
                cloud_id=_optional_int(item.get("id")),
                name=_optional_str(item.get("name")) or registration_id,
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
                orbweb_sid=_optional_str(orbweb.get("sid")),
                orbweb_password=_optional_str(orbweb.get("password")),
            )
        )
    return tuple(cameras)


class HubbleCloudClient:
    """Small async client using a Home Assistant-managed HTTP session."""

    def __init__(self, session: Any, base_url: str = API_BASE_URL) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")

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
