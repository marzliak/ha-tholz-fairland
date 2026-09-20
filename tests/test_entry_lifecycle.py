"""Config-entry setup, teardown and rotated-credential bookkeeping."""

import asyncio
from types import SimpleNamespace

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from custom_components.tholz_fairland import (
    DOMAIN,
    SKIP_RELOAD_KEY,
    _adopt_rotated_token,
    _async_reload_entry,
    _build_client,
    _entry_settings,
    _resolve_credentials,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.tholz_fairland.cloud.client import FairlandCloudAuthError

CLOUD_DATA = {
    "thing_id": "thing-uuid",
    "cognito_user_sub": "user-sub",
    "cognito_client_secret": "client-secret",
    "cognito_refresh_token": "live-token",
    "credential_seed_refresh_token": "seed-token",
}


class FakeLoop:
    """Run thread-safe callbacks inline so tests stay deterministic."""

    def call_soon_threadsafe(self, callback, *args):
        callback(*args)


class FakeConfigEntries:
    def __init__(self, forward_error=None):
        self.forward_error = forward_error
        self.unload_result = True
        self.reloaded = []
        self.updates = []

    async def async_forward_entry_setups(self, _entry, _platforms):
        if self.forward_error is not None:
            raise self.forward_error

    async def async_unload_platforms(self, _entry, _platforms):
        return self.unload_result

    async def async_schedule_reload(self, entry_id):
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
        self.update_listeners = []

    def async_on_unload(self, callback):
        self.unload_callbacks.append(callback)

    def add_update_listener(self, listener):
        self.update_listeners.append(listener)
        return lambda: None


class FakeClient:
    def __init__(self, status_error=None):
        self.status_error = status_error
        self.closed = 0
        self.refresh_callback = None
        self.auth_callback = None
        self.state_callback = None

    def get_status(self):
        if self.status_error is not None:
            raise self.status_error
        return {"id": 1496}

    def close(self):
        self.closed += 1

    def set_state_change_callback(self, callback):
        self.state_callback = callback

    def set_refresh_token_callback(self, callback):
        self.refresh_callback = callback

    def set_auth_failure_callback(self, callback):
        self.auth_callback = callback


def _install_client(monkeypatch, client, polling=30):
    monkeypatch.setattr(
        "custom_components.tholz_fairland._build_client",
        lambda _entry: (client, polling),
    )


# --- settings precedence -------------------------------------------------


def test_options_take_precedence_over_stored_data():
    entry = SimpleNamespace(
        data={**CLOUD_DATA, "polling_interval": 30},
        options={"polling_interval": 120},
    )
    assert _entry_settings(entry)["polling_interval"] == 120


def test_edited_polling_interval_reaches_the_manager():
    """The options flow would be a no-op if data won the merge."""
    entry = SimpleNamespace(
        data=CLOUD_DATA,
        options={"polling_interval": 120},
    )
    _, polling = _build_client(entry)
    assert polling == 120


def test_cloud_defaults_apply_when_no_interval_is_stored():
    entry = SimpleNamespace(data=CLOUD_DATA, options={})
    _, polling = _build_client(entry)
    assert polling == 30


def test_transport_identity_ignores_options():
    """thing_id is never exposed by the options schema and must not be forgeable."""
    entry = SimpleNamespace(data=CLOUD_DATA, options={"thing_id": "attacker-thing"})
    client, _ = _build_client(entry)
    assert client.thing_id == "thing-uuid"


def test_seed_falls_back_to_the_live_token():
    live, seed = _resolve_credentials({"cognito_refresh_token": "only-token"})
    assert (live, seed) == ("only-token", "only-token")


def test_seed_is_kept_distinct_when_stored():
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
    _install_client(monkeypatch, client)

    assert await async_setup_entry(hass, entry)

    assert hass.data[DOMAIN][entry.entry_id]["client"] is client
    assert client.refresh_callback is not None
    assert client.auth_callback is not None
    assert hass.background == ["Tholz Fairland polling updater"]

    await async_unload_entry(hass, entry)


@pytest.mark.asyncio
async def test_unload_stops_polling_and_clears_domain_data(monkeypatch):
    hass, entry, client = FakeHass(), FakeEntry(CLOUD_DATA), FakeClient()
    _install_client(monkeypatch, client)
    await async_setup_entry(hass, entry)

    assert await async_unload_entry(hass, entry)

    assert DOMAIN not in hass.data
    assert client.closed == 1
    assert client.state_callback is None


@pytest.mark.asyncio
async def test_unload_is_refused_when_platforms_refuse(monkeypatch):
    hass, entry, client = FakeHass(), FakeEntry(CLOUD_DATA), FakeClient()
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
    _install_client(monkeypatch, client)

    with pytest.raises(ConfigEntryAuthFailed):
        await async_setup_entry(hass, entry)

    assert DOMAIN not in hass.data
    assert client.closed == 1


@pytest.mark.asyncio
async def test_unreachable_device_during_setup_retries_later(monkeypatch):
    client = FakeClient(status_error=OSError("no route to host"))
    hass, entry = FakeHass(), FakeEntry(CLOUD_DATA)
    _install_client(monkeypatch, client)

    with pytest.raises(ConfigEntryNotReady):
        await async_setup_entry(hass, entry)

    assert DOMAIN not in hass.data
    assert client.closed == 1


@pytest.mark.asyncio
async def test_platform_failure_releases_the_client(monkeypatch):
    """A half-registered runtime would leak an MQTT connection on retry."""
    hass = FakeHass(forward_error=RuntimeError("platform boom"))
    entry, client = FakeEntry(CLOUD_DATA), FakeClient()
    _install_client(monkeypatch, client)

    with pytest.raises(RuntimeError):
        await async_setup_entry(hass, entry)

    assert DOMAIN not in hass.data
    assert client.closed == 1
    assert hass.background == []


@pytest.mark.asyncio
async def test_second_entry_survives_teardown_of_the_first(monkeypatch):
    hass = FakeHass()
    first, second = FakeEntry(CLOUD_DATA), FakeEntry(CLOUD_DATA)
    second.entry_id = "entry-2"
    _install_client(monkeypatch, FakeClient())
    await async_setup_entry(hass, first)
    _install_client(monkeypatch, FakeClient())
    await async_setup_entry(hass, second)

    await async_unload_entry(hass, first)

    assert second.entry_id in hass.data[DOMAIN]

    await async_unload_entry(hass, second)


# --- rotated credential bookkeeping --------------------------------------


def test_rotated_token_is_persisted_without_reloading():
    hass, entry = FakeHass(), FakeEntry(CLOUD_DATA)
    hass.data[DOMAIN] = {entry.entry_id: {}}

    _adopt_rotated_token(hass, entry, "rotated-token")

    assert entry.data["cognito_refresh_token"] == "rotated-token"
    assert hass.data[DOMAIN][entry.entry_id][SKIP_RELOAD_KEY] is True


def test_rotated_token_clears_a_shadowing_options_copy():
    """An options token would otherwise win the merge and undo the rotation."""
    hass = FakeHass()
    entry = FakeEntry(CLOUD_DATA, options={"cognito_refresh_token": "stale"})
    hass.data[DOMAIN] = {entry.entry_id: {}}

    _adopt_rotated_token(hass, entry, "rotated-token")

    assert "cognito_refresh_token" not in entry.options
    assert _entry_settings(entry)["cognito_refresh_token"] == "rotated-token"


def test_adopting_an_unchanged_token_writes_nothing():
    hass, entry = FakeHass(), FakeEntry(CLOUD_DATA)
    hass.data[DOMAIN] = {entry.entry_id: {}}

    _adopt_rotated_token(hass, entry, "live-token")

    assert hass.config_entries.updates == []


def test_seed_is_never_replaced_by_a_rotated_token():
    hass, entry = FakeHass(), FakeEntry(CLOUD_DATA)
    hass.data[DOMAIN] = {entry.entry_id: {}}

    _adopt_rotated_token(hass, entry, "rotated-token")

    assert entry.data["credential_seed_refresh_token"] == "seed-token"


@pytest.mark.asyncio
async def test_reload_listener_ignores_our_own_token_write():
    hass, entry = FakeHass(), FakeEntry(CLOUD_DATA)
    hass.data[DOMAIN] = {entry.entry_id: {}}
    _adopt_rotated_token(hass, entry, "rotated-token")

    await _async_reload_entry(hass, entry)

    assert hass.config_entries.reloaded == []


@pytest.mark.asyncio
async def test_reload_listener_still_reloads_on_a_user_options_change():
    hass, entry = FakeHass(), FakeEntry(CLOUD_DATA)
    hass.data[DOMAIN] = {entry.entry_id: {}}

    await _async_reload_entry(hass, entry)

    assert hass.config_entries.reloaded == [entry.entry_id]


@pytest.mark.asyncio
async def test_reload_suppression_applies_only_once():
    hass, entry = FakeHass(), FakeEntry(CLOUD_DATA)
    hass.data[DOMAIN] = {entry.entry_id: {}}
    _adopt_rotated_token(hass, entry, "rotated-token")

    await _async_reload_entry(hass, entry)
    await _async_reload_entry(hass, entry)

    assert hass.config_entries.reloaded == [entry.entry_id]
