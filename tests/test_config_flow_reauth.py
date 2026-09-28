"""Credential-based reauthentication, challenge handling and entry seeding."""

import inspect
import time
from types import SimpleNamespace

import pytest

from custom_components.tholz_fairland import config_flow
from custom_components.tholz_fairland.cloud.client import FairlandCloudAuthError
from custom_components.tholz_fairland.cloud.cognito_auth import (
    AuthSuccess,
    AuthTokens,
    ChallengeRequired,
    ChallengeState,
    CognitoChallengeRequired,
    CognitoChallengeResponseRejected,
    CognitoCredentialRejected,
    CognitoTransientError,
)
from custom_components.tholz_fairland.config_flow import (
    TholzFairlandConfigFlow,
    TholzFairlandOptionsFlowHandler,
    _options_schema,
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
    "cognito_username": "person@example.test",
    "cognito_recovery_mode": "interactive",
}


def _tokens(refresh="fresh-token"):
    return AuthTokens(
        id_token="id",
        access_token="ac",
        refresh_token=refresh,
        expires_at=time.time() + 3600,
    )


class FakeHass:
    async def async_add_executor_job(self, target, *args):
        return target(*args)


class FakeAuthenticator:
    """Records SRP calls and returns scripted outcomes."""

    login_outcome = None
    login_error = None
    challenge_outcome = None
    challenge_error = None
    refresh_error = None
    refresh_result = None

    def __init__(self, *_args, **_kwargs):
        pass

    def login_srp(self, username, password, client_secret):
        FakeAuthenticator.last_login = (username, password, client_secret)
        if FakeAuthenticator.login_error is not None:
            raise FakeAuthenticator.login_error
        return FakeAuthenticator.login_outcome

    def respond_to_challenge(self, state, response, client_secret):
        FakeAuthenticator.last_challenge = (state, response, client_secret)
        if FakeAuthenticator.challenge_error is not None:
            raise FakeAuthenticator.challenge_error
        return FakeAuthenticator.challenge_outcome

    def refresh(self, refresh_token, subject, client_secret):
        # Used by the flow's pre-commit subject verification. By default a
        # refresh grant issues no new token (refresh_token=None), so the SRP
        # token stays current; a test may set refresh_result to model rotation.
        FakeAuthenticator.last_verify = (refresh_token, subject, client_secret)
        if FakeAuthenticator.refresh_error is not None:
            raise FakeAuthenticator.refresh_error
        return FakeAuthenticator.refresh_result or _tokens(None)


class RecordingFlow(TholzFairlandConfigFlow):
    """Capture flow results instead of driving a real Home Assistant."""

    def __init__(self, entry):
        super().__init__()
        self._entry = entry
        self.hass = FakeHass()
        self.aborted = None
        self.form = None

    def _get_reauth_entry(self):
        return self._entry

    def _get_reconfigure_entry(self):
        return self._entry

    def async_show_form(self, **kwargs):
        self.form = kwargs
        return {"type": "form", **kwargs}

    def async_update_reload_and_abort(self, entry, **kwargs):
        self.aborted = {"entry": entry, **kwargs}
        return {"type": "abort", "reason": "reauth_successful", **kwargs}


def _entry(options=None):
    return SimpleNamespace(
        entry_id="entry-1", data=dict(CLOUD_DATA), options=dict(options or {})
    )


@pytest.fixture(autouse=True)
def _reset_fake():
    FakeAuthenticator.login_outcome = None
    FakeAuthenticator.login_error = None
    FakeAuthenticator.challenge_outcome = None
    FakeAuthenticator.challenge_error = None
    FakeAuthenticator.refresh_error = None
    FakeAuthenticator.refresh_result = None


def _patch_authenticator(monkeypatch):
    monkeypatch.setattr(config_flow, "CognitoAuthenticator", FakeAuthenticator)


# --- reauth entry points -------------------------------------------------


@pytest.mark.asyncio
async def test_reauth_shows_the_credential_form():
    flow = RecordingFlow(_entry())

    result = await flow.async_step_reauth(CLOUD_DATA)

    assert result["type"] == "form"
    assert flow.form["step_id"] == "reauth_confirm"


def test_reauth_form_asks_for_username_and_password():
    keys = {marker.schema for marker in _reauth_schema({}).schema}
    assert {"cognito_username", "cognito_password"} <= keys


@pytest.mark.asyncio
async def test_successful_login_updates_the_same_entry(monkeypatch):
    _patch_authenticator(monkeypatch)
    FakeAuthenticator.login_outcome = AuthSuccess(
        tokens=_tokens("fresh-token"), subject="fresh-sub"
    )
    entry = _entry()
    flow = RecordingFlow(entry)

    result = await flow.async_step_reauth_confirm(
        {
            "cognito_username": "person@example.test",
            "cognito_password": "pw",
            "cognito_recovery_mode": "interactive",
        }
    )

    assert result["type"] == "abort"
    data = flow.aborted["data"]
    assert data["cognito_refresh_token"] == "fresh-token"
    assert data["credential_seed_refresh_token"] == "fresh-token"
    assert data["credential_seed_refresh_token"] == "fresh-token"
    assert data["cognito_user_sub"] == "fresh-sub"
    assert data["cognito_username"] == "person@example.test"


@pytest.mark.asyncio
async def test_a_rotation_during_verification_is_what_gets_persisted(monkeypatch):
    """If the verification refresh rotates the token, persist the rotated one."""
    _patch_authenticator(monkeypatch)
    FakeAuthenticator.login_outcome = AuthSuccess(
        tokens=_tokens("srp-token"), subject="fresh-sub"
    )
    FakeAuthenticator.refresh_result = _tokens("rotated-on-verify")
    flow = RecordingFlow(_entry())

    await flow.async_step_reauth_confirm(
        {"cognito_username": "u", "cognito_password": "pw"}
    )

    data = flow.aborted["data"]
    assert data["cognito_refresh_token"] == "rotated-on-verify"
    assert data["credential_seed_refresh_token"] == "rotated-on-verify"


@pytest.mark.asyncio
async def test_automatic_mode_stores_the_password(monkeypatch):
    _patch_authenticator(monkeypatch)
    FakeAuthenticator.login_outcome = AuthSuccess(tokens=_tokens(), subject="fresh-sub")
    flow = RecordingFlow(_entry())

    await flow.async_step_reauth_confirm(
        {
            "cognito_username": "person@example.test",
            "cognito_password": "secret-pw",
            "cognito_recovery_mode": "automatic",
        }
    )

    assert flow.aborted["data"]["cognito_password"] == "secret-pw"
    assert flow.aborted["data"]["cognito_recovery_mode"] == "automatic"


@pytest.mark.asyncio
async def test_interactive_mode_never_stores_a_password(monkeypatch):
    _patch_authenticator(monkeypatch)
    FakeAuthenticator.login_outcome = AuthSuccess(tokens=_tokens(), subject="fresh-sub")
    flow = RecordingFlow(_entry())

    await flow.async_step_reauth_confirm(
        {
            "cognito_username": "person@example.test",
            "cognito_password": "secret-pw",
            "cognito_recovery_mode": "interactive",
        }
    )

    assert "cognito_password" not in flow.aborted["data"]


@pytest.mark.asyncio
async def test_reauth_clears_a_shadowing_options_token(monkeypatch):
    _patch_authenticator(monkeypatch)
    FakeAuthenticator.login_outcome = AuthSuccess(tokens=_tokens(), subject="fresh-sub")
    flow = RecordingFlow(_entry(options={"cognito_refresh_token": "older"}))

    await flow.async_step_reauth_confirm(
        {"cognito_username": "u", "cognito_password": "pw"}
    )

    assert "cognito_refresh_token" not in flow.aborted["options"]


@pytest.mark.asyncio
async def test_reauth_reuses_the_stored_client_secret(monkeypatch):
    _patch_authenticator(monkeypatch)
    FakeAuthenticator.login_outcome = AuthSuccess(tokens=_tokens(), subject="fresh-sub")
    flow = RecordingFlow(_entry())

    await flow.async_step_reauth_confirm(
        {"cognito_username": "u", "cognito_password": "pw"}
    )

    assert FakeAuthenticator.last_login[2] == "client-secret"


@pytest.mark.asyncio
async def test_rejected_credentials_redisplay_the_form(monkeypatch):
    _patch_authenticator(monkeypatch)
    FakeAuthenticator.login_error = CognitoCredentialRejected("nope")
    flow = RecordingFlow(_entry())

    result = await flow.async_step_reauth_confirm(
        {"cognito_username": "u", "cognito_password": "bad"}
    )

    assert result["type"] == "form"
    assert flow.form["errors"] == {"base": "invalid_auth"}
    assert flow.aborted is None


@pytest.mark.asyncio
async def test_transient_failure_reports_cannot_connect(monkeypatch):
    _patch_authenticator(monkeypatch)
    FakeAuthenticator.login_error = CognitoTransientError("throttle")
    flow = RecordingFlow(_entry())

    await flow.async_step_reauth_confirm(
        {"cognito_username": "u", "cognito_password": "pw"}
    )

    assert flow.form["errors"] == {"base": "cannot_connect"}


@pytest.mark.asyncio
async def test_unsupported_challenge_is_reported(monkeypatch):
    _patch_authenticator(monkeypatch)
    FakeAuthenticator.login_error = CognitoChallengeRequired(
        "CUSTOM_CHALLENGE", supported=False
    )
    flow = RecordingFlow(_entry())

    await flow.async_step_reauth_confirm(
        {"cognito_username": "u", "cognito_password": "pw"}
    )

    assert flow.form["errors"] == {"base": "unsupported_challenge"}


# --- interactive challenge -----------------------------------------------


@pytest.mark.asyncio
async def test_supported_challenge_advances_to_the_challenge_step(monkeypatch):
    _patch_authenticator(monkeypatch)
    FakeAuthenticator.login_outcome = ChallengeRequired(
        state=ChallengeState(
            challenge_name="SMS_MFA", username="u", session="SECRET-SESSION"
        )
    )
    flow = RecordingFlow(_entry())

    result = await flow.async_step_reauth_confirm(
        {"cognito_username": "u", "cognito_password": "pw"}
    )

    assert result["type"] == "form"
    assert flow.form["step_id"] == "challenge"


@pytest.mark.asyncio
async def test_challenge_completion_persists_fresh_tokens(monkeypatch):
    _patch_authenticator(monkeypatch)
    flow = RecordingFlow(_entry())
    flow._challenge_state = ChallengeState(
        challenge_name="SMS_MFA", username="u", session="SECRET-SESSION"
    )
    flow._reauth_username = "u"
    flow._reauth_password = "pw"
    flow._reauth_client_secret = "client-secret"
    FakeAuthenticator.challenge_outcome = AuthSuccess(
        tokens=_tokens("post-mfa"), subject="mfa-sub"
    )

    result = await flow.async_step_challenge({"challenge_response": "123456"})

    assert result["type"] == "abort"
    assert flow.aborted["data"]["cognito_refresh_token"] == "post-mfa"


@pytest.mark.asyncio
async def test_challenge_session_is_never_persisted(monkeypatch):
    _patch_authenticator(monkeypatch)
    flow = RecordingFlow(_entry())
    flow._challenge_state = ChallengeState(
        challenge_name="SMS_MFA", username="u", session="SECRET-SESSION"
    )
    flow._reauth_username = "u"
    flow._reauth_password = "pw"
    flow._reauth_client_secret = "client-secret"
    FakeAuthenticator.challenge_outcome = AuthSuccess(
        tokens=_tokens("post-mfa"), subject="mfa-sub"
    )

    await flow.async_step_challenge({"challenge_response": "123456"})

    serialized = repr(flow.aborted["data"]) + repr(flow.aborted["options"])
    assert "SECRET-SESSION" not in serialized
    # Flow memory is scrubbed once the challenge completes.
    assert flow._challenge_state is None
    assert flow._reauth_password is None


@pytest.mark.asyncio
async def test_new_password_challenge_stores_the_new_password(monkeypatch):
    """Automatic mode must store the new password, not the superseded one."""
    _patch_authenticator(monkeypatch)
    flow = RecordingFlow(_entry())
    flow._challenge_state = ChallengeState(
        challenge_name="NEW_PASSWORD_REQUIRED", username="u", session="S"
    )
    flow._reauth_username = "u"
    flow._reauth_password = "old-password"
    flow._reauth_mode = "automatic"
    flow._reauth_client_secret = "client-secret"
    FakeAuthenticator.challenge_outcome = AuthSuccess(
        tokens=_tokens("post-npr"), subject="npr-sub"
    )

    await flow.async_step_challenge({"challenge_response": "Brand-New-Pass1"})

    assert flow.aborted["data"]["cognito_password"] == "Brand-New-Pass1"


@pytest.mark.asyncio
async def test_new_password_then_mfa_still_stores_the_new_password(monkeypatch):
    """A new password followed by an MFA challenge must not be forgotten."""
    _patch_authenticator(monkeypatch)
    flow = RecordingFlow(_entry())
    flow._challenge_state = ChallengeState(
        challenge_name="NEW_PASSWORD_REQUIRED", username="u", session="S1"
    )
    flow._reauth_username = "u"
    flow._reauth_password = "old-password"
    flow._reauth_mode = "automatic"
    flow._reauth_client_secret = "client-secret"

    # Step 1: new password accepted, but Cognito now asks for an MFA code.
    FakeAuthenticator.challenge_outcome = ChallengeRequired(
        state=ChallengeState(
            challenge_name="SOFTWARE_TOKEN_MFA", username="u", session="S2"
        )
    )
    await flow.async_step_challenge({"challenge_response": "Brand-New-Pass2"})
    assert flow._challenge_state.challenge_name == "SOFTWARE_TOKEN_MFA"

    # Step 2: MFA code completes the sign-in.
    FakeAuthenticator.challenge_outcome = AuthSuccess(
        tokens=_tokens("post-chain"), subject="chain-sub"
    )
    await flow.async_step_challenge({"challenge_response": "123456"})

    assert flow.aborted["data"]["cognito_password"] == "Brand-New-Pass2"


@pytest.mark.asyncio
async def test_wrong_code_stays_on_the_challenge_step(monkeypatch):
    _patch_authenticator(monkeypatch)
    flow = RecordingFlow(_entry())
    flow._challenge_state = ChallengeState(
        challenge_name="SMS_MFA", username="u", session="S"
    )
    flow._reauth_username = "u"
    flow._reauth_password = "pw"
    flow._reauth_client_secret = "client-secret"
    FakeAuthenticator.challenge_error = CognitoChallengeResponseRejected("bad code")

    result = await flow.async_step_challenge({"challenge_response": "000000"})

    assert result["type"] == "form"
    assert flow.form["step_id"] == "challenge"
    assert flow.form["errors"] == {"base": "challenge_failed"}


@pytest.mark.asyncio
async def test_a_wrong_account_login_writes_nothing(monkeypatch):
    """A token whose subject cannot refresh must never be committed."""
    _patch_authenticator(monkeypatch)
    FakeAuthenticator.login_outcome = AuthSuccess(tokens=_tokens(), subject="mismatch")
    FakeAuthenticator.refresh_error = CognitoCredentialRejected("wrong subject")
    flow = RecordingFlow(_entry())

    result = await flow.async_step_reauth_confirm(
        {"cognito_username": "u", "cognito_password": "pw"}
    )

    assert result["type"] == "form"
    assert flow.form["errors"] == {"base": "wrong_account"}
    assert flow.aborted is None


# --- cancellation safety -------------------------------------------------


def test_flow_removal_hook_is_a_synchronous_callback():
    """Home Assistant's FlowManager calls ``async_remove`` without awaiting it."""
    assert not inspect.iscoroutinefunction(TholzFairlandConfigFlow.async_remove)


def _cancel_first(flow, original):
    """Simulate the user closing the dialog while a Cognito call is in flight."""

    def _call(self, *args):
        flow.async_remove()  # what Home Assistant does when a flow is removed
        return original(self, *args)

    return _call


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["login_srp", "refresh", "respond_to_challenge"])
async def test_a_reauth_cancelled_mid_flight_writes_nothing(monkeypatch, phase):
    _patch_authenticator(monkeypatch)
    success = AuthSuccess(tokens=_tokens("late-token"), subject="late-sub")
    FakeAuthenticator.login_outcome = success
    FakeAuthenticator.challenge_outcome = success
    flow = RecordingFlow(_entry())
    monkeypatch.setattr(
        FakeAuthenticator, phase, _cancel_first(flow, getattr(FakeAuthenticator, phase))
    )

    if phase == "respond_to_challenge":
        flow._challenge_state = ChallengeState(
            challenge_name="SMS_MFA", username="u", session="S"
        )
        flow._reauth_username = "u"
        flow._reauth_password = "pw"
        flow._reauth_mode = "automatic"
        flow._reauth_client_secret = "client-secret"
        result = await flow.async_step_challenge({"challenge_response": "123456"})
    else:
        result = await flow.async_step_reauth_confirm(
            {
                "cognito_username": "u",
                "cognito_password": "pw",
                "cognito_recovery_mode": "automatic",
            }
        )

    assert result["type"] == "abort"
    assert result["reason"] == "reauth_unsuccessful"
    assert flow.aborted is None
    assert flow._reauth_password is None
    assert flow._challenge_state is None


# --- validation error mapping --------------------------------------------


def _patch_client(monkeypatch, error):
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

    monkeypatch.setattr(config_flow, "FairlandCloudClient", _Client)
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
    instances = _patch_client(monkeypatch, TimeoutError("timed out"))

    with pytest.raises(ValueError, match="cannot_connect"):
        await _validate_cloud(FakeHass(), CLOUD_DATA)

    assert instances[0].closed is True


def test_options_flow_uses_home_assistant_managed_config_entry():
    handler = TholzFairlandConfigFlow.async_get_options_flow(_entry())

    assert isinstance(handler, TholzFairlandOptionsFlowHandler)
    assert "__init__" not in TholzFairlandOptionsFlowHandler.__dict__


# --- reconfigure --------------------------------------------------------


@pytest.mark.asyncio
async def test_reconfigure_to_interactive_removes_the_password():
    entry = _entry()
    entry.data["cognito_recovery_mode"] = "automatic"
    entry.data["cognito_password"] = "stored-pw"
    flow = RecordingFlow(entry)

    await flow.async_step_reconfigure({"cognito_recovery_mode": "interactive"})

    assert "cognito_password" not in flow.aborted["data"]
    assert flow.aborted["data"]["cognito_recovery_mode"] == "interactive"


@pytest.mark.asyncio
async def test_reconfigure_to_automatic_stores_a_new_password():
    flow = RecordingFlow(_entry())

    await flow.async_step_reconfigure(
        {"cognito_recovery_mode": "automatic", "cognito_password": "new-pw"}
    )

    assert flow.aborted["data"]["cognito_password"] == "new-pw"
    assert flow.aborted["data"]["cognito_recovery_mode"] == "automatic"


@pytest.mark.asyncio
async def test_reconfigure_to_automatic_without_a_password_is_refused():
    flow = RecordingFlow(_entry())

    result = await flow.async_step_reconfigure({"cognito_recovery_mode": "automatic"})

    assert result["type"] == "form"
    assert flow.form["errors"] == {"base": "missing_password"}


# --- options flow (polling only) ----------------------------------------


class _OptionsHass:
    def __init__(self):
        self.updated = None
        self.reloaded = []
        self.config_entries = self

    def async_update_entry(self, _entry, **kwargs):
        self.updated = kwargs

    def async_schedule_reload(self, entry_id):
        self.reloaded.append(entry_id)


class RecordingOptionsFlow(TholzFairlandOptionsFlowHandler):
    """Inject a config entry (HA owns the read-only property) and record results."""

    def __init__(self, entry, hass):
        self._entry = entry
        self.hass = hass
        self.created = None

    @property
    def config_entry(self):
        return self._entry

    def async_create_entry(self, title, data):
        self.created = {"title": title, "data": data}
        return {"type": "create_entry", "data": data}


@pytest.mark.asyncio
async def test_options_flow_saves_polling_and_reloads():
    hass = _OptionsHass()
    entry = SimpleNamespace(entry_id="entry-1", data=dict(CLOUD_DATA), options={})
    handler = RecordingOptionsFlow(entry, hass)

    result = await handler.async_step_init({"polling_interval": 90})

    assert result["type"] == "create_entry"
    assert hass.updated["options"] == {"polling_interval": 90}
    assert hass.reloaded == ["entry-1"]
    # No credential key is ever written to options.
    assert set(hass.updated["options"]) == {"polling_interval"}


def test_options_form_offers_only_the_polling_interval():
    keys = {marker.schema for marker in _options_schema({}).schema}
    assert keys == {"polling_interval"}


@pytest.mark.asyncio
async def test_new_entries_store_a_recovery_seed_and_mode(monkeypatch):
    """New entries retain the initial token as a recovery seed."""

    async def _ok(_hass, _values):
        return None

    monkeypatch.setattr(config_flow, "_validate_cloud", _ok)
    created = {}

    class _Flow(TholzFairlandConfigFlow):
        def __init__(self):
            super().__init__()
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
    assert created["data"]["credential_seed_refresh_token"] == "first-token"
    assert created["data"]["cognito_recovery_mode"] == "interactive"
    assert created["unique_id"] == "thing-uuid"


@pytest.mark.asyncio
async def test_automatic_mode_requires_a_password_at_setup(monkeypatch):
    async def _ok(_hass, _values):
        return None

    monkeypatch.setattr(config_flow, "_validate_cloud", _ok)

    class _Flow(TholzFairlandConfigFlow):
        def __init__(self):
            super().__init__()
            self.hass = FakeHass()

        def async_show_form(self, **kwargs):
            return {"type": "form", **kwargs}

    user_input = {
        "name": "Fairland",
        "cognito_user_sub": "user-sub",
        "cognito_client_secret": "client-secret",
        "cognito_refresh_token": "first-token",
        "thing_id": "thing-uuid",
        "cognito_recovery_mode": "automatic",
    }
    result = await _Flow().async_step_user(user_input)

    assert result["type"] == "form"
    assert result["errors"] == {"base": "missing_password"}
