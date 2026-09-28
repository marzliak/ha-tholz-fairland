"""Config-entry setup, teardown and recovered-credential bookkeeping."""

import asyncio
from types import SimpleNamespace

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from custom_components.tholz_fairland import (
    AUTH_MANAGER_KEY,
    CLOSED_KEY,
    DOMAIN,
    _SRP_LEDGERS,
    _apply_credential_update,
    _build_auth_manager,
    _build_client,
    _entry_settings,
    _normalize,
    _normalize_in_place,
    _resolve_credentials,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.tholz_fairland.cloud.auth_manager import CredentialUpdate
from custom_components.tholz_fairland.cloud.client import FairlandCloudAuthError

CLOUD_DATA = {
    "thing_id": "thing-uuid",
    "cognito_user_sub": "user-sub",
    "cognito_client_secret": "",
    "cognito_refresh_token": "live-token",
    "credential_seed_refresh_token": "seed-token",
    "cognito_username": "person@example.test",
    "cognito_password": "synthetic-password",
    "cognito_recovery_mode": "automatic",
}


class FakeLoop:
    """Run thread-safe callbacks inline so tests stay deterministic."""

    def call_soon_threadsafe(self, callback, *args):
        callback(*args)


class FakeConfigEntries:
    def __init__(self, forward_error=None):
        self.forward_error = forward_error
        self.unload_result = True
        self.updates = []
        self.reloaded = []
        self.entries = {}

    def register(self, entry):
        self.entries[entry.entry_id] = entry

    def async_get_entry(self, entry_id):
        return self.entries.get(entry_id)

    async def async_forward_entry_setups(self, _entry, _platforms):
        if self.forward_error is not None:
            raise self.forward_error

    async def async_unload_platforms(self, _entry, _platforms):
        return self.unload_result

    def async_schedule_reload(self, entry_id):
        self.reloaded.append(entry_id)

    def async_update_entry(self, entry, **kwargs):
        self.updates.append(kwargs)
        if "data" in kwargs:
            entry.data = kwargs["data"]
        if "options" in kwargs:
            entry.options = kwargs["options"]


class FakeHass:
    def __init__(self, forward_error=None):
        self.data = {}
        self.loop = FakeLoop()
        self.config_entries = FakeConfigEntries(forward_error)
        self.background = []

    async def async_add_executor_job(self, target, *args):
        return target(*args)

    def async_create_background_task(self, coroutine, name):
        self.background.append(name)
        return asyncio.create_task(coroutine)


class FakeEntry:
    def __init__(self, data=None, options=None):
        self.entry_id = "entry-1"
        self.title = "Fairland"
        self.data = dict(data or {})
        self.options = dict(options or {})
        self.unload_callbacks = []

    def async_on_unload(self, callback):
        self.unload_callbacks.append(callback)


class FakeClient:
    def __init__(self, status_error=None):
        self.status_error = status_error
        self.closed = 0
        self.state_callback = None

    def get_status(self):
        if self.status_error is not None:
            raise self.status_error
        return {"id": 1496}

    def close(self):
        self.closed += 1

    def set_state_change_callback(self, callback):
        self.state_callback = callback


class FakeAuthManager:
    """Captures the persistence/reauth callbacks the setup wires."""

    def __init__(self):
        self.persist_callback = None
        self.reauth_callback = None

    def set_persist_callback(self, callback):
        self.persist_callback = callback

    def set_reauth_callback(self, callback):
        self.reauth_callback = callback


def _install_client(monkeypatch, client, auth_manager=None, polling=30):
    auth_manager = auth_manager or FakeAuthManager()
    monkeypatch.setattr(
        "custom_components.tholz_fairland._build_client",
        lambda _entry: (client, auth_manager, polling),
    )
    return auth_manager


def _register(hass, entry, manager):
    hass.data[DOMAIN] = {entry.entry_id: {AUTH_MANAGER_KEY: manager}}
    hass.config_entries.register(entry)


# --- settings precedence -------------------------------------------------


def test_options_take_precedence_over_stored_data():
    entry = SimpleNamespace(
        data={**CLOUD_DATA, "polling_interval": 30},
        options={"polling_interval": 120},
    )
    assert _entry_settings(entry)["polling_interval"] == 120


def test_edited_polling_interval_reaches_the_manager():
    entry = SimpleNamespace(
        entry_id="e", data=CLOUD_DATA, options={"polling_interval": 120}
    )
    _, _auth, polling = _build_client(entry)
    assert polling == 120


def test_cloud_defaults_apply_when_no_interval_is_stored():
    entry = SimpleNamespace(entry_id="e", data=CLOUD_DATA, options={})
    _, _auth, polling = _build_client(entry)
    assert polling == 30


def test_transport_identity_ignores_options():
    """thing_id is never exposed by the options schema and must not be forgeable."""
    entry = SimpleNamespace(
        entry_id="e", data=CLOUD_DATA, options={"thing_id": "attacker-thing"}
    )
    client, _auth, _ = _build_client(entry)
    assert client.thing_id == "thing-uuid"


def test_seed_falls_back_to_the_live_token():
    live, seed = _resolve_credentials({"cognito_refresh_token": "only-token"})
    assert (live, seed) == ("only-token", "only-token")


def test_seed_uses_the_stored_recovery_seed():
    live, seed = _resolve_credentials(
        {
            "cognito_refresh_token": "rotated",
            "credential_seed_refresh_token": "original",
        }
    )
    assert (live, seed) == ("rotated", "original")


def test_missing_credentials_resolve_to_empty():
    assert _resolve_credentials({}) == ("", None)


# --- setup and teardown --------------------------------------------------


@pytest.mark.asyncio
async def test_successful_setup_registers_runtime_and_callbacks(monkeypatch):
    hass, entry, client = FakeHass(), FakeEntry(CLOUD_DATA), FakeClient()
    hass.config_entries.register(entry)
    auth = _install_client(monkeypatch, client)

    assert await async_setup_entry(hass, entry)

    assert hass.data[DOMAIN][entry.entry_id]["client"] is client
    assert hass.data[DOMAIN][entry.entry_id][AUTH_MANAGER_KEY] is auth
    assert auth.persist_callback is not None
    assert auth.reauth_callback is not None
    assert hass.background == ["Tholz Fairland polling updater"]

    await async_unload_entry(hass, entry)


@pytest.mark.asyncio
async def test_unload_detaches_callbacks_before_stopping(monkeypatch):
    hass, entry, client = FakeHass(), FakeEntry(CLOUD_DATA), FakeClient()
    hass.config_entries.register(entry)
    auth = _install_client(monkeypatch, client)
    await async_setup_entry(hass, entry)

    assert await async_unload_entry(hass, entry)

    assert DOMAIN not in hass.data
    assert client.closed == 1
    assert client.state_callback is None
    # Detached inside async_unload_entry so a late worker thread cannot reach a
    # torn-down entry through this manager.
    assert auth.persist_callback is None
    assert auth.reauth_callback is None


@pytest.mark.asyncio
async def test_unload_is_refused_when_platforms_refuse(monkeypatch):
    hass, entry, client = FakeHass(), FakeEntry(CLOUD_DATA), FakeClient()
    hass.config_entries.register(entry)
    _install_client(monkeypatch, client)
    await async_setup_entry(hass, entry)
    hass.config_entries.unload_result = False

    assert not await async_unload_entry(hass, entry)
    assert entry.entry_id in hass.data[DOMAIN]

    hass.config_entries.unload_result = True
    await async_unload_entry(hass, entry)


@pytest.mark.asyncio
async def test_auth_error_during_setup_asks_for_reauth(monkeypatch):
    client = FakeClient(status_error=FairlandCloudAuthError("rejected"))
    hass, entry = FakeHass(), FakeEntry(CLOUD_DATA)
    hass.config_entries.register(entry)
    _install_client(monkeypatch, client)

    with pytest.raises(ConfigEntryAuthFailed):
        await async_setup_entry(hass, entry)

    assert DOMAIN not in hass.data
    assert client.closed == 1


@pytest.mark.asyncio
async def test_unreachable_device_during_setup_retries_later(monkeypatch):
    client = FakeClient(status_error=OSError("no route to host"))
    hass, entry = FakeHass(), FakeEntry(CLOUD_DATA)
    hass.config_entries.register(entry)
    _install_client(monkeypatch, client)

    with pytest.raises(ConfigEntryNotReady):
        await async_setup_entry(hass, entry)

    assert DOMAIN not in hass.data
    assert client.closed == 1


@pytest.mark.asyncio
async def test_platform_failure_releases_the_client(monkeypatch):
    hass = FakeHass(forward_error=RuntimeError("platform boom"))
    entry, client = FakeEntry(CLOUD_DATA), FakeClient()
    hass.config_entries.register(entry)
    _install_client(monkeypatch, client)

    with pytest.raises(RuntimeError):
        await async_setup_entry(hass, entry)

    assert DOMAIN not in hass.data
    assert client.closed == 1
    assert hass.background == []


# --- migration/normalization ---------------------------------------------


def test_normalize_deshadows_credentials_out_of_options():
    data = {"cognito_user_sub": "canonical", "cognito_refresh_token": "tok"}
    options = {"cognito_user_sub": "stale", "polling_interval": 45}
    new_data, new_options = _normalize(data, options)
    assert new_data["cognito_user_sub"] == "stale"  # merged value wins, moved to data
    assert "cognito_user_sub" not in new_options
    assert new_options == {"polling_interval": 45}


def test_normalize_fills_seed_and_defaults_mode():
    new_data, _ = _normalize({"cognito_refresh_token": "tok"}, {})
    assert new_data["credential_seed_refresh_token"] == "tok"
    assert new_data["cognito_recovery_mode"] == "automatic"


def test_normalize_drops_password_without_automatic_mode():
    new_data, _ = _normalize(
        {"cognito_refresh_token": "t", "cognito_password": "pw"}, {}
    )
    assert "cognito_password" not in new_data
    assert new_data["cognito_recovery_mode"] == "automatic"


def test_normalize_is_idempotent():
    once = _normalize(dict(CLOUD_DATA), {"cognito_refresh_token": "shadow"})
    twice = _normalize(*once)
    assert twice == once


def test_normalize_never_carries_thing_id_from_options():
    _data, options = _normalize(CLOUD_DATA, {"thing_id": "attacker"})
    assert "thing_id" not in options


def test_setup_normalization_repairs_a_shadowing_options_write():
    """A rolled-back build may put credentials back into options; setup repairs it."""
    hass = FakeHass()
    entry = FakeEntry(CLOUD_DATA, options={"cognito_user_sub": "stale-shadow"})
    hass.config_entries.register(entry)

    _normalize_in_place(hass, entry)

    assert "cognito_user_sub" not in entry.options
    assert entry.data["cognito_user_sub"] == "stale-shadow"
    assert hass.config_entries.updates  # a write happened


def test_setup_normalization_is_a_noop_when_already_canonical():
    hass = FakeHass()
    entry = FakeEntry(CLOUD_DATA, options={"polling_interval": 45})
    hass.config_entries.register(entry)

    _normalize_in_place(hass, entry)

    assert hass.config_entries.updates == []


def test_srp_budget_ledger_is_shared_across_manager_instances():
    """Rebuilding the manager for one entry must not reset its SRP budget."""
    _SRP_LEDGERS.pop("entry-ledger-test", None)
    entry = SimpleNamespace(
        entry_id="entry-ledger-test", data=dict(CLOUD_DATA), options={}
    )
    first = _build_auth_manager(entry)
    second = _build_auth_manager(entry)
    assert first._srp_ledger is second._srp_ledger
    _SRP_LEDGERS.pop("entry-ledger-test", None)


# --- recovered credential bookkeeping ------------------------------------


def test_rotated_token_is_persisted():
    hass, entry = FakeHass(), FakeEntry(CLOUD_DATA)
    manager = object()
    _register(hass, entry, manager)

    _apply_credential_update(
        hass,
        entry,
        manager,
        CredentialUpdate(current_token="rotated", expected_current="live-token"),
    )

    assert entry.data["cognito_refresh_token"] == "rotated"


def test_ordinary_rotation_never_touches_the_recovery_seed():
    hass, entry = FakeHass(), FakeEntry(CLOUD_DATA)
    manager = object()
    _register(hass, entry, manager)

    _apply_credential_update(
        hass, entry, manager, CredentialUpdate(current_token="rotated")
    )

    assert entry.data["credential_seed_refresh_token"] == "seed-token"


def test_fresh_srp_promotes_current_seed_and_sub():
    hass, entry = FakeHass(), FakeEntry(CLOUD_DATA)
    manager = object()
    _register(hass, entry, manager)

    _apply_credential_update(
        hass,
        entry,
        manager,
        CredentialUpdate(
            current_token="fresh",
            recovery_token="fresh",
            user_sub="new-sub",
            promote_recovery=True,
            expected_current="live-token",
        ),
    )

    assert entry.data["cognito_refresh_token"] == "fresh"
    # Single seed key, kept in step so a rollback build still authenticates.
    assert entry.data["credential_seed_refresh_token"] == "fresh"
    assert entry.data["cognito_user_sub"] == "new-sub"


def test_adopting_an_unchanged_token_writes_nothing():
    hass, entry = FakeHass(), FakeEntry(CLOUD_DATA)
    manager = object()
    _register(hass, entry, manager)

    _apply_credential_update(
        hass, entry, manager, CredentialUpdate(current_token="live-token")
    )

    assert hass.config_entries.updates == []


def test_stale_write_is_dropped_when_the_stored_token_moved():
    """CAS: another writer already advanced the token past this cycle's baseline."""
    hass, entry = FakeHass(), FakeEntry(CLOUD_DATA)
    manager = object()
    _register(hass, entry, manager)

    _apply_credential_update(
        hass,
        entry,
        manager,
        CredentialUpdate(current_token="stale", expected_current="a-different-token"),
    )

    assert entry.data["cognito_refresh_token"] == "live-token"
    assert hass.config_entries.updates == []


def test_write_from_a_superseded_manager_is_dropped():
    hass, entry = FakeHass(), FakeEntry(CLOUD_DATA)
    active = object()
    _register(hass, entry, active)
    superseded = object()

    _apply_credential_update(
        hass, entry, superseded, CredentialUpdate(current_token="stale")
    )

    assert entry.data["cognito_refresh_token"] == "live-token"
    assert hass.config_entries.updates == []


def test_write_to_a_removed_entry_is_dropped():
    hass, entry = FakeHass(), FakeEntry(CLOUD_DATA)
    manager = object()
    # Runtime present but the entry has been removed from the registry.
    hass.data[DOMAIN] = {entry.entry_id: {AUTH_MANAGER_KEY: manager}}

    _apply_credential_update(
        hass, entry, manager, CredentialUpdate(current_token="rotated")
    )

    assert hass.config_entries.updates == []


def test_write_after_close_is_dropped():
    hass, entry = FakeHass(), FakeEntry(CLOUD_DATA)
    manager = object()
    hass.data[DOMAIN] = {entry.entry_id: {AUTH_MANAGER_KEY: manager, CLOSED_KEY: True}}
    hass.config_entries.register(entry)

    _apply_credential_update(
        hass, entry, manager, CredentialUpdate(current_token="rotated")
    )

    assert hass.config_entries.updates == []
