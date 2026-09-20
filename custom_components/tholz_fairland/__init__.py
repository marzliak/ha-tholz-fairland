"""Cloud-only Home Assistant integration for Tholz Fairland heat pumps."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from .cloud.client import FairlandCloudAuthError, FairlandCloudClient
from .cloud.client_manager import FairlandClientManager
from .utils.const import (
    CONF_COGNITO_CLIENT_SECRET_KEY,
    CONF_COGNITO_REFRESH_TOKEN_KEY,
    CONF_COGNITO_USER_SUB_KEY,
    CONF_CREDENTIAL_SEED_REFRESH_TOKEN_KEY,
    CONF_POLLING_INTERVAL_DEFAULT_VALUE,
    CONF_POLLING_INTERVAL_KEY,
    CONF_THING_ID_KEY,
    DOMAIN,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.WATER_HEATER,
]

ERR_ENTRY_NOT_READY = "Unable to read the Fairland device state"
ERR_AUTH_FAILED = "Cloud authentication rejected every stored refresh token"
SKIP_RELOAD_KEY = "skip_reload"


async def async_setup(_hass, _config):
    """Set up the integration namespace."""
    return True


def _entry_settings(entry: ConfigEntry) -> dict:
    """Merge entry data and options, with validated options taking precedence."""
    return {**entry.data, **entry.options}


def _resolve_credentials(settings: dict) -> tuple[str, str | None]:
    """Return the current refresh token and its recovery seed."""
    live = settings.get(CONF_COGNITO_REFRESH_TOKEN_KEY) or ""
    seed = settings.get(CONF_CREDENTIAL_SEED_REFRESH_TOKEN_KEY) or live or None
    return live, seed


def _build_client(entry: ConfigEntry) -> tuple[FairlandCloudClient, int]:
    """Build the cloud client from a config entry."""
    settings = _entry_settings(entry)
    live_token, seed_token = _resolve_credentials(settings)
    client = FairlandCloudClient(
        thing_id=entry.data[CONF_THING_ID_KEY],
        user_sub=settings[CONF_COGNITO_USER_SUB_KEY],
        client_secret=settings[CONF_COGNITO_CLIENT_SECRET_KEY],
        refresh_token=live_token,
        initial_refresh_token=seed_token,
    )
    polling = settings.get(
        CONF_POLLING_INTERVAL_KEY, CONF_POLLING_INTERVAL_DEFAULT_VALUE
    )
    return client, int(polling)


def _on_auth_failure(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Start reauthentication when the cloud rejects all stored tokens."""
    _LOGGER.warning(
        "%s: cloud authentication failed; starting reauthentication", entry.title
    )

    def _start_reauth() -> None:
        if entry.state == ConfigEntryState.LOADED:
            entry.async_start_reauth(hass)

    hass.loop.call_soon_threadsafe(_start_reauth)


def _adopt_rotated_token(
    hass: HomeAssistant, entry: ConfigEntry, new_token: str
) -> None:
    """Persist a rotated token without restarting the integration."""
    if _entry_settings(entry).get(CONF_COGNITO_REFRESH_TOKEN_KEY) == new_token:
        return

    def _update() -> None:
        if _entry_settings(entry).get(CONF_COGNITO_REFRESH_TOKEN_KEY) == new_token:
            return
        data = dict(entry.data)
        data[CONF_COGNITO_REFRESH_TOKEN_KEY] = new_token
        options = dict(entry.options)
        options.pop(CONF_COGNITO_REFRESH_TOKEN_KEY, None)
        runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id)
        if runtime is not None:
            runtime[SKIP_RELOAD_KEY] = True
        hass.config_entries.async_update_entry(entry, data=data, options=options)
        _LOGGER.info("%s: persisted a rotated cloud token", entry.title)

    hass.loop.call_soon_threadsafe(_update)


async def _close_client(client: FairlandCloudClient, hass: HomeAssistant) -> None:
    await hass.async_add_executor_job(client.close)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one Fairland device after a read-only state validation."""
    client, polling_interval = _build_client(entry)
    manager = FairlandClientManager(client, polling_interval)
    runtime: dict = {"client": client, "manager": manager}
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime

    client.set_refresh_token_callback(
        lambda new_token: _adopt_rotated_token(hass, entry, new_token)
    )
    client.set_auth_failure_callback(lambda _exc: _on_auth_failure(hass, entry))

    def _clear_client_callbacks() -> None:
        client.set_refresh_token_callback(None)
        client.set_auth_failure_callback(None)

    entry.async_on_unload(_clear_client_callbacks)

    def _drop_runtime() -> None:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        if not hass.data.get(DOMAIN):
            hass.data.pop(DOMAIN, None)

    try:
        await manager.get_status()
    except FairlandCloudAuthError as exc:
        await _close_client(client, hass)
        _drop_runtime()
        raise ConfigEntryAuthFailed(ERR_AUTH_FAILED) from exc
    except Exception as exc:
        await _close_client(client, hass)
        _drop_runtime()
        raise ConfigEntryNotReady(ERR_ENTRY_NOT_READY) from exc

    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        await manager.stop()
        _drop_runtime()
        raise

    manager.start(hass)
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload after a user changes validated options."""
    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if runtime is not None and runtime.pop(SKIP_RELOAD_KEY, False):
        return
    await hass.config_entries.async_schedule_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload one Fairland device and close its cloud connection."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    runtime = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if runtime is not None:
        await runtime["manager"].stop()
    if not hass.data.get(DOMAIN):
        hass.data.pop(DOMAIN, None)
    return True
