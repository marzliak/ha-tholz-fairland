"""Configuration flow for the Tholz Fairland cloud integration."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.helpers import selector
import voluptuous as vol

from .cloud.client import FairlandCloudAuthError, FairlandCloudClient
from .utils.const import (
    CONF_COGNITO_CLIENT_SECRET_KEY,
    CONF_COGNITO_REFRESH_TOKEN_KEY,
    CONF_COGNITO_USER_SUB_KEY,
    CONF_CREDENTIAL_SEED_REFRESH_TOKEN_KEY,
    CONF_NAME_KEY,
    CONF_POLLING_INTERVAL_DEFAULT_VALUE,
    CONF_POLLING_INTERVAL_KEY,
    CONF_THING_ID_KEY,
    DOMAIN,
)

ERR_CANNOT_CONNECT = "cannot_connect"
ERR_INVALID_TOKEN = "invalid_token"  # noqa: S105
_PASSWORD_SELECTOR = selector.TextSelector(
    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
)


def _cloud_schema(defaults: dict | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_NAME_KEY, default=defaults.get(CONF_NAME_KEY, "Fairland")
            ): str,
            vol.Required(
                CONF_COGNITO_USER_SUB_KEY,
                default=defaults.get(CONF_COGNITO_USER_SUB_KEY, ""),
            ): str,
            vol.Required(
                CONF_COGNITO_CLIENT_SECRET_KEY,
                default=defaults.get(CONF_COGNITO_CLIENT_SECRET_KEY, ""),
            ): _PASSWORD_SELECTOR,
            vol.Required(
                CONF_COGNITO_REFRESH_TOKEN_KEY,
                default=defaults.get(CONF_COGNITO_REFRESH_TOKEN_KEY, ""),
            ): _PASSWORD_SELECTOR,
            vol.Required(
                CONF_THING_ID_KEY, default=defaults.get(CONF_THING_ID_KEY, "")
            ): str,
            vol.Optional(
                CONF_POLLING_INTERVAL_KEY,
                default=defaults.get(
                    CONF_POLLING_INTERVAL_KEY, CONF_POLLING_INTERVAL_DEFAULT_VALUE
                ),
            ): vol.All(int, vol.Range(min=10, max=300)),
        }
    )


def _cloud_options_schema(defaults: dict) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_COGNITO_USER_SUB_KEY,
                default=defaults.get(CONF_COGNITO_USER_SUB_KEY, ""),
            ): str,
            vol.Required(
                CONF_COGNITO_CLIENT_SECRET_KEY,
                default=defaults.get(CONF_COGNITO_CLIENT_SECRET_KEY, ""),
            ): _PASSWORD_SELECTOR,
            vol.Required(
                CONF_COGNITO_REFRESH_TOKEN_KEY,
                default=defaults.get(CONF_COGNITO_REFRESH_TOKEN_KEY, ""),
            ): _PASSWORD_SELECTOR,
            vol.Optional(
                CONF_POLLING_INTERVAL_KEY,
                default=defaults.get(
                    CONF_POLLING_INTERVAL_KEY, CONF_POLLING_INTERVAL_DEFAULT_VALUE
                ),
            ): vol.All(int, vol.Range(min=10, max=300)),
        }
    )


async def _validate_cloud(hass, values: dict) -> None:
    """Authenticate and read state without sending a device command."""
    client = FairlandCloudClient(
        thing_id=values[CONF_THING_ID_KEY],
        user_sub=values[CONF_COGNITO_USER_SUB_KEY],
        client_secret=values[CONF_COGNITO_CLIENT_SECRET_KEY],
        refresh_token=values[CONF_COGNITO_REFRESH_TOKEN_KEY],
    )
    try:
        await hass.async_add_executor_job(client.get_status)
    except FairlandCloudAuthError as exc:
        raise ValueError(ERR_INVALID_TOKEN) from exc
    except Exception as exc:
        raise ValueError(ERR_CANNOT_CONNECT) from exc
    finally:
        await hass.async_add_executor_job(client.close)


def _reauth_schema(defaults: dict | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Optional(
                CONF_COGNITO_REFRESH_TOKEN_KEY,
                default=defaults.get(CONF_COGNITO_REFRESH_TOKEN_KEY, ""),
            ): _PASSWORD_SELECTOR
        }
    )


class TholzFairlandConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure a cloud-connected Fairland heat pump."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                await _validate_cloud(self.hass, user_input)
            except ValueError as exc:
                errors["base"] = str(exc)
            except Exception:
                errors["base"] = ERR_CANNOT_CONNECT
            else:
                await self.async_set_unique_id(user_input[CONF_THING_ID_KEY])
                self._abort_if_unique_id_configured()
                data = {
                    **user_input,
                    CONF_CREDENTIAL_SEED_REFRESH_TOKEN_KEY: user_input[
                        CONF_COGNITO_REFRESH_TOKEN_KEY
                    ],
                }
                return self.async_create_entry(title=data[CONF_NAME_KEY], data=data)
        return self.async_show_form(
            step_id="user", data_schema=_cloud_schema(user_input), errors=errors
        )

    @staticmethod
    def async_get_options_flow(_config_entry):
        return TholzFairlandOptionsFlowHandler()

    async def async_step_reauth(self, _entry_data: dict[str, str]):
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, str] | None = None):
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            current = {**entry.data, **entry.options, **user_input}
            reason = None
            try:
                await _validate_cloud(self.hass, current)
            except ValueError as exc:
                reason = str(exc)
            except Exception:
                reason = ERR_CANNOT_CONNECT
            if reason is None:
                token = current[CONF_COGNITO_REFRESH_TOKEN_KEY]
                data = dict(entry.data)
                data[CONF_COGNITO_REFRESH_TOKEN_KEY] = token
                data[CONF_CREDENTIAL_SEED_REFRESH_TOKEN_KEY] = token
                options = dict(entry.options)
                options.pop(CONF_COGNITO_REFRESH_TOKEN_KEY, None)
                return self.async_update_reload_and_abort(
                    entry, data=data, options=options
                )
            errors["base"] = reason
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_reauth_schema(
                {**entry.data, **entry.options, **(user_input or {})}
            ),
            errors=errors,
        )


class TholzFairlandOptionsFlowHandler(config_entries.OptionsFlow):
    """Edit cloud credentials and polling frequency."""

    async def async_step_init(self, user_input=None):
        current = {**self.config_entry.data, **self.config_entry.options}
        errors = {}
        if user_input is not None:
            try:
                await _validate_cloud(self.hass, {**current, **user_input})
            except ValueError as exc:
                errors["base"] = str(exc)
            except Exception:
                errors["base"] = ERR_CANNOT_CONNECT
            else:
                return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=_cloud_options_schema(current),
            errors=errors,
        )
