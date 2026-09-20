"""Re-authentication flow and cloud-entry credential seeding."""

from types import SimpleNamespace

import pytest

from custom_components.tholz_fairland.cloud.client import FairlandCloudAuthError
from custom_components.tholz_fairland.config_flow import (
    TholzFairlandConfigFlow,
    TholzFairlandOptionsFlowHandler,
    _reauth_schema,
    _validate_cloud,
)

CLOUD_DATA = {
    "name": "Fairland",
    "thing_id": "thing-uuid",
    "cognito_user_sub": "user-sub",
    "cognito_client_secret": "client-secret",
    "cognito_refresh_token": "stale-token",
    "credential_seed_refresh_token": "stale-token",
}


class FakeHass:
    async def async_add_executor_job(self, target, *args):
        return target(*args)


class RecordingFlow(TholzFairlandConfigFlow):
    """Capture the flow results instead of driving a real Home Assistant."""

    def __init__(self, entry, validate=None):
        self._entry = entry
        self._validate = validate
        self.hass = FakeHass()
        self.aborted = None
        self.form = None

    def _get_reauth_entry(self):
        return self._entry

    def async_show_form(self, **kwargs):
        self.form = kwargs
        return {"type": "form", **kwargs}

    def async_abort(self, **kwargs):
        self.aborted = kwargs
        return {"type": "abort", **kwargs}

    def async_update_reload_and_abort(self, entry, **kwargs):
        self.aborted = {"entry": entry, **kwargs}
        return {"type": "abort", "reason": "reauth_successful", **kwargs}


def _entry(options=None):
    return SimpleNamespace(
        entry_id="entry-1", data=dict(CLOUD_DATA), options=dict(options or {})
    )


def _patch_validate(monkeypatch, error=None):
    calls = []

    async def _fake(_hass, values):
        calls.append(values)
        if error is not None:
            raise error

    monkeypatch.setattr(
        "custom_components.tholz_fairland.config_flow._validate_cloud", _fake
    )
    return calls


@pytest.mark.asyncio
async def test_reauth_shows_the_token_form():
    flow = RecordingFlow(_entry())

    result = await flow.async_step_reauth(CLOUD_DATA)

    assert result["type"] == "form"
    assert flow.form["step_id"] == "reauth_confirm"


@pytest.mark.asyncio
async def test_reauth_confirm_accepts_home_assistants_positional_call(monkeypatch):
    """Home Assistant calls every step as ``async_step_x(user_input)``."""
    _patch_validate(monkeypatch)
    entry = _entry()
    flow = RecordingFlow(entry)

    step = flow.async_step_reauth_confirm
    result = await step({"cognito_refresh_token": "fresh-token"})

    assert result["type"] == "abort"
    assert entry.data["cognito_refresh_token"] == "stale-token"
    assert flow.aborted["data"]["cognito_refresh_token"] == "fresh-token"


@pytest.mark.asyncio
async def test_a_pasted_token_becomes_the_new_recovery_seed(monkeypatch):
    _patch_validate(monkeypatch)
    flow = RecordingFlow(_entry())

    await flow.async_step_reauth_confirm({"cognito_refresh_token": "fresh-token"})

    assert flow.aborted["data"]["credential_seed_refresh_token"] == "fresh-token"


@pytest.mark.asyncio
async def test_reauth_clears_a_shadowing_options_token(monkeypatch):
    _patch_validate(monkeypatch)
    flow = RecordingFlow(_entry(options={"cognito_refresh_token": "older"}))

    await flow.async_step_reauth_confirm({"cognito_refresh_token": "fresh-token"})

    assert "cognito_refresh_token" not in flow.aborted["options"]


@pytest.mark.asyncio
async def test_reauth_reuses_stored_identity_fields(monkeypatch):
    """Only the token is re-entered; everything else comes from the entry."""
    calls = _patch_validate(monkeypatch)
    flow = RecordingFlow(_entry())

    await flow.async_step_reauth_confirm({"cognito_refresh_token": "fresh-token"})

    assert calls[0]["thing_id"] == "thing-uuid"
    assert calls[0]["cognito_user_sub"] == "user-sub"
    assert calls[0]["cognito_refresh_token"] == "fresh-token"


@pytest.mark.asyncio
async def test_a_rejected_token_redisplays_the_form(monkeypatch):
    _patch_validate(monkeypatch, error=ValueError("invalid_token"))
    flow = RecordingFlow(_entry())

    result = await flow.async_step_reauth_confirm({"cognito_refresh_token": "nope"})

    assert result["type"] == "form"
    assert flow.form["errors"] == {"base": "invalid_token"}
    assert flow.aborted is None


@pytest.mark.asyncio
async def test_unreachable_cloud_reports_cannot_connect(monkeypatch):
    _patch_validate(monkeypatch, error=RuntimeError("network down"))
    flow = RecordingFlow(_entry())

    result = await flow.async_step_reauth_confirm({"cognito_refresh_token": "token"})

    assert result["type"] == "form"
    assert flow.form["errors"] == {"base": "cannot_connect"}


def test_reauth_form_only_asks_for_the_token():
    keys = {marker.schema for marker in _reauth_schema({}).schema}
    assert keys == {"cognito_refresh_token"}


# --- validation error mapping --------------------------------------------


def _patch_client(monkeypatch, error):
    """Swap in a probe client that always fails, and record every instance."""
    instances = []

    class _Client:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.closed = False
            instances.append(self)

        def get_status(self):
            raise error

        def close(self):
            self.closed = True

    monkeypatch.setattr(
        "custom_components.tholz_fairland.config_flow.FairlandCloudClient", _Client
    )
    return instances


@pytest.mark.asyncio
async def test_rejected_credentials_map_to_invalid_token(monkeypatch):
    _patch_client(monkeypatch, FairlandCloudAuthError("rejected"))

    with pytest.raises(ValueError, match="invalid_token"):
        await _validate_cloud(FakeHass(), CLOUD_DATA)


@pytest.mark.asyncio
async def test_transport_failures_map_to_cannot_connect(monkeypatch):
    _patch_client(monkeypatch, TimeoutError("timed out"))

    with pytest.raises(ValueError, match="cannot_connect"):
        await _validate_cloud(FakeHass(), CLOUD_DATA)


@pytest.mark.asyncio
async def test_validation_always_closes_its_probe_client(monkeypatch):
    """A leaked probe would keep an MQTT session open after a failed attempt."""
    instances = _patch_client(monkeypatch, TimeoutError("timed out"))

    with pytest.raises(ValueError, match="cannot_connect"):
        await _validate_cloud(FakeHass(), CLOUD_DATA)

    assert instances[0].closed is True


def test_options_flow_uses_home_assistant_managed_config_entry():
    """Current Home Assistant owns the read-only config_entry property."""
    handler = TholzFairlandConfigFlow.async_get_options_flow(_entry())

    assert isinstance(handler, TholzFairlandOptionsFlowHandler)
    assert "__init__" not in TholzFairlandOptionsFlowHandler.__dict__


@pytest.mark.asyncio
async def test_new_entries_store_a_recovery_seed(monkeypatch):
    """New entries retain the initial token as a recovery seed."""
    _patch_validate(monkeypatch)
    created = {}

    class _Flow(TholzFairlandConfigFlow):
        def __init__(self):
            self.hass = FakeHass()

        async def async_set_unique_id(self, unique_id):
            created["unique_id"] = unique_id

        def _abort_if_unique_id_configured(self):
            return None

        def async_create_entry(self, title, data):
            created["title"] = title
            created["data"] = data
            return {"type": "create_entry", "data": data}

        def async_show_form(self, **kwargs):
            return {"type": "form", **kwargs}

    user_input = {
        "name": "Fairland",
        "cognito_user_sub": "user-sub",
        "cognito_client_secret": "client-secret",
        "cognito_refresh_token": "first-token",
        "thing_id": "thing-uuid",
    }
    result = await _Flow().async_step_user(user_input)

    assert result["type"] == "create_entry"
    assert created["data"]["credential_seed_refresh_token"] == "first-token"
    assert created["unique_id"] == "thing-uuid"
