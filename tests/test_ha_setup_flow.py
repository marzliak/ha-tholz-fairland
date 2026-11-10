"""Home Assistant setup, unload, failure and reauth flows with the cloud mocked."""

from __future__ import annotations

import copy
import logging
import time
from importlib.util import find_spec

import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType

from custom_components.tholz_fairland.cloud.client import (
    API_BASE_URL,
    FairlandCloudAuthError,
    FairlandCloudClient,
    FairlandCloudError,
)
from custom_components.tholz_fairland.cloud.cognito_auth import (
    AuthSuccess,
    AuthTokens,
    CognitoAuthenticator,
)
from custom_components.tholz_fairland.utils.const import DOMAIN

# Do not hide a broken official installation behind the legacy framework.
if find_spec("pytest_homeassistant") is not None:
    from pytest_homeassistant.common import MockConfigEntry
else:
    from pytest_homeassistant_custom_component.common import MockConfigEntry

pytestmark = pytest.mark.asyncio

USERNAME = "owner@example.test"
PASSWORD = "synthetic-password"
THING_ID = "00000000-0000-4000-8000-000000000001"
SUB_OWNER = "sub-synthetic-owner"
SUB_OTHER = "sub-synthetic-other"
REFRESH = "refresh-synthetic"
DEVICES = {
    "devices": [
        {
            "thingId": THING_ID,
            "productId": 1496,
            "productName": "Fairland heat pump",
            "name": "Piscina",
        }
    ]
}
SHADOW = {
    "id": 1496,
    "connected": True,
    "error": 0,
    "heatings": {
        "heat0": {
            "type": 7,
            "on": False,
            "opMode": 0,
            "sp": 330,
            "step": 10,
            "fanMode": 1,
            "t1": 180,
            "t2": 240,
            "t3": 230,
        }
    },
}


def _tokens() -> AuthTokens:
    return AuthTokens(
        id_token="id-synthetic",
        access_token="access-synthetic",
        refresh_token=REFRESH,
        expires_at=time.time() + 3600,
    )


def _success(subject: str) -> AuthSuccess:
    return AuthSuccess(tokens=_tokens(), subject=subject)


@pytest.fixture
def cloud(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Replace the cloud boundary; every knob is per-test and synthetic."""
    state: dict = {"error": None, "login": lambda _u, _p: _success(SUB_OWNER)}

    def get_status(_self):
        if state["error"] is not None:
            raise state["error"]
        return copy.deepcopy(SHADOW)

    monkeypatch.setattr(FairlandCloudClient, "get_status", get_status)

    def refuse_command(_self, _data):
        pytest.fail("Setup, reload, and reauthentication must never command equipment")

    monkeypatch.setattr(FairlandCloudClient, "set_status", refuse_command)
    monkeypatch.setattr(FairlandCloudClient, "close", lambda _self: None)
    monkeypatch.setattr(
        CognitoAuthenticator,
        "login_srp",
        lambda _self, username, password, _secret: state["login"](username, password),
    )
    monkeypatch.setattr(
        CognitoAuthenticator,
        "refresh",
        lambda _self, _token, _subject, _secret: _tokens(),
    )
    return state


def _entry_data() -> dict:
    return {
        "name": "Fairland",
        "thing_id": THING_ID,
        "cognito_user_sub": SUB_OWNER,
        "cognito_client_secret": "",
        "cognito_refresh_token": REFRESH,
        "credential_seed_refresh_token": REFRESH,
        "cognito_username": USERNAME,
        "cognito_password": PASSWORD,
        "cognito_recovery_mode": "automatic",
        "polling_interval": 30,
    }


def _entry(hass) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        minor_version=4,
        title="Fairland",
        unique_id=THING_ID,
        data=_entry_data(),
        options={},
    )
    entry.add_to_hass(hass)
    return entry


@pytest.mark.usefixtures("enable_custom_integrations", "cloud")
async def test_user_flow_sets_up_entities_and_unloads_cleanly(
    hass,
    aioclient_mock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    aioclient_mock.get(f"{API_BASE_URL}/", json=DEVICES)

    flow = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"],
        {
            "name": "Fairland",
            "cognito_username": USERNAME,
            "cognito_password": PASSWORD,
            "polling_interval": 30,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.data["cognito_refresh_token"] == REFRESH
    assert "cognito_password" not in entry.options

    heaters = hass.states.async_all("water_heater")
    assert len(heaters) == 1
    assert heaters[0].state == "off"
    assert heaters[0].attributes["temperature"] == 33.0

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    # Home Assistant intentionally keeps restored/unavailable states for
    # registered entities after unload. Their absence is not the contract.
    restored = hass.states.get(heaters[0].entity_id)
    assert restored is not None
    assert restored.state == "unavailable"
    assert restored.attributes.get("restored") is True
    assert DOMAIN not in hass.data

    # Reload the same entry, proving the registered entity identity survives.
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get(heaters[0].entity_id).state == "off"
    assert len(hass.states.async_all("water_heater")) == 1
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert PASSWORD not in caplog.text
    assert REFRESH not in caplog.text


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_rejected_session_at_setup_starts_reauthentication(hass, cloud) -> None:
    entry = _entry(hass)
    cloud["error"] = FairlandCloudAuthError("rejected")

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR
    sources = [
        flow["context"]["source"]
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    ]
    assert config_entries.SOURCE_REAUTH in sources


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_transient_device_failure_retries_setup(hass, cloud) -> None:
    entry = _entry(hass)
    cloud["error"] = FairlandCloudError("unreachable")

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_reauth_with_another_account_changes_nothing(hass, cloud) -> None:
    entry = _entry(hass)
    before = copy.deepcopy(dict(entry.data))
    cloud["login"] = lambda _u, _p: _success(SUB_OTHER)

    flow = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=entry.data,
    )
    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"],
        {"cognito_username": USERNAME, "cognito_password": "other-synthetic"},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "wrong_account"}
    assert dict(entry.data) == before
