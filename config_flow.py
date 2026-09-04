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
    CONF_ORBWEB_ACCOUNT,
    CONF_ORBWEB_HOST,
    CONF_ORBWEB_PASSWORD,
    CONF_ORBWEB_SID,
    CONF_PROVIDER,
    CONF_RTSP_PASSWORD,
    CONF_RTSP_USERNAME,
    DEFAULT_CLOUD_CAMERA_IDS,
    DEFAULT_LOCAL_CAMERAS,
    DEFAULT_NAME,
    DOMAIN,
    PROVIDER_HUBBLE,
    PROVIDER_MANUAL_VM85,
    VM85_ORBWEB_ACCOUNT,
)
from .local import HubbleLocalConfigError, parse_local_camera_specs
from .manual_orbweb import ManualOrbwebConfigError, build_manual_orbweb_camera


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
                default=defaults.get(CONF_CLOUD_CAMERA_IDS, DEFAULT_CLOUD_CAMERA_IDS),
            ): str,
        }
    )


class HubbleConnectedConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a Hubble Connected config flow."""

    VERSION = 2

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the account or manually provisioned camera provider."""
        return self.async_show_menu(
            step_id="user",
            menu_options=[PROVIDER_HUBBLE, PROVIDER_MANUAL_VM85],
        )

    async def async_step_hubble(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create a local-first entry with optional Hubble cloud discovery."""
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
                # Preserve the unique ID used by existing installations. The
                # integration still has one Hubble account entry, alongside
                # any number of separately keyed manual VM85 entries.
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()
                title = user_input.pop(CONF_NAME)
                user_input[CONF_PROVIDER] = PROVIDER_HUBBLE
                return self.async_create_entry(title=title, data=user_input)

        return self.async_show_form(
            step_id=PROVIDER_HUBBLE,
            data_schema=_user_schema(user_input),
            errors=errors,
        )

    async def async_step_manual_vm85(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add an owner-authorized VM85 using credentials obtained by its app."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                camera = build_manual_orbweb_camera(
                    name=user_input[CONF_NAME],
                    host=user_input[CONF_ORBWEB_HOST],
                    target_id=user_input[CONF_ORBWEB_SID],
                    auth_account=user_input[CONF_ORBWEB_ACCOUNT],
                    auth_password=user_input[CONF_ORBWEB_PASSWORD],
                    rtsp_username=user_input[CONF_RTSP_USERNAME],
                    rtsp_password=user_input[CONF_RTSP_PASSWORD],
                )
            except ManualOrbwebConfigError:
                errors["base"] = "invalid_manual_orbweb"
            else:
                await self.async_set_unique_id(camera.key)
                self._abort_if_unique_id_configured()
                title = user_input.pop(CONF_NAME)
                user_input[CONF_PROVIDER] = PROVIDER_MANUAL_VM85
                return self.async_create_entry(title=title, data=user_input)

        defaults = user_input or {}
        return self.async_show_form(
            step_id=PROVIDER_MANUAL_VM85,
            data_schema=_manual_vm85_schema(defaults),
            errors=errors,
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
        provider = self.config_entry.data.get(CONF_PROVIDER, PROVIDER_HUBBLE)
        if provider == PROVIDER_MANUAL_VM85:
            return await self.async_step_manual_vm85(user_input)

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
                        default=saved(CONF_CLOUD_CAMERA_IDS, DEFAULT_CLOUD_CAMERA_IDS),
                    ): str,
                }
            ),
            errors=errors,
        )

    async def async_step_manual_vm85(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Update a manually provisioned VM85 camera."""
        errors: dict[str, str] = {}

        def saved(key: str, fallback: str = "") -> str:
            return str(
                self.config_entry.options.get(
                    key, self.config_entry.data.get(key, fallback)
                )
            )

        if user_input is not None:
            for secret_key in (
                CONF_ORBWEB_PASSWORD,
                CONF_RTSP_USERNAME,
                CONF_RTSP_PASSWORD,
            ):
                if not user_input.get(secret_key, ""):
                    user_input[secret_key] = saved(secret_key)
            try:
                build_manual_orbweb_camera(
                    name=self.config_entry.title,
                    host=user_input[CONF_ORBWEB_HOST],
                    target_id=user_input[CONF_ORBWEB_SID],
                    auth_account=user_input[CONF_ORBWEB_ACCOUNT],
                    auth_password=user_input[CONF_ORBWEB_PASSWORD],
                    rtsp_username=user_input[CONF_RTSP_USERNAME],
                    rtsp_password=user_input[CONF_RTSP_PASSWORD],
                )
            except ManualOrbwebConfigError:
                errors["base"] = "invalid_manual_orbweb"
            else:
                user_input[CONF_PROVIDER] = PROVIDER_MANUAL_VM85
                return self.async_create_entry(title="", data=user_input)

        defaults = {
            CONF_ORBWEB_HOST: saved(CONF_ORBWEB_HOST),
            CONF_ORBWEB_SID: saved(CONF_ORBWEB_SID),
            CONF_ORBWEB_ACCOUNT: saved(CONF_ORBWEB_ACCOUNT, VM85_ORBWEB_ACCOUNT),
            **(user_input or {}),
        }
        return self.async_show_form(
            step_id=PROVIDER_MANUAL_VM85,
            data_schema=_manual_vm85_schema(defaults, options=True),
            errors=errors,
        )


def _manual_vm85_schema(
    defaults: dict[str, Any], *, options: bool = False
) -> vol.Schema:
    """Return the manual VM85 form without ever redisplaying saved secrets."""
    fields: dict[vol.Marker, Any] = {}
    if not options:
        fields[vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, "VM85"))] = str
    fields.update(
        {
            vol.Required(
                CONF_ORBWEB_HOST, default=defaults.get(CONF_ORBWEB_HOST, "")
            ): str,
            vol.Required(
                CONF_ORBWEB_SID, default=defaults.get(CONF_ORBWEB_SID, "")
            ): str,
            vol.Required(
                CONF_ORBWEB_ACCOUNT,
                default=defaults.get(CONF_ORBWEB_ACCOUNT, VM85_ORBWEB_ACCOUNT),
            ): str,
            vol.Required(CONF_ORBWEB_PASSWORD, default=""): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
            vol.Required(CONF_RTSP_USERNAME, default=""): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
            vol.Required(CONF_RTSP_PASSWORD, default=""): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
        }
    )
    return vol.Schema(fields)
