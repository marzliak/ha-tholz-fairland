"""
Sentinel-secret redaction tests for the Cognito recovery path.

Every secret below is a unique synthetic sentinel containing ``SENTINEL``. The
fake Cognito client deliberately echoes all of them back inside its error
payloads (the worst case of a verbose AWS error), and the recovery chain is
driven through every outcome: refresh rejection, transient failure, SRP
rejection, challenges, protocol errors, callback failures and success.

The assertions then prove that no sentinel, and no derived secret (SECRET_HASH
or SRP proof values), reaches a log record (message, arguments or rendered
traceback) or the text of any raised exception, including chained causes.
"""

import base64
import inspect
import json
import logging
import time
import traceback
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from homeassistant.config_entries import ConfigEntry

from custom_components.tholz_fairland import (
    AUTH_MANAGER_KEY,
    DOMAIN,
    _apply_credential_update,
    _make_reauth_callback,
    async_migrate_entry,
)
from custom_components.tholz_fairland.cloud.auth_manager import (
    CredentialUpdate,
    FairlandAuthManager,
    ReauthReason,
    RecoveryMode,
)
from custom_components.tholz_fairland.cloud.client import (
    FairlandCloudClient,
    FairlandCloudError,
)
from custom_components.tholz_fairland.cloud.cognito_auth import (
    AuthSuccess,
    AuthTokens,
    ChallengeRequired,
    ChallengeState,
    CognitoAuthenticator,
    CognitoAuthError,
    secret_hash,
)
from custom_components.tholz_fairland.utils.const import (
    CONF_COGNITO_CLIENT_SECRET_KEY,
    CONF_COGNITO_PASSWORD_KEY,
    CONF_COGNITO_REFRESH_TOKEN_KEY,
    CONF_COGNITO_USER_SUB_KEY,
    CONF_COGNITO_USERNAME_KEY,
    CONF_RECOVERY_MODE_KEY,
    CONF_THING_ID_KEY,
    RECOVERY_MODE_AUTOMATIC,
)

pytestmark = pytest.mark.filterwarnings(
    "ignore:datetime.datetime.utcnow:DeprecationWarning"
)

MARKER = "SENTINEL"
PASSWORD = "SENTINEL_PW_9Z"
NEW_PASSWORD = "SENTINEL_NEWPW_5T"
CLIENT_SECRET = "SENTINEL_SECRET_7Q"
CURRENT_RT = "SENTINEL_RT_CURRENT_4K"
RECOVERY_RT = "SENTINEL_RT_RECOVERY_5M"
SRP_RT = "SENTINEL_RT_SRP_6N"
ACCESS_TOKEN = "SENTINEL_AT_1V"
USERNAME = "SENTINEL_USER_3X@example.invalid"
USER_SUB = "SENTINEL_SUB_2W"
INTERNAL_USERNAME = "SENTINEL_INTERNAL_8J"
SESSION = "SENTINEL_SESSION_8P"
MFA_CODE = "SENTINEL_CODE_0R"
ALL_SENTINELS = (
    PASSWORD,
    NEW_PASSWORD,
    CLIENT_SECRET,
    CURRENT_RT,
    RECOVERY_RT,
    SRP_RT,
    ACCESS_TOKEN,
    USERNAME,
    USER_SUB,
    INTERNAL_USERNAME,
    SESSION,
    MFA_CODE,
)
REGION = "us-east-1"
POOL_ID = "us-east-1_SynthPool"
CLIENT_ID = "synthetic-app-client-id"
TITLE = "Fairland"
MANIFEST = (
    Path(__file__).parents[1] / "custom_components" / "tholz_fairland" / "manifest.json"
)


# -- sentinel-laden synthetic Cognito --------------------------------------------
def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _id_token(*, ttl=3600.0, subject=INTERNAL_USERNAME):
    claims = {"sub": USER_SUB, "cognito:username": subject, "exp": time.time() + ttl}
    header = _b64url(b'{"alg":"none"}')
    # The signature segment carries the marker so any partial leak is caught.
    return f"{header}.{_b64url(json.dumps(claims).encode())}.SENTINEL_SIG_4Q"


def _auth_result(refresh=SRP_RT, *, ttl=3600.0):
    result = {
        "IdToken": _id_token(ttl=ttl),
        "AccessToken": ACCESS_TOKEN,
        "ExpiresIn": 3600,
        "TokenType": "Bearer",
    }
    if refresh:
        result["RefreshToken"] = refresh
    return {"AuthenticationResult": result}


def _hostile_error(code, operation="InitiateAuth"):
    """Build a ClientError whose payload echoes every secret back."""
    message = "echoed request: " + " ".join(ALL_SENTINELS)
    return ClientError({"Error": {"Code": code, "Message": message}}, operation)


def _hostile_transport_error():
    return EndpointConnectionError(
        endpoint_url=f"https://{PASSWORD}.invalid/{CURRENT_RT}"
    )


def _password_verifier():
    return {
        "ChallengeName": "PASSWORD_VERIFIER",
        "ChallengeParameters": {
            "USERNAME": INTERNAL_USERNAME,
            "USER_ID_FOR_SRP": INTERNAL_USERNAME,
            "SALT": "a1b2c3d4e5f60718293a4b5c6d7e8f90",
            "SRP_B": "cd" * 64,
            "SECRET_BLOCK": base64.b64encode(b"SENTINEL_BLOCK_1A").decode(),
        },
    }


def _challenge(name):
    return {
        "ChallengeName": name,
        "Session": SESSION,
        "ChallengeParameters": {"USERNAME": INTERNAL_USERNAME},
    }


class SentinelCognitoClient:
    """Fake ``cognito-idp``: refresh outcomes per token, SRP legs in order."""

    def __init__(self, *, refresh=None, respond=()):
        self.calls = []
        self._refresh = refresh or {}
        self._respond = list(respond)

    def initiate_auth(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["AuthFlow"] == "REFRESH_TOKEN_AUTH":
            token = kwargs["AuthParameters"]["REFRESH_TOKEN"]
            outcome = self._refresh.get(token, "NotAuthorizedException")
            if isinstance(outcome, list):
                outcome = outcome.pop(0) if len(outcome) > 1 else outcome[0]
        else:
            outcome = _password_verifier()
        return _resolve(outcome)

    def respond_to_auth_challenge(self, **kwargs):
        self.calls.append(kwargs)
        return _resolve(self._respond.pop(0))


def _resolve(outcome):
    if isinstance(outcome, str):
        raise _hostile_error(outcome)
    if callable(outcome):
        raise outcome()
    return outcome


def _authenticator(client):
    return CognitoAuthenticator(
        REGION, POOL_ID, CLIENT_ID, client_factory=lambda: client
    )


def _manager(client, **overrides):
    options = {
        "user_sub": USER_SUB,
        "client_secret": CLIENT_SECRET,
        "current_token": CURRENT_RT,
        "recovery_token": RECOVERY_RT,
        "username": USERNAME,
        "password": PASSWORD,
        "mode": RecoveryMode.AUTOMATIC,
        "srp_min_interval": 0.0,
        "transient_backoff": 0.0,
    }
    options.update(overrides)
    manager = FairlandAuthManager(_authenticator(client), **options)
    persisted, reauths = [], []
    manager.set_persist_callback(persisted.append)
    manager.set_reauth_callback(reauths.append)
    return manager, persisted, reauths


def _attempt(call, raised):
    """Run ``call``; keep any typed Cognito failure for later inspection."""
    try:
        return call()
    except CognitoAuthError as exc:
        raised.append(exc)
        return None


# -- inspection helpers ---------------------------------------------------------------
def _exception_text(exc):
    """Everything an operator could see for an exception, chain included."""
    return "\n".join(
        [str(exc), repr(exc), repr(exc.args), "".join(traceback.format_exception(exc))]
    )


def _log_text(caplog):
    """Message, arguments and rendered traceback of every captured record."""
    formatter = logging.Formatter("%(name)s %(levelname)s %(message)s")
    return "\n".join(
        f"{formatter.format(record)}\n{record.msg!r}\n{record.args!r}"
        for record in caplog.records
    )


def _derived_secrets(calls):
    """SECRET_HASH and SRP proof values that crossed the (fake) wire."""
    values = {
        secret_hash(name, CLIENT_ID, CLIENT_SECRET)
        for name in (USER_SUB, USERNAME, INTERNAL_USERNAME)
    }
    for call in calls:
        for mapping in (call.get("AuthParameters"), call.get("ChallengeResponses")):
            for key in (
                "SECRET_HASH",
                "SRP_A",
                "PASSWORD_CLAIM_SIGNATURE",
                "PASSWORD_CLAIM_SECRET_BLOCK",
            ):
                if mapping and mapping.get(key):
                    values.add(mapping[key])
    return values


def _assert_clean(text, derived=()):
    assert MARKER not in text
    for value in derived:
        assert value not in text


def _assert_nothing_leaked(caplog, raised, client=None):
    derived = _derived_secrets(client.calls) if client is not None else ()
    _assert_clean(_log_text(caplog), derived)
    for exc in raised:
        _assert_clean(_exception_text(exc), derived)


def _raise_sanitized_from(cause):
    message = "sanitized"
    raise CognitoAuthError(message) from cause


def test_the_leak_detector_catches_planted_leaks(caplog):
    """Canary: the scanners above must fail on a real leak, not pass vacuously."""
    caplog.set_level(logging.DEBUG)
    logging.getLogger("synthetic.canary").debug("token %s", CURRENT_RT)
    with pytest.raises(CognitoAuthError) as caught:
        _raise_sanitized_from(KeyError(CURRENT_RT))

    with pytest.raises(AssertionError):
        _assert_clean(_log_text(caplog))
    # The sanitized message alone looks clean; the chained cause does not.
    assert MARKER not in str(caught.value)
    with pytest.raises(AssertionError):
        _assert_clean(_exception_text(caught.value))
    with pytest.raises(AssertionError):
        _assert_clean("opaque", derived={"opaque"})


# -- manager + adapter: every outcome ------------------------------------------------
def test_refresh_rejections_and_srp_rejection_leak_nothing(caplog):
    caplog.set_level(logging.DEBUG)
    client = SentinelCognitoClient(respond=["NotAuthorizedException"])
    manager, persisted, reauths = _manager(client)
    raised = []

    for _ in range(3):
        _attempt(manager.get_valid_id_token, raised)

    assert len(raised) == 3
    assert reauths == [ReauthReason.CREDENTIAL_REJECTED]
    assert persisted == []
    assert caplog.records  # the scenario really did log
    _assert_nothing_leaked(caplog, raised, client)


def test_srp_recovery_and_later_rotation_leak_nothing(caplog):
    caplog.set_level(logging.DEBUG)
    client = SentinelCognitoClient(
        refresh={
            SRP_RT: [
                _auth_result(refresh=None, ttl=60),
                _auth_result(refresh="SENTINEL_RT_ROTATED_2B"),
            ]
        },
        respond=[_auth_result(ttl=60)],
    )
    manager, persisted, _ = _manager(client)
    raised = []

    _attempt(manager.get_valid_id_token, raised)
    _attempt(manager.get_valid_id_token, raised)

    assert raised == []
    assert [update.promote_recovery for update in persisted] == [True, False]
    assert caplog.records  # the scenario really did log
    _assert_nothing_leaked(caplog, raised, client)


@pytest.mark.parametrize(
    ("refresh", "respond"),
    [
        pytest.param({CURRENT_RT: _hostile_transport_error}, [], id="current-network"),
        pytest.param(
            {CURRENT_RT: "TooManyRequestsException"}, [], id="current-throttled"
        ),
        pytest.param({RECOVERY_RT: "InternalErrorException"}, [], id="recovery-outage"),
        pytest.param({}, [_hostile_transport_error], id="srp-network"),
        pytest.param({}, ["SomeBrandNewCognitoException"], id="srp-unknown-code"),
    ],
)
def test_transient_failures_leak_nothing(caplog, refresh, respond):
    caplog.set_level(logging.DEBUG)
    client = SentinelCognitoClient(refresh=refresh, respond=respond)
    manager, _, reauths = _manager(client)
    raised = []

    _attempt(manager.get_valid_id_token, raised)

    assert len(raised) == 1
    assert reauths == []
    _assert_nothing_leaked(caplog, raised, client)


@pytest.mark.parametrize(
    "respond",
    [
        pytest.param(_challenge("SOFTWARE_TOKEN_MFA"), id="supported"),
        pytest.param(_challenge("MFA_SETUP"), id="unsupported"),
        pytest.param("InvalidParameterException", id="configuration"),
        pytest.param({"unexpected": SESSION}, id="protocol"),
    ],
)
def test_srp_challenge_configuration_and_protocol_outcomes_leak_nothing(
    caplog, respond
):
    caplog.set_level(logging.DEBUG)
    client = SentinelCognitoClient(respond=[respond])
    manager, _, reauths = _manager(client)
    raised = []

    _attempt(manager.get_valid_id_token, raised)
    _attempt(manager.get_valid_id_token, raised)

    assert len(raised) == 2
    assert len(reauths) == 1
    assert caplog.records  # the scenario really did log
    _assert_nothing_leaked(caplog, raised, client)


def test_malformed_id_token_protocol_error_leaks_nothing(caplog):
    caplog.set_level(logging.DEBUG)
    malformed = [
        {"AuthenticationResult": {"IdToken": "SENTINEL_IDTOKEN_NO_SEGMENTS"}},
        {"AuthenticationResult": {"IdToken": f"h.{_b64url(PASSWORD.encode())}.s"}},
        {"AuthenticationResult": {"IdToken": f"h.{CLIENT_SECRET}.s"}},
    ]
    raised = []
    for response in malformed:
        client = SentinelCognitoClient(refresh={CURRENT_RT: response})
        manager, _, reauths = _manager(client)
        _attempt(manager.get_valid_id_token, raised)
        assert reauths == [ReauthReason.PROTOCOL]

    assert len(raised) == len(malformed)
    _assert_nothing_leaked(caplog, raised)


def test_callback_failures_are_logged_without_their_payload(caplog):
    caplog.set_level(logging.DEBUG)
    client = SentinelCognitoClient(respond=["NotAuthorizedException"])
    manager, _, _ = _manager(client)

    def failing_callback(_value):
        raise RuntimeError(" ".join(ALL_SENTINELS))

    manager.set_persist_callback(failing_callback)
    manager.set_reauth_callback(failing_callback)
    raised = []
    _attempt(manager.get_valid_id_token, raised)

    rotating = SentinelCognitoClient(
        refresh={CURRENT_RT: _auth_result(refresh="SENTINEL_RT_ROTATED_2B")}
    )
    rotation_manager, _, _ = _manager(rotating)
    rotation_manager.set_persist_callback(failing_callback)
    _attempt(rotation_manager.get_valid_id_token, raised)

    assert "reauth callback failed" in caplog.text
    assert "persistence callback failed" in caplog.text
    _assert_nothing_leaked(caplog, raised, client)


# -- adapter: wire and state hygiene --------------------------------------------------
def test_srp_never_puts_the_password_on_the_wire():
    client = SentinelCognitoClient(respond=[_challenge("NEW_PASSWORD_REQUIRED")])
    authenticator = _authenticator(client)

    challenge = authenticator.login_srp(USERNAME, PASSWORD, CLIENT_SECRET)
    client._respond.append(_auth_result())
    authenticator.respond_to_challenge(challenge.state, NEW_PASSWORD, CLIENT_SECRET)

    wire = repr(client.calls)
    assert PASSWORD not in wire
    # The new password is answered against the session; SRP is not re-run.
    assert NEW_PASSWORD in wire
    assert sum(call.get("AuthFlow") == "USER_SRP_AUTH" for call in client.calls) == 1


def test_secret_hash_and_srp_proof_values_are_never_logged(caplog):
    caplog.set_level(logging.DEBUG)
    client = SentinelCognitoClient(
        refresh={CURRENT_RT: _auth_result(refresh=None)},
        respond=[
            _challenge("SMS_MFA"),
            "CodeMismatchException",
            _auth_result(),
        ],
    )
    authenticator = _authenticator(client)
    raised = []

    authenticator.refresh(CURRENT_RT, USER_SUB, CLIENT_SECRET)
    challenge = authenticator.login_srp(USERNAME, PASSWORD, CLIENT_SECRET)
    _attempt(
        lambda: authenticator.respond_to_challenge(
            challenge.state, MFA_CODE, CLIENT_SECRET
        ),
        raised,
    )
    authenticator.respond_to_challenge(challenge.state, MFA_CODE, CLIENT_SECRET)

    derived = _derived_secrets(client.calls)
    # Hashes for the sub, login name and internal name plus the SRP proof.
    assert len(derived) >= 5
    assert len(raised) == 1
    _assert_nothing_leaked(caplog, raised, client)


def test_authenticator_holds_no_credential_state():
    client = SentinelCognitoClient(
        refresh={CURRENT_RT: _auth_result(refresh=None)}, respond=[_auth_result()]
    )
    authenticator = _authenticator(client)

    authenticator.refresh(CURRENT_RT, USER_SUB, CLIENT_SECRET)
    authenticator.login_srp(USERNAME, PASSWORD, CLIENT_SECRET)

    _assert_clean(repr(vars(authenticator)))


def test_credential_dataclasses_hide_secret_fields_in_repr():
    tokens = AuthTokens(
        id_token=_id_token(),
        access_token=ACCESS_TOKEN,
        refresh_token=SRP_RT,
        expires_at=1.0,
    )
    state = ChallengeState(
        challenge_name="SMS_MFA", username=INTERNAL_USERNAME, session=SESSION
    )
    values = [
        tokens,
        AuthSuccess(tokens=tokens, subject=INTERNAL_USERNAME),
        state,
        ChallengeRequired(state),
        CredentialUpdate(
            current_token=SRP_RT,
            generation=1,
            promote_recovery=True,
            recovery_token=SRP_RT,
            user_sub=USER_SUB,
            expected_current=CURRENT_RT,
        ),
    ]

    for value in values:
        _assert_clean(repr(value))
        _assert_clean(str(value))


# -- integration wiring ---------------------------------------------------------------
@pytest.mark.parametrize(
    "failure",
    [
        pytest.param({CURRENT_RT: "NotAuthorizedException"}, id="auth-error"),
        pytest.param({CURRENT_RT: _hostile_transport_error}, id="retry-later"),
        pytest.param(
            {CURRENT_RT: {"AuthenticationResult": {"IdToken": "SENTINEL_BAD"}}},
            id="protocol",
        ),
    ],
)
def test_cloud_client_errors_carry_no_secret(failure):
    client = SentinelCognitoClient(refresh=failure)
    manager, _, _ = _manager(client, mode=RecoveryMode.INTERACTIVE)
    cloud = FairlandCloudClient(thing_id="synthetic-thing-id", auth_manager=manager)

    with pytest.raises(FairlandCloudError) as caught:
        cloud._id_token()

    _assert_clean(_exception_text(caught.value))


# The ConfigEntry constructor grows required keywords across Home Assistant
# releases (e.g. ``subentries_data``); pass only the ones this release accepts.
_ENTRY_PARAMETERS = frozenset(inspect.signature(ConfigEntry).parameters)


def _sentinel_entry(minor_version=1, version=1):
    candidates = {
        "data": {
            CONF_THING_ID_KEY: "synthetic-thing-id",
            CONF_COGNITO_USER_SUB_KEY: USER_SUB,
            CONF_COGNITO_CLIENT_SECRET_KEY: CLIENT_SECRET,
            CONF_COGNITO_REFRESH_TOKEN_KEY: CURRENT_RT,
            CONF_COGNITO_USERNAME_KEY: USERNAME,
            CONF_RECOVERY_MODE_KEY: RECOVERY_MODE_AUTOMATIC,
            CONF_COGNITO_PASSWORD_KEY: PASSWORD,
        },
        "discovery_keys": MappingProxyType({}),
        "domain": DOMAIN,
        "entry_id": "synthetic-entry-id",
        "minor_version": minor_version,
        "options": {CONF_COGNITO_REFRESH_TOKEN_KEY: RECOVERY_RT},
        "source": "user",
        "subentries_data": (),
        "title": TITLE,
        "unique_id": "synthetic-thing-id",
        "version": version,
    }
    return ConfigEntry(
        **{key: value for key, value in candidates.items() if key in _ENTRY_PARAMETERS}
    )


class RecordingConfigEntries:
    def __init__(self, entry=None):
        self._entry = entry
        self.updates = []

    def async_get_entry(self, _entry_id):
        return self._entry

    def async_update_entry(self, entry, **changes):
        self.updates.append(changes)
        for key, value in changes.items():
            stored = (
                MappingProxyType(dict(value)) if key in {"data", "options"} else value
            )
            object.__setattr__(entry, key, stored)
        return True


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [1, 2])
async def test_migration_logs_no_credential_values(caplog, version):
    caplog.set_level(logging.DEBUG)
    entry = _sentinel_entry(version=version)
    hass = SimpleNamespace(config_entries=RecordingConfigEntries(entry))

    await async_migrate_entry(hass, entry)

    assert caplog.records
    _assert_clean(_log_text(caplog))


def test_persistence_and_reauth_wiring_log_no_credential_values(caplog):
    caplog.set_level(logging.DEBUG)
    entry = _sentinel_entry(minor_version=2)
    manager = object()
    hass = SimpleNamespace(
        data={DOMAIN: {entry.entry_id: {AUTH_MANAGER_KEY: manager}}},
        config_entries=RecordingConfigEntries(entry),
        loop=SimpleNamespace(call_soon_threadsafe=lambda *_args: None),
    )
    landed = CredentialUpdate(
        current_token=SRP_RT,
        generation=1,
        promote_recovery=True,
        recovery_token=SRP_RT,
        user_sub=INTERNAL_USERNAME,
        expected_current=CURRENT_RT,
    )
    stale = CredentialUpdate(
        current_token="SENTINEL_RT_LATE_7C", generation=1, expected_current=CURRENT_RT
    )

    _apply_credential_update(hass, entry, manager, landed)
    _apply_credential_update(hass, entry, manager, stale)
    _make_reauth_callback(hass, entry, manager)(ReauthReason.CREDENTIAL_REJECTED)

    assert len(hass.config_entries.updates) == 1
    assert caplog.records
    _assert_clean(_log_text(caplog))


def test_manifest_never_enables_third_party_debug_loggers():
    """Botocore logs raw request parameters (tokens, SECRET_HASH) at DEBUG."""
    manifest = json.loads(MANIFEST.read_text())
    loggers = {name.split(".")[0] for name in manifest.get("loggers", [])}

    assert not loggers & {"boto3", "botocore", "pycognito", "urllib3", "requests"}
