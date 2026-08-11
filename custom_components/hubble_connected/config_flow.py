"""Config flow for Hubble Connected."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .cloud import (
    HubbleCloudAuthError,
    HubbleCloudCannotConnect,
    HubbleCloudClient,
    HubbleCloudConfigError,
    HubbleCloudProtocolError,
    parse_cloud_camera_ids,
)
from .const import (
    CONF_CLOUD_CAMERA_IDS,
    CONF_CLOUD_LOGIN,
    CONF_CLOUD_PASSWORD,
    CONF_LOCAL_CAMERAS,
    DEFAULT_CLOUD_CAMERA_IDS,
    DEFAULT_LOCAL_CAMERAS,
    DEFAULT_NAME,
    DOMAIN,
)
from .local import HubbleLocalConfigError, parse_local_camera_specs


def _user_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, DEFAULT_NAME)): str,
            vol.Optional(
                CONF_LOCAL_CAMERAS,
                default=defaults.get(CONF_LOCAL_CAMERAS, DEFAULT_LOCAL_CAMERAS),
            ): str,
            vol.Optional(
                CONF_CLOUD_LOGIN,
                default=defaults.get(CONF_CLOUD_LOGIN, ""),
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.EMAIL)
            ),
            vol.Optional(
                CONF_CLOUD_PASSWORD,
                default="",
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
            vol.Optional(
                CONF_CLOUD_CAMERA_IDS,
                default=defaults.get(
                    CONF_CLOUD_CAMERA_IDS, DEFAULT_CLOUD_CAMERA_IDS
                ),
            ): str,
        }
    )


class HubbleConnectedConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a Hubble Connected config flow."""

    VERSION = 2

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create a local-first entry with optional cloud stream discovery."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                parse_local_camera_specs(user_input[CONF_LOCAL_CAMERAS])
            except HubbleLocalConfigError:
                errors["base"] = "invalid_camera_list"
            try:
                cloud_camera_ids = parse_cloud_camera_ids(
                    user_input.get(CONF_CLOUD_CAMERA_IDS, "")
                )
            except HubbleCloudConfigError:
                cloud_camera_ids = ()
                errors["base"] = "invalid_cloud_camera_ids"
            cloud_login = user_input.get(CONF_CLOUD_LOGIN, "").strip()
            cloud_password = user_input.get(CONF_CLOUD_PASSWORD, "")
            if cloud_camera_ids and not cloud_login:
                errors["base"] = "cloud_credentials_required"
            if cloud_login and not cloud_password:
                errors["base"] = "cloud_invalid_auth"
            elif cloud_login and not errors:
                client = HubbleCloudClient(async_get_clientsession(self.hass))
                try:
                    session = await client.async_authenticate(
                        cloud_login, cloud_password
                    )
                    await client.async_get_cameras(session)
                    for registration_id in cloud_camera_ids:
                        await client.async_get_camera(session, registration_id)
                except HubbleCloudAuthError:
                    errors["base"] = "cloud_invalid_auth"
                except HubbleCloudCannotConnect:
                    errors["base"] = "cloud_cannot_connect"
                except HubbleCloudProtocolError:
                    errors["base"] = "cloud_invalid_response"
                else:
                    user_input[CONF_CLOUD_LOGIN] = cloud_login
            else:
                user_input.pop(CONF_CLOUD_PASSWORD, None)
            if not errors:
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()
                title = user_input.pop(CONF_NAME)
                return self.async_create_entry(title=title, data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=_user_schema(user_input), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OptionsFlow:
        """Return the local camera options flow."""
        return HubbleConnectedOptionsFlow()


class HubbleConnectedOptionsFlow(OptionsFlow):
    """Configure account access and optional local camera addresses."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                parse_local_camera_specs(user_input[CONF_LOCAL_CAMERAS])
            except HubbleLocalConfigError:
                errors["base"] = "invalid_camera_list"
            try:
                cloud_camera_ids = parse_cloud_camera_ids(
                    user_input.get(CONF_CLOUD_CAMERA_IDS, "")
                )
            except HubbleCloudConfigError:
                cloud_camera_ids = ()
                errors["base"] = "invalid_cloud_camera_ids"
            if not errors:
                cloud_login = user_input.get(CONF_CLOUD_LOGIN, "").strip()
                cloud_password = user_input.get(CONF_CLOUD_PASSWORD, "")
                if cloud_login and not cloud_password:
                    cloud_password = self.config_entry.options.get(
                        CONF_CLOUD_PASSWORD,
                        self.config_entry.data.get(CONF_CLOUD_PASSWORD, ""),
                    )
                if cloud_login and not cloud_password:
                    errors["base"] = "cloud_invalid_auth"
                elif cloud_camera_ids and not cloud_login:
                    errors["base"] = "cloud_credentials_required"
                elif cloud_login:
                    client = HubbleCloudClient(async_get_clientsession(self.hass))
                    try:
                        session = await client.async_authenticate(
                            cloud_login, cloud_password
                        )
                        await client.async_get_cameras(session)
                        for registration_id in cloud_camera_ids:
                            await client.async_get_camera(session, registration_id)
                    except HubbleCloudAuthError:
                        errors["base"] = "cloud_invalid_auth"
                    except HubbleCloudCannotConnect:
                        errors["base"] = "cloud_cannot_connect"
                    except HubbleCloudProtocolError:
                        errors["base"] = "cloud_invalid_response"
                    else:
                        user_input[CONF_CLOUD_LOGIN] = cloud_login
                        user_input[CONF_CLOUD_PASSWORD] = cloud_password
                else:
                    user_input.pop(CONF_CLOUD_PASSWORD, None)
            if not errors:
                return self.async_create_entry(title="", data=user_input)

        defaults = user_input or self.config_entry.options

        def saved(key: str, fallback: Any = "") -> Any:
            return defaults.get(key, self.config_entry.data.get(key, fallback))

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_LOCAL_CAMERAS,
                        default=defaults.get(
                            CONF_LOCAL_CAMERAS,
                            self.config_entry.data.get(
                                CONF_LOCAL_CAMERAS, DEFAULT_LOCAL_CAMERAS
                            ),
                        ),
                    ): str,
                    vol.Optional(
                        CONF_CLOUD_LOGIN,
                        default=saved(CONF_CLOUD_LOGIN),
                    ): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.EMAIL
                        )
                    ),
                    vol.Optional(
                        CONF_CLOUD_PASSWORD,
                        default="",
                    ): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD
                        )
                    ),
                    vol.Optional(
                        CONF_CLOUD_CAMERA_IDS,
                        default=saved(
                            CONF_CLOUD_CAMERA_IDS, DEFAULT_CLOUD_CAMERA_IDS
                        ),
                    ): str,
                }
            ),
            errors=errors,
        )
