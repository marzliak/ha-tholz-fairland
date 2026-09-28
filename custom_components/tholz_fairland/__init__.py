"""Cloud-only Home Assistant integration for Tholz Fairland heat pumps."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from .cloud.auth_manager import CredentialUpdate, FairlandAuthManager, RecoveryMode
from .cloud.client import (
    REGION,
    USER_POOL_CLIENT_ID,
    USER_POOL_ID,
    FairlandCloudAuthError,
    FairlandCloudClient,
)
from .cloud.client_manager import FairlandClientManager
from .cloud.cognito_auth import CognitoAuthenticator
from .utils.const import (
    CONF_COGNITO_CLIENT_SECRET_KEY,
    CONF_COGNITO_PASSWORD_KEY,
    CONF_COGNITO_REFRESH_TOKEN_KEY,
    CONF_COGNITO_USER_SUB_KEY,
    CONF_COGNITO_USERNAME_KEY,
    CONF_CREDENTIAL_SEED_REFRESH_TOKEN_KEY,
    CONF_NAME_KEY,
    CONF_POLLING_INTERVAL_DEFAULT_VALUE,
    CONF_POLLING_INTERVAL_KEY,
    CONF_RECOVERY_MODE_KEY,
    CONF_THING_ID_KEY,
    CONFIG_ENTRY_MINOR_VERSION,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
    RECOVERY_MODE_AUTOMATIC,
    RECOVERY_MODE_INTERACTIVE,
    RECOVERY_MODES,
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
ERR_AUTH_FAILED = "Cloud authentication rejected every stored credential"
# SRP budget ledgers, keyed by entry_id, kept for the life of the process so a
# reload or ConfigEntryNotReady retry cannot reset the rate limit.
_SRP_LEDGERS: dict[str, dict[str, Any]] = {}
CLIENT_KEY = "client"
POLL_MANAGER_KEY = "manager"
AUTH_MANAGER_KEY = "auth"
CLOSED_KEY = "closed"

# Credential-bearing keys that must live in entry.data, never in options where
# they would shadow a freshly persisted value. polling_interval is the only key
# the options flow may own; thing_id is identity and is never read from options.
_DATA_OWNED_KEYS = (
    CONF_NAME_KEY,
    CONF_COGNITO_USER_SUB_KEY,
    CONF_COGNITO_CLIENT_SECRET_KEY,
    CONF_COGNITO_REFRESH_TOKEN_KEY,
    CONF_CREDENTIAL_SEED_REFRESH_TOKEN_KEY,
    CONF_COGNITO_USERNAME_KEY,
    CONF_COGNITO_PASSWORD_KEY,
    CONF_RECOVERY_MODE_KEY,
)


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


def _resolve_mode(settings: dict) -> RecoveryMode:
    """Read the recovery mode, defaulting to interactive when absent/unknown."""
    raw = settings.get(CONF_RECOVERY_MODE_KEY, RECOVERY_MODE_INTERACTIVE)
    if raw not in RECOVERY_MODES:
        return RecoveryMode.INTERACTIVE
    return RecoveryMode(raw)


def _normalize(data: dict, options: dict) -> tuple[dict, dict]:
    """
    Return canonical (data, options) for the current schema.

    Pure, total and forward-tolerant: it never raises on malformed content and
    is idempotent (``f(f(x)) == f(x)``). Credential-bearing keys are pulled out
    of options into data so a still-valid value can never be shadowed; the
    recovery seed is filled from the current token when missing; the recovery
    mode defaults to interactive; a password is only kept in automatic mode and
    is never invented. Unknown keys are preserved untouched.
    """
    data = dict(data)
    options = dict(options)
    merged = {**data, **options}

    for key in _DATA_OWNED_KEYS:
        if key in options:
            value = merged.get(key)
            if isinstance(value, str) and value:
                data[key] = value
            options.pop(key, None)
    # thing_id is identity and must never be honoured from options.
    options.pop(CONF_THING_ID_KEY, None)

    current = data.get(CONF_COGNITO_REFRESH_TOKEN_KEY)
    if isinstance(current, str) and current:
        seed = data.get(CONF_CREDENTIAL_SEED_REFRESH_TOKEN_KEY)
        if not (isinstance(seed, str) and seed):
            data[CONF_CREDENTIAL_SEED_REFRESH_TOKEN_KEY] = current

    username = data.get(CONF_COGNITO_USERNAME_KEY)
    if not (isinstance(username, str) and username):
        data.pop(CONF_COGNITO_USERNAME_KEY, None)

    mode = data.get(CONF_RECOVERY_MODE_KEY)
    password = data.get(CONF_COGNITO_PASSWORD_KEY)
    has_password = isinstance(password, str) and bool(password)
    if mode == RECOVERY_MODE_AUTOMATIC and has_password:
        data[CONF_RECOVERY_MODE_KEY] = RECOVERY_MODE_AUTOMATIC
    else:
        # No usable password, or an unknown mode: fall back to interactive and
        # never leave a stray password behind.
        data[CONF_RECOVERY_MODE_KEY] = RECOVERY_MODE_INTERACTIVE
        data.pop(CONF_COGNITO_PASSWORD_KEY, None)

    return data, options


def _build_auth_manager(entry: ConfigEntry) -> FairlandAuthManager:
    """Assemble the Cognito recovery state machine from a config entry."""
    settings = _entry_settings(entry)
    live_token, seed_token = _resolve_credentials(settings)
    authenticator = CognitoAuthenticator(REGION, USER_POOL_ID, USER_POOL_CLIENT_ID)
    return FairlandAuthManager(
        authenticator,
        user_sub=settings[CONF_COGNITO_USER_SUB_KEY],
        client_secret=settings[CONF_COGNITO_CLIENT_SECRET_KEY],
        current_token=live_token,
        recovery_token=seed_token,
        username=settings.get(CONF_COGNITO_USERNAME_KEY),
        password=settings.get(CONF_COGNITO_PASSWORD_KEY),
        mode=_resolve_mode(settings),
        srp_ledger=_SRP_LEDGERS.setdefault(entry.entry_id, {}),
    )


def _build_client(
    entry: ConfigEntry,
) -> tuple[FairlandCloudClient, FairlandAuthManager, int]:
    """Build the cloud client, its auth manager, and the polling interval."""
    settings = _entry_settings(entry)
    manager = _build_auth_manager(entry)
    client = FairlandCloudClient(
        thing_id=entry.data[CONF_THING_ID_KEY], auth_manager=manager
    )
    polling = settings.get(
        CONF_POLLING_INTERVAL_KEY, CONF_POLLING_INTERVAL_DEFAULT_VALUE
    )
    return client, manager, int(polling)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """
    Migrate a config entry to the current schema, idempotently.

    The change is additive, so it is a MINOR version bump: an older build keeps
    the same major version and can still load the entry (that is the rollback
    path). entry_id, unique_id, title, thing_id and every device/entity
    registration are left untouched; the entry is updated in place.
    """
    if entry.version > CONFIG_ENTRY_VERSION:
        # A newer major schema than this build understands: refuse rather than
        # risk corrupting an entry a future build owns.
        _LOGGER.error(
            "%s: cannot downgrade config entry from schema version %s",
            entry.title,
            entry.version,
        )
        return False
    if entry.minor_version >= CONFIG_ENTRY_MINOR_VERSION:
        return True

    data, options = _normalize(entry.data, entry.options)
    hass.config_entries.async_update_entry(
        entry,
        data=data,
        options=options,
        minor_version=CONFIG_ENTRY_MINOR_VERSION,
    )
    _LOGGER.info(
        "%s: migrated config entry to schema %s.%s",
        entry.title,
        CONFIG_ENTRY_VERSION,
        CONFIG_ENTRY_MINOR_VERSION,
    )
    return True


def _normalize_in_place(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Re-run normalization at setup so a rolled-back write cannot shadow data."""
    data, options = _normalize(entry.data, entry.options)
    if data != dict(entry.data) or options != dict(entry.options):
        hass.config_entries.async_update_entry(entry, data=data, options=options)


def _apply_credential_update(
    hass: HomeAssistant,
    entry: ConfigEntry,
    manager: FairlandAuthManager,
    update: CredentialUpdate,
) -> None:
    """Persist a credential change on the event loop with a stale-write guard."""
    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if (
        runtime is None
        or runtime.get(AUTH_MANAGER_KEY) is not manager
        or runtime.get(CLOSED_KEY)
    ):
        # A reload replaced this manager; its late write must not run.
        return

    live = hass.config_entries.async_get_entry(entry.entry_id)
    if live is None:
        return

    stored_current = live.data.get(CONF_COGNITO_REFRESH_TOKEN_KEY)
    if update.expected_current is not None and stored_current not in (
        update.expected_current,
        update.current_token,
    ):
        # The stored token moved on since this cycle began: another writer won.
        _LOGGER.debug("%s: skipped a stale credential write", live.title)
        return

    data = dict(live.data)
    options = dict(live.options)
    changed = False

    if data.get(CONF_COGNITO_REFRESH_TOKEN_KEY) != update.current_token:
        data[CONF_COGNITO_REFRESH_TOKEN_KEY] = update.current_token
        changed = True
    if options.pop(CONF_COGNITO_REFRESH_TOKEN_KEY, None) is not None:
        changed = True

    if update.promote_recovery and update.recovery_token:
        if data.get(CONF_CREDENTIAL_SEED_REFRESH_TOKEN_KEY) != update.recovery_token:
            data[CONF_CREDENTIAL_SEED_REFRESH_TOKEN_KEY] = update.recovery_token
            changed = True
        if options.pop(CONF_CREDENTIAL_SEED_REFRESH_TOKEN_KEY, None) is not None:
            changed = True
    if update.user_sub and data.get(CONF_COGNITO_USER_SUB_KEY) != update.user_sub:
        data[CONF_COGNITO_USER_SUB_KEY] = update.user_sub
        changed = True

    if not changed:
        return

    hass.config_entries.async_update_entry(live, data=data, options=options)
    _LOGGER.info(
        "%s: persisted %s cloud credentials",
        live.title,
        "recovered" if update.promote_recovery else "rotated",
    )


def _make_persist_callback(
    hass: HomeAssistant, entry: ConfigEntry, manager: FairlandAuthManager
):
    """Return a thread-safe persistence callback bound to one entry."""

    def _persist(update: CredentialUpdate) -> None:
        hass.loop.call_soon_threadsafe(
            _apply_credential_update, hass, entry, manager, update
        )

    return _persist


def _make_reauth_callback(
    hass: HomeAssistant, entry: ConfigEntry, manager: FairlandAuthManager
):
    """Return a thread-safe callback that opens a single reauth flow."""

    def _reauth(_reason) -> None:
        def _start_reauth() -> None:
            runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id)
            if (
                runtime is None
                or runtime.get(AUTH_MANAGER_KEY) is not manager
                or runtime.get(CLOSED_KEY)
            ):
                return
            if entry.state == ConfigEntryState.LOADED:
                entry.async_start_reauth(hass)

        _LOGGER.warning(
            "%s: cloud authentication needs attention; starting reauthentication",
            entry.title,
        )
        hass.loop.call_soon_threadsafe(_start_reauth)

    return _reauth


async def _close_client(client: FairlandCloudClient, hass: HomeAssistant) -> None:
    await hass.async_add_executor_job(client.close)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one Fairland device after a read-only state validation."""
    # Repair any option-shadowed credentials before building the client, so a
    # rolled-back build's writes cannot outvote the canonical data.
    _normalize_in_place(hass, entry)

    client, auth_manager, polling_interval = _build_client(entry)
    manager = FairlandClientManager(client, polling_interval)
    runtime: dict[str, Any] = {
        CLIENT_KEY: client,
        POLL_MANAGER_KEY: manager,
        AUTH_MANAGER_KEY: auth_manager,
    }
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime

    auth_manager.set_persist_callback(_make_persist_callback(hass, entry, auth_manager))
    auth_manager.set_reauth_callback(_make_reauth_callback(hass, entry, auth_manager))

    def _detach_callbacks() -> None:
        runtime[CLOSED_KEY] = True
        auth_manager.set_persist_callback(None)
        auth_manager.set_reauth_callback(None)

    entry.async_on_unload(_detach_callbacks)

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

    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        await manager.stop()
        _drop_runtime()
        raise

    manager.start(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload one Fairland device and close its cloud connection."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    runtime = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if runtime is not None:
        # Detach callbacks before stopping so a late worker thread cannot reach
        # a torn-down entry through this manager.
        runtime[CLOSED_KEY] = True
        auth_manager = runtime.get(AUTH_MANAGER_KEY)
        if auth_manager is not None:
            auth_manager.set_persist_callback(None)
            auth_manager.set_reauth_callback(None)
        await runtime[POLL_MANAGER_KEY].stop()
    if not hass.data.get(DOMAIN):
        hass.data.pop(DOMAIN, None)
    return True
