"""Credential-based reauthentication, challenge handling and entry seeding."""

import inspect
import time
from types import SimpleNamespace

import pytest

from custom_components.tholz_fairland import config_flow
from custom_components.tholz_fairland.cloud.auth_manager import CredentialUpdate
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
    _cloud_schema,
    _discover_devices,
    _options_schema,
    _reauth_schema,
    _reconfigure_schema,
    _supported_devices,
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
    "cognito_password": "stored-password",
    "cognito_recovery_mode": "automatic",
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
        self._flow_kind = "reauth"
        self._target_entry = entry
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
def _reset_fake(monkeypatch):
    FakeAuthenticator.login_outcome = None
    FakeAuthenticator.login_error = None
    FakeAuthenticator.challenge_outcome = None
    FakeAuthenticator.challenge_error = None
    FakeAuthenticator.refresh_error = None
    FakeAuthenticator.refresh_result = None

    async def _ok(_hass, _values):
        return _values["cognito_refresh_token"]

    monkeypatch.setattr(config_flow, "_validate_cloud", _ok)


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
        tokens=_tokens("fresh-token"), subject="user-sub"
    )
    entry = _entry()
    flow = RecordingFlow(entry)

    result = await flow.async_step_reauth_confirm(
        {
            "cognito_username": "person@example.test",
            "cognito_password": "pw",
        }
    )

    assert result["type"] == "abort"
    data = flow.aborted["data"]
    assert data["cognito_refresh_token"] == "fresh-token"
    assert data["credential_seed_refresh_token"] == "fresh-token"
    assert data["credential_seed_refresh_token"] == "fresh-token"
    assert data["cognito_user_sub"] == "user-sub"
    assert data["cognito_username"] == "person@example.test"
    assert data["cognito_password"] == "pw"
    assert data["cognito_recovery_mode"] == "automatic"


@pytest.mark.asyncio
async def test_a_rotation_during_verification_is_what_gets_persisted(monkeypatch):
    """If the verification refresh rotates the token, persist the rotated one."""
    _patch_authenticator(monkeypatch)
    FakeAuthenticator.login_outcome = AuthSuccess(
        tokens=_tokens("srp-token"), subject="user-sub"
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
async def test_reauth_always_stores_the_password_for_automatic_recovery(monkeypatch):
    _patch_authenticator(monkeypatch)
    FakeAuthenticator.login_outcome = AuthSuccess(tokens=_tokens(), subject="user-sub")
    flow = RecordingFlow(_entry())

    await flow.async_step_reauth_confirm(
        {
            "cognito_username": "person@example.test",
            "cognito_password": "secret-pw",
        }
    )

    assert flow.aborted["data"]["cognito_password"] == "secret-pw"
    assert flow.aborted["data"]["cognito_recovery_mode"] == "automatic"


@pytest.mark.asyncio
async def test_reauth_clears_a_shadowing_options_token(monkeypatch):
    _patch_authenticator(monkeypatch)
    FakeAuthenticator.login_outcome = AuthSuccess(tokens=_tokens(), subject="user-sub")
    flow = RecordingFlow(_entry(options={"cognito_refresh_token": "older"}))

    await flow.async_step_reauth_confirm(
        {"cognito_username": "u", "cognito_password": "pw"}
    )

    assert "cognito_refresh_token" not in flow.aborted["options"]


@pytest.mark.asyncio
async def test_reauth_ignores_and_clears_the_obsolete_client_secret(monkeypatch):
    _patch_authenticator(monkeypatch)
    FakeAuthenticator.login_outcome = AuthSuccess(tokens=_tokens(), subject="user-sub")
    flow = RecordingFlow(_entry())

    await flow.async_step_reauth_confirm(
        {"cognito_username": "u", "cognito_password": "pw"}
    )

    assert FakeAuthenticator.last_login[2] is None
    assert flow.aborted["data"]["cognito_client_secret"] == ""


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
async def test_initial_setup_completes_an_mfa_challenge_before_persisting(monkeypatch):
    _patch_authenticator(monkeypatch)
    FakeAuthenticator.login_outcome = ChallengeRequired(
        state=ChallengeState(
            challenge_name="SMS_MFA",
            username="person@example.test",
            session="SECRET-SESSION",
        )
    )
    FakeAuthenticator.challenge_outcome = AuthSuccess(
        tokens=_tokens("post-mfa"), subject="user-sub"
    )

    async def _discover(_hass, _id_token):
        return {"thing-uuid": "Pool heat pump"}

    monkeypatch.setattr(config_flow, "_discover_devices", _discover)
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

    flow = _Flow()
    result = await flow.async_step_user(
        {
            "name": "Fairland",
            "cognito_username": "person@example.test",
            "cognito_password": "synthetic-password",
        }
    )
    assert result["type"] == "form"
    assert result["step_id"] == "challenge"

    result = await flow.async_step_challenge({"challenge_response": "123456"})

    assert result["type"] == "create_entry"
    assert created["unique_id"] == "thing-uuid"
    assert created["data"]["cognito_password"] == "synthetic-password"
    assert "SECRET-SESSION" not in repr(created)


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
        tokens=_tokens("post-mfa"), subject="user-sub"
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
        tokens=_tokens("post-mfa"), subject="user-sub"
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
    flow._reauth_client_secret = "client-secret"
    FakeAuthenticator.challenge_outcome = AuthSuccess(
        tokens=_tokens("post-npr"), subject="user-sub"
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
        tokens=_tokens("post-chain"), subject="user-sub"
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


@pytest.mark.asyncio
@pytest.mark.parametrize("subject", [None, "", "   "])
async def test_reauth_fails_closed_when_the_stored_subject_is_unusable(
    monkeypatch, subject
):
    _patch_authenticator(monkeypatch)
    FakeAuthenticator.login_outcome = AuthSuccess(
        tokens=_tokens(), subject="new-account-subject"
    )
    entry = _entry()
    if subject is None:
        entry.data.pop("cognito_user_sub")
    else:
        entry.data["cognito_user_sub"] = subject
    flow = RecordingFlow(entry)

    result = await flow.async_step_reauth_confirm(
        {"cognito_username": "u", "cognito_password": "pw"}
    )

    assert result["type"] == "form"
    assert flow.form["errors"] == {"base": "configuration_error"}
    assert flow.aborted is None


@pytest.mark.asyncio
async def test_missing_challenge_state_aborts_instead_of_rendering_a_dead_form():
    flow = RecordingFlow(_entry())

    result = await flow.async_step_challenge()

    assert result["type"] == "abort"
    assert result["reason"] == "configuration_error"


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
    success = AuthSuccess(tokens=_tokens("late-token"), subject="user-sub")
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
        flow._reauth_client_secret = "client-secret"
        result = await flow.async_step_challenge({"challenge_response": "123456"})
    else:
        result = await flow.async_step_reauth_confirm(
            {
                "cognito_username": "u",
                "cognito_password": "pw",
            }
        )

    assert result["type"] == "abort"
    assert result["reason"] == "reauth_unsuccessful"
    assert flow.aborted is None
    assert flow._reauth_password is None
    assert flow._challenge_state is None


@pytest.mark.asyncio
@pytest.mark.parametrize("flow_kind", ["reauth", "reconfigure"])
async def test_cancellation_during_final_validation_writes_nothing(
    monkeypatch, flow_kind
):
    _patch_authenticator(monkeypatch)
    FakeAuthenticator.login_outcome = AuthSuccess(
        tokens=_tokens("late-token"), subject="user-sub"
    )
    flow = RecordingFlow(_entry())

    async def _cancel_during_validation(_hass, _data):
        flow.async_remove()

    monkeypatch.setattr(config_flow, "_validate_cloud", _cancel_during_validation)
    credentials = {"cognito_username": "u", "cognito_password": "pw"}

    if flow_kind == "reauth":
        result = await flow.async_step_reauth_confirm(credentials)
    else:
        result = await flow.async_step_reconfigure(credentials)

    assert result["type"] == "abort"
    assert result["reason"] == "reauth_unsuccessful"
    assert flow.aborted is None
    assert flow._reauth_password is None


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


@pytest.mark.asyncio
async def test_validation_returns_a_refresh_token_rotated_by_the_probe(monkeypatch):
    managers = []
    clients = []
    captured_callbacks = []

    class _Manager:
        def __init__(self, *_args, **_kwargs):
            self.persist_callback = None
            managers.append(self)

        def set_persist_callback(self, callback):
            self.persist_callback = callback
            if callback is not None:
                captured_callbacks.append(callback)

    class _Client:
        def __init__(self, *, auth_manager, **_kwargs):
            self.auth_manager = auth_manager
            self.closed = False
            clients.append(self)

        def get_status(self):
            # Accept the newest in-generation callback, then ignore an older
            # callback and one that belongs to another baseline token.
            self.auth_manager.persist_callback(
                CredentialUpdate(
                    current_token="rotated-during-validation",
                    generation=2,
                    expected_current="fresh-token",
                )
            )
            self.auth_manager.persist_callback(
                CredentialUpdate(
                    current_token="out-of-order-token",
                    generation=1,
                    expected_current="fresh-token",
                )
            )
            self.auth_manager.persist_callback(
                CredentialUpdate(
                    current_token="wrong-baseline-token",
                    generation=3,
                    expected_current="some-other-token",
                )
            )
            return {"reported": {}}

        def close(self):
            self.closed = True
            # Model a worker that retained the callback reference before detach.
            captured_callbacks[0](
                CredentialUpdate(
                    current_token="late-token-after-close",
                    generation=4,
                    expected_current="fresh-token",
                )
            )

    monkeypatch.setattr(config_flow, "FairlandAuthManager", _Manager)
    monkeypatch.setattr(config_flow, "FairlandCloudClient", _Client)

    token = await _validate_cloud(
        FakeHass(), CLOUD_DATA | {"cognito_refresh_token": "fresh-token"}
    )

    assert token == "rotated-during-validation"
    assert managers[0].persist_callback is None
    assert clients[0].closed is True


@pytest.mark.asyncio
async def test_setup_cancelled_during_discovery_does_not_repopulate_flow_state(
    monkeypatch,
):
    _patch_authenticator(monkeypatch)
    FakeAuthenticator.login_outcome = AuthSuccess(
        tokens=_tokens("first-token"), subject="user-sub"
    )
    validate_called = False
    flow = None

    async def _discover(_hass, _id_token):
        flow.async_remove()
        return {"thing-uuid": "Pool heat pump"}

    async def _validate(_hass, _data):
        nonlocal validate_called
        validate_called = True
        return _data["cognito_refresh_token"]

    monkeypatch.setattr(config_flow, "_discover_devices", _discover)
    monkeypatch.setattr(config_flow, "_validate_cloud", _validate)

    class _Flow(TholzFairlandConfigFlow):
        def __init__(self):
            super().__init__()
            self.hass = FakeHass()

        def async_show_form(self, **kwargs):
            return {"type": "form", **kwargs}

    flow = _Flow()
    result = await flow.async_step_user(
        {
            "name": "Fairland",
            "cognito_username": "person@example.test",
            "cognito_password": "synthetic-password",
        }
    )

    assert result["type"] == "abort"
    assert result["reason"] == "user_cancelled"
    assert validate_called is False
    assert flow._pending_user_input == {}
    assert flow._pending_success is None
    assert flow._verified_refresh_token is None
    assert flow._devices == {}
    assert flow._reauth_password is None


@pytest.mark.asyncio
@pytest.mark.parametrize("flow_kind", ["user", "reauth", "reconfigure"])
async def test_flow_persists_the_token_rotated_during_final_validation(
    monkeypatch, flow_kind
):
    _patch_authenticator(monkeypatch)
    FakeAuthenticator.login_outcome = AuthSuccess(
        tokens=_tokens("verified-token"), subject="user-sub"
    )

    async def _rotating_validation(_hass, _data):
        return "rotated-during-validation"

    monkeypatch.setattr(config_flow, "_validate_cloud", _rotating_validation)

    if flow_kind == "user":

        async def _discover(_hass, _id_token):
            return {"thing-uuid": "Pool heat pump"}

        monkeypatch.setattr(config_flow, "_discover_devices", _discover)
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

        result = await _Flow().async_step_user(
            {
                "name": "Fairland",
                "cognito_username": "person@example.test",
                "cognito_password": "synthetic-password",
            }
        )
        data = created["data"]
    else:
        flow = RecordingFlow(_entry())
        credentials = {"cognito_username": "u", "cognito_password": "pw"}
        if flow_kind == "reauth":
            result = await flow.async_step_reauth_confirm(credentials)
        else:
            result = await flow.async_step_reconfigure(credentials)
        data = flow.aborted["data"]

    assert result["type"] in {"abort", "create_entry"}
    assert data["cognito_refresh_token"] == "rotated-during-validation"
    assert data["credential_seed_refresh_token"] == "rotated-during-validation"


def test_options_flow_uses_home_assistant_managed_config_entry():
    handler = TholzFairlandConfigFlow.async_get_options_flow(_entry())

    assert isinstance(handler, TholzFairlandOptionsFlowHandler)
    assert "__init__" not in TholzFairlandOptionsFlowHandler.__dict__


# --- reconfigure --------------------------------------------------------


@pytest.mark.asyncio
async def test_reconfigure_validates_and_replaces_automatic_credentials(monkeypatch):
    _patch_authenticator(monkeypatch)
    FakeAuthenticator.login_outcome = AuthSuccess(tokens=_tokens(), subject="user-sub")
    flow = RecordingFlow(_entry())

    await flow.async_step_reconfigure(
        {"cognito_username": "person@example.test", "cognito_password": "new-pw"}
    )

    assert flow.aborted["data"]["cognito_password"] == "new-pw"
    assert flow.aborted["data"]["cognito_recovery_mode"] == "automatic"


def test_reconfigure_form_requires_username_and_password():
    markers = list(_reconfigure_schema({}).schema)
    assert {marker.schema for marker in markers} == {
        "cognito_username",
        "cognito_password",
    }
    assert all(marker.__class__.__name__ == "Required" for marker in markers)


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
async def test_new_entries_discover_the_device_and_store_validated_credentials(
    monkeypatch,
):
    """New entries derive tokens and device identity from account credentials."""
    _patch_authenticator(monkeypatch)
    FakeAuthenticator.login_outcome = AuthSuccess(
        tokens=_tokens("first-token"), subject="user-sub"
    )

    async def _discover(_hass, _id_token):
        return {"thing-uuid": "Pool heat pump"}

    monkeypatch.setattr(config_flow, "_discover_devices", _discover)
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
        "cognito_username": "person@example.test",
        "cognito_password": "synthetic-password",
    }
    result = await _Flow().async_step_user(user_input)

    assert result["type"] == "create_entry"
    assert created["data"]["credential_seed_refresh_token"] == "first-token"
    assert created["data"]["cognito_recovery_mode"] == "automatic"
    assert created["data"]["cognito_password"] == "synthetic-password"
    assert created["unique_id"] == "thing-uuid"


def test_device_discovery_keeps_only_supported_products_and_safe_labels():
    devices = _supported_devices(
        {
            "devices": [
                {
                    "thingId": "heat-1",
                    "name": "Main pool",
                    "productName": "Tholz TurboSilence",
                    "productId": 1496,
                    "private": "must-not-escape",
                },
                {
                    "thingId": "controller-1",
                    "name": "Controller",
                    "productName": "Basic SmartPool 2ª Ger.",
                    "productId": 1000,
                },
            ]
        }
    )

    assert devices == {"heat-1": "Main pool"}
    assert "must-not-escape" not in repr(devices)


class _DiscoveryResponse:
    def __init__(self, status=200, payload=None, json_error=None):
        self.status = status
        self._payload = payload
        self._json_error = json_error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def json(self, **_kwargs):
        if self._json_error is not None:
            raise self._json_error
        return self._payload


class _DiscoverySession:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.request = None

    def get(self, url, **kwargs):
        self.request = (url, kwargs)
        if self.error is not None:
            raise self.error
        return self.response


@pytest.mark.asyncio
async def test_discover_devices_uses_confirmed_master_contract(monkeypatch):
    payload = {
        "devices": [
            {
                "thingId": "heat-1",
                "name": "Main pool",
                "productName": "Tholz TurboSilence",
                "productId": 1496,
            },
            {
                "thingId": "controller-1",
                "name": "Controller",
                "productName": "Basic SmartPool 2ª Ger.",
                "productId": 1000,
            },
        ]
    }
    session = _DiscoverySession(_DiscoveryResponse(payload=payload))
    monkeypatch.setattr(config_flow, "async_get_clientsession", lambda _hass: session)

    devices = await _discover_devices(FakeHass(), "synthetic-id-token")

    assert devices == {"heat-1": "Main pool"}
    assert session.request is not None
    assert session.request[0].endswith("/master/")
    assert session.request[1]["headers"] == {"Authorization": "synthetic-id-token"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (_DiscoveryResponse(status=401), "invalid_auth"),
        (_DiscoveryResponse(status=403), "invalid_auth"),
        (_DiscoveryResponse(status=503), "cannot_connect"),
        (
            _DiscoveryResponse(json_error=ValueError("malformed synthetic JSON")),
            "cannot_connect",
        ),
    ],
)
async def test_discover_devices_maps_http_and_json_failures(
    monkeypatch, response, expected
):
    monkeypatch.setattr(
        config_flow,
        "async_get_clientsession",
        lambda _hass: _DiscoverySession(response),
    )

    with pytest.raises(ValueError, match=f"^{expected}$"):
        await _discover_devices(FakeHass(), "synthetic-id-token")


@pytest.mark.asyncio
async def test_discover_devices_maps_timeout(monkeypatch):
    monkeypatch.setattr(
        config_flow,
        "async_get_clientsession",
        lambda _hass: _DiscoverySession(error=TimeoutError()),
    )

    with pytest.raises(ValueError, match=r"^cannot_connect$"):
        await _discover_devices(FakeHass(), "synthetic-id-token")


@pytest.mark.asyncio
async def test_discover_devices_returns_empty_for_incompatible_products(monkeypatch):
    session = _DiscoverySession(
        _DiscoveryResponse(
            payload={
                "devices": [
                    {
                        "thingId": "controller-1",
                        "productName": "Basic SmartPool 2ª Ger.",
                        "productId": 1000,
                    }
                ]
            }
        )
    )
    monkeypatch.setattr(config_flow, "async_get_clientsession", lambda _hass: session)

    assert await _discover_devices(FakeHass(), "synthetic-id-token") == {}


@pytest.mark.asyncio
async def test_multiple_heat_pumps_require_selection_before_creation(monkeypatch):
    _patch_authenticator(monkeypatch)
    FakeAuthenticator.login_outcome = AuthSuccess(
        tokens=_tokens("first-token"), subject="user-sub"
    )

    async def _discover(_hass, _id_token):
        return {"thing-a": "Pool A", "thing-b": "Pool B"}

    monkeypatch.setattr(config_flow, "_discover_devices", _discover)
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

    flow = _Flow()
    result = await flow.async_step_user(
        {
            "name": "Fairland",
            "cognito_username": "person@example.test",
            "cognito_password": "synthetic-password",
        }
    )

    assert result["type"] == "form"
    assert result["step_id"] == "select_device"
    result = await flow.async_step_select_device({"thing_id": "thing-b"})
    assert result["type"] == "create_entry"
    assert created["unique_id"] == "thing-b"
    assert created["data"]["thing_id"] == "thing-b"


@pytest.mark.asyncio
async def test_duplicate_device_aborts_before_final_cloud_read(monkeypatch):
    _patch_authenticator(monkeypatch)
    FakeAuthenticator.login_outcome = AuthSuccess(
        tokens=_tokens("first-token"), subject="user-sub"
    )

    async def _discover(_hass, _id_token):
        return {"thing-uuid": "Pool heat pump"}

    validate_called = False

    async def _validate(_hass, _data):
        nonlocal validate_called
        validate_called = True

    monkeypatch.setattr(config_flow, "_discover_devices", _discover)
    monkeypatch.setattr(config_flow, "_validate_cloud", _validate)

    class _Flow(TholzFairlandConfigFlow):
        def __init__(self):
            super().__init__()
            self.hass = FakeHass()

        async def async_set_unique_id(self, _unique_id):
            return None

        def _abort_if_unique_id_configured(self):
            reason = "duplicate"
            raise RuntimeError(reason)

    with pytest.raises(RuntimeError, match=r"^duplicate$"):
        await _Flow().async_step_user(
            {
                "name": "Fairland",
                "cognito_username": "person@example.test",
                "cognito_password": "synthetic-password",
            }
        )

    assert validate_called is False


@pytest.mark.asyncio
async def test_setup_with_no_compatible_device_persists_nothing(monkeypatch):
    _patch_authenticator(monkeypatch)
    FakeAuthenticator.login_outcome = AuthSuccess(
        tokens=_tokens("first-token"), subject="user-sub"
    )

    async def _discover(_hass, _id_token):
        return {}

    monkeypatch.setattr(config_flow, "_discover_devices", _discover)

    class _Flow(TholzFairlandConfigFlow):
        def __init__(self):
            super().__init__()
            self.hass = FakeHass()
            self.created = False

        def async_create_entry(self, **_kwargs):
            self.created = True
            return {"type": "create_entry"}

        def async_show_form(self, **kwargs):
            return {"type": "form", **kwargs}

    flow = _Flow()
    result = await flow.async_step_user(
        {
            "name": "Fairland",
            "cognito_username": "person@example.test",
            "cognito_password": "synthetic-password",
        }
    )

    assert result["type"] == "form"
    assert result["errors"] == {"base": "no_devices"}
    assert flow.created is False


def test_setup_form_requires_username_and_password_without_a_mode_selector():
    markers = list(_cloud_schema().schema)
    by_name = {marker.schema: marker.__class__.__name__ for marker in markers}
    assert by_name["cognito_username"] == "Required"
    assert by_name["cognito_password"] == "Required"
    assert "cognito_recovery_mode" not in by_name
