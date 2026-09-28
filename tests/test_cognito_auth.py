"""
Unit tests for the Cognito refresh/SRP adapter (``cloud/cognito_auth.py``).

Every Cognito interaction goes through a scripted fake ``cognito-idp`` client,
or a real botocore client wrapped in ``Stubber`` and pointed at a closed
localhost port, so nothing can reach AWS. Credentials are synthetic and tokens
are JWT-shaped strings whose claims are plain JSON.

The SRP legs run pycognito's real proof math against the fake, so the exact
wire shape (USERNAME, SRP_A, SECRET_HASH, PASSWORD_VERIFIER answers) is
verified rather than assumed.
"""

import base64
import hashlib
import hmac
import json
import socket
import ssl
import time

import boto3
import pytest
from botocore import UNSIGNED
from botocore.config import Config
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ParamValidationError,
    ReadTimeoutError,
)
from botocore.stub import ANY, Stubber
from pycognito.aws_srp import AWSSRP

from custom_components.tholz_fairland.cloud.cognito_auth import (
    CHALLENGE_NEW_PASSWORD_REQUIRED,
    SUPPORTED_CHALLENGES,
    AuthSuccess,
    AuthTokens,
    ChallengeRequired,
    ChallengeState,
    CognitoAuthenticator,
    CognitoAuthError,
    CognitoChallengeRequired,
    CognitoChallengeResponseRejected,
    CognitoConfigurationError,
    CognitoCredentialRejected,
    CognitoProtocolError,
    CognitoTransientError,
    secret_hash,
)

# pycognito 2024.5.1 still calls datetime.utcnow() while signing the
# PASSWORD_VERIFIER answer; that deprecation is not ours to fix.
pytestmark = pytest.mark.filterwarnings(
    "ignore:datetime.datetime.utcnow:DeprecationWarning"
)

REGION = "us-east-1"
POOL_ID = "us-east-1_SynthPool"
CLIENT_ID = "synthetic-app-client-id"
CLIENT_SECRET = "synthetic-app-client-secret"
USER_SUB = "00000000-0000-4000-8000-00000000cafe"
USERNAME = "owner@example.invalid"
PASSWORD = "synthetic-password"
# The internal username Cognito echoes back when sign-in used an alias.
INTERNAL_USERNAME = "synthetic-internal-username"
SESSION = "synthetic-challenge-session-0001"
ENDPOINT = "https://cognito-idp.us-east-1.amazonaws.invalid"
MAX_CHALLENGE_CHAIN = 3


# -- synthetic material --------------------------------------------------------
def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _jwt(claims) -> str:
    header = _b64url(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    return f"{header}.{_b64url(json.dumps(claims).encode())}.synthetic-signature"


def _id_token(*, ttl=3600.0, **claims) -> str:
    body = {"sub": USER_SUB, "exp": int(time.time() + ttl), "token_use": "id"}
    body.update(claims)
    return _jwt(body)


def _auth_result(*, refresh="synthetic-refresh-token", id_token=None):
    result = {
        "IdToken": id_token if id_token is not None else _id_token(),
        "AccessToken": "synthetic-access-token",
        "ExpiresIn": 3600,
        "TokenType": "Bearer",
    }
    if refresh is not None:
        result["RefreshToken"] = refresh
    return {"AuthenticationResult": result}


def _password_verifier():
    """Return the first-leg SRP challenge Cognito sends for USER_SRP_AUTH."""
    return {
        "ChallengeName": "PASSWORD_VERIFIER",
        "ChallengeParameters": {
            "USERNAME": INTERNAL_USERNAME,
            "USER_ID_FOR_SRP": INTERNAL_USERNAME,
            "SALT": "a1b2c3d4e5f60718293a4b5c6d7e8f90",
            "SRP_B": "ab" * 64,
            "SECRET_BLOCK": base64.b64encode(b"synthetic-secret-block").decode(),
        },
    }


def _challenge(name, *, session=SESSION, internal=INTERNAL_USERNAME):
    parameters = {"USERNAME": internal} if internal else {}
    response = {"ChallengeName": name, "ChallengeParameters": parameters}
    if session is not None:
        response["Session"] = session
    return response


def _client_error(code, operation="InitiateAuth"):
    return ClientError(
        {"Error": {"Code": code, "Message": "synthetic failure"}}, operation
    )


class FakeCognitoClient:
    """Scripted ``cognito-idp`` stand-in that records every request."""

    def __init__(self, *, initiate=(), respond=()):
        self.initiate_calls = []
        self.respond_calls = []
        self._initiate = list(initiate)
        self._respond = list(respond)

    def initiate_auth(self, **kwargs):
        self.initiate_calls.append(kwargs)
        return _next_outcome(self._initiate)

    def respond_to_auth_challenge(self, **kwargs):
        self.respond_calls.append(kwargs)
        return _next_outcome(self._respond)


def _next_outcome(script):
    outcome = script.pop(0)
    if isinstance(outcome, BaseException):
        raise outcome
    return outcome


def _authenticator(client):
    return CognitoAuthenticator(
        REGION, POOL_ID, CLIENT_ID, client_factory=lambda: client
    )


def _counting_authenticator():
    """Return an authenticator whose client factory only records being called."""
    calls = []
    authenticator = CognitoAuthenticator(
        REGION, POOL_ID, CLIENT_ID, client_factory=lambda: calls.append(1)
    )
    return authenticator, calls


def _srp_login(*respond, initiate=None, client_secret=CLIENT_SECRET):
    """Run a full SRP login against the fake and return (client, outcome)."""
    client = FakeCognitoClient(
        initiate=[initiate or _password_verifier()], respond=list(respond)
    )
    return client, _authenticator(client).login_srp(USERNAME, PASSWORD, client_secret)


def _state(name="SMS_MFA", *, session=SESSION, depth=0):
    return ChallengeState(
        challenge_name=name, username=INTERNAL_USERNAME, session=session, depth=depth
    )


def _local_botocore_client():
    """Real botocore client aimed at a closed local port (never AWS)."""
    return boto3.session.Session().client(
        "cognito-idp",
        region_name=REGION,
        endpoint_url="http://127.0.0.1:9",
        config=Config(signature_version=UNSIGNED, retries={"total_max_attempts": 1}),
    )


# Fresh instances per test: re-raising one shared exception object would chain
# tracebacks across tests.
TRANSPORT_FAILURES = [
    pytest.param(lambda: EndpointConnectionError(endpoint_url=ENDPOINT), id="endpoint"),
    pytest.param(
        lambda: ConnectTimeoutError(endpoint_url=ENDPOINT), id="connect-timeout"
    ),
    pytest.param(lambda: ReadTimeoutError(endpoint_url=ENDPOINT), id="read-timeout"),
    pytest.param(lambda: ConnectionClosedError(endpoint_url=ENDPOINT), id="closed"),
    pytest.param(TimeoutError, id="timeout"),
    pytest.param(ConnectionResetError, id="reset"),
    pytest.param(lambda: socket.gaierror(-2, "synthetic dns failure"), id="dns"),
    pytest.param(lambda: ssl.SSLError(1, "synthetic tls failure"), id="tls"),
    pytest.param(BotoCoreError, id="botocore"),
]

THROTTLE_AND_OUTAGE_CODES = [
    "TooManyRequestsException",
    "ThrottlingException",
    "ThrottledException",
    "LimitExceededException",
    "InternalErrorException",
    "ServiceUnavailableException",
    "RequestTimeoutException",
]

CONFIGURATION_CODES = [
    "InvalidParameterException",
    "InvalidLambdaResponseException",
    "UnexpectedLambdaException",
    "OperationNotEnabledException",
]


# -- SECRET_HASH ---------------------------------------------------------------
def test_secret_hash_matches_the_cognito_reference_formula():
    """SECRET_HASH is Base64(HMAC-SHA256(client_secret, name + client_id))."""
    digest = hmac.new(
        CLIENT_SECRET.encode(), f"{USER_SUB}{CLIENT_ID}".encode(), hashlib.sha256
    ).digest()
    expected = base64.b64encode(digest).decode()

    assert secret_hash(USER_SUB, CLIENT_ID, CLIENT_SECRET) == expected
    # pycognito computes the same value for the SRP legs it signs itself.
    assert AWSSRP.get_secret_hash(USER_SUB, CLIENT_ID, CLIENT_SECRET) == expected


@pytest.mark.parametrize(
    ("name", "client_id", "client_secret"),
    [
        (USERNAME, CLIENT_ID, CLIENT_SECRET),
        (USER_SUB, "another-client-id", CLIENT_SECRET),
        (USER_SUB, CLIENT_ID, "another-client-secret"),
    ],
)
def test_secret_hash_changes_with_every_input(name, client_id, client_secret):
    baseline = secret_hash(USER_SUB, CLIENT_ID, CLIENT_SECRET)
    assert secret_hash(name, client_id, client_secret) != baseline


# -- AuthTokens freshness --------------------------------------------------------
@pytest.mark.parametrize(
    ("ttl", "fresh"),
    [(3600, True), (600, True), (60, False), (0, False), (-30, False)],
)
def test_tokens_are_fresh_only_outside_the_expiry_skew(ttl, fresh):
    tokens = AuthTokens(
        id_token="id",
        access_token=None,
        refresh_token=None,
        expires_at=time.time() + ttl,
    )
    assert tokens.is_fresh is fresh


# -- production client configuration ------------------------------------------------
def test_default_client_is_unsigned_single_attempt_and_reused():
    """A single attempt avoids re-sending a one-time SRP proof on retry."""
    authenticator = CognitoAuthenticator(REGION, POOL_ID, CLIENT_ID)

    client = authenticator._idp()

    assert authenticator._idp() is client
    assert client.meta.region_name == REGION
    assert client.meta.config.signature_version is UNSIGNED
    assert client.meta.config.retries["total_max_attempts"] == 1
    assert client.meta.config.connect_timeout <= 10
    assert client.meta.config.read_timeout <= 30


# -- REFRESH_TOKEN_AUTH ----------------------------------------------------------
def test_refresh_sends_refresh_token_auth_hashed_with_the_subject():
    client = FakeCognitoClient(initiate=[_auth_result(refresh=None)])

    _authenticator(client).refresh("synthetic-current-rt", USER_SUB, CLIENT_SECRET)

    (call,) = client.initiate_calls
    assert call["ClientId"] == CLIENT_ID
    assert call["AuthFlow"] == "REFRESH_TOKEN_AUTH"
    assert call["AuthParameters"] == {
        "REFRESH_TOKEN": "synthetic-current-rt",
        "SECRET_HASH": secret_hash(USER_SUB, CLIENT_ID, CLIENT_SECRET),
    }
    # The refresh grant must never be hashed against the human login name.
    assert call["AuthParameters"]["SECRET_HASH"] != secret_hash(
        USERNAME, CLIENT_ID, CLIENT_SECRET
    )


def test_refresh_returns_validated_tokens_with_the_decoded_expiry():
    expiry = int(time.time()) + 3600
    id_token = _jwt({"sub": USER_SUB, "exp": expiry})
    client = FakeCognitoClient(
        initiate=[
            {
                "AuthenticationResult": {
                    "IdToken": id_token,
                    "AccessToken": "synthetic-access-token",
                }
            }
        ]
    )

    tokens = _authenticator(client).refresh("rt", USER_SUB, CLIENT_SECRET)

    assert tokens.id_token == id_token
    assert tokens.access_token == "synthetic-access-token"
    assert tokens.expires_at == float(expiry)
    assert tokens.refresh_token is None
    assert tokens.is_fresh


def test_refresh_surfaces_a_rotated_refresh_token():
    client = FakeCognitoClient(initiate=[_auth_result(refresh="synthetic-rotated-rt")])

    tokens = _authenticator(client).refresh("rt", USER_SUB, CLIENT_SECRET)

    assert tokens.refresh_token == "synthetic-rotated-rt"


@pytest.mark.parametrize("rotated", ["", None, 12345])
def test_refresh_ignores_an_empty_or_non_string_rotated_token(rotated):
    response = _auth_result(refresh=None)
    response["AuthenticationResult"]["RefreshToken"] = rotated
    client = FakeCognitoClient(initiate=[response])

    tokens = _authenticator(client).refresh("rt", USER_SUB, CLIENT_SECRET)

    assert tokens.refresh_token is None


@pytest.mark.parametrize("missing", ["", None])
def test_an_empty_refresh_token_is_rejected_without_calling_cognito(missing):
    authenticator, factory_calls = _counting_authenticator()

    with pytest.raises(CognitoCredentialRejected):
        authenticator.refresh(missing, USER_SUB, CLIENT_SECRET)
    assert factory_calls == []


@pytest.mark.parametrize("subject", ["", None])
def test_refresh_without_subject_is_a_configuration_error(subject):
    authenticator, factory_calls = _counting_authenticator()

    with pytest.raises(CognitoConfigurationError):
        authenticator.refresh("rt", subject, None)
    assert factory_calls == []


@pytest.mark.parametrize("client_secret", ["", None])
def test_refresh_public_client_omits_secret_hash(client_secret):
    client = FakeCognitoClient(initiate=[_auth_result(refresh=None)])

    _authenticator(client).refresh("rt", USER_SUB, client_secret)

    (request,) = client.initiate_calls
    assert request["AuthParameters"] == {"REFRESH_TOKEN": "rt"}


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("NotAuthorizedException", CognitoCredentialRejected),
        ("ExpiredTokenException", CognitoCredentialRejected),
        ("InvalidTokenException", CognitoCredentialRejected),
        ("ResourceNotFoundException", CognitoCredentialRejected),
        ("UserNotFoundException", CognitoCredentialRejected),
        *[(code, CognitoTransientError) for code in THROTTLE_AND_OUTAGE_CODES],
        *[(code, CognitoConfigurationError) for code in CONFIGURATION_CODES],
        # A novel code is a possible new throttle/outage, never a rejection.
        ("SomeBrandNewCognitoException", CognitoTransientError),
        ("", CognitoTransientError),
    ],
)
def test_refresh_client_errors_are_classified(code, expected):
    client = FakeCognitoClient(initiate=[_client_error(code)])

    with pytest.raises(CognitoAuthError) as caught:
        _authenticator(client).refresh("rt", USER_SUB, CLIENT_SECRET)

    assert type(caught.value) is expected
    # The raw botocore error (and its payload) never travels with the typed one.
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


def test_refresh_client_error_without_an_error_body_is_transient():
    client = FakeCognitoClient(initiate=[ClientError({}, "InitiateAuth")])

    with pytest.raises(CognitoTransientError):
        _authenticator(client).refresh("rt", USER_SUB, CLIENT_SECRET)


@pytest.mark.parametrize("make_error", TRANSPORT_FAILURES)
def test_refresh_transport_failures_are_transient(make_error):
    client = FakeCognitoClient(initiate=[make_error()])

    with pytest.raises(CognitoTransientError) as caught:
        _authenticator(client).refresh("rt", USER_SUB, CLIENT_SECRET)
    assert caught.value.__cause__ is None


def test_refresh_request_rejected_by_botocore_validation_is_a_configuration_error():
    client = FakeCognitoClient(initiate=[ParamValidationError(report="synthetic")])

    with pytest.raises(CognitoConfigurationError):
        _authenticator(client).refresh("rt", USER_SUB, CLIENT_SECRET)


def test_refresh_unexpected_failure_is_a_protocol_error_never_a_rejection():
    client = FakeCognitoClient(initiate=[KeyError("synthetic")])

    with pytest.raises(CognitoProtocolError):
        _authenticator(client).refresh("rt", USER_SUB, CLIENT_SECRET)


@pytest.mark.parametrize(
    "response",
    [
        pytest.param({}, id="no-authentication-result"),
        pytest.param({"AuthenticationResult": None}, id="null-result"),
        pytest.param({"AuthenticationResult": {}}, id="no-id-token"),
        pytest.param({"AuthenticationResult": {"IdToken": ""}}, id="empty-id-token"),
        pytest.param({"AuthenticationResult": {"IdToken": 42}}, id="non-string"),
        pytest.param({"AuthenticationResult": {"IdToken": "opaque"}}, id="no-segments"),
        pytest.param(
            {"AuthenticationResult": {"IdToken": "a.@@@.c"}}, id="bad-base64-claims"
        ),
        pytest.param(
            {"AuthenticationResult": {"IdToken": f"a.{_b64url(b'not json')}.c"}},
            id="non-json-claims",
        ),
        pytest.param(
            {"AuthenticationResult": {"IdToken": _jwt(["exp", 1])}},
            id="claims-not-an-object",
        ),
        pytest.param(
            {"AuthenticationResult": {"IdToken": _jwt({"sub": USER_SUB})}},
            id="missing-exp",
        ),
        pytest.param(
            {"AuthenticationResult": {"IdToken": _jwt({"exp": "soon"})}},
            id="string-exp",
        ),
        pytest.param(
            {"AuthenticationResult": {"IdToken": _jwt({"exp": True})}},
            id="boolean-exp",
        ),
    ],
)
def test_refresh_malformed_responses_are_protocol_errors(response):
    client = FakeCognitoClient(initiate=[response])

    with pytest.raises(CognitoProtocolError):
        _authenticator(client).refresh("rt", USER_SUB, CLIENT_SECRET)


# -- USER_SRP_AUTH ---------------------------------------------------------------
def test_srp_login_uses_the_login_name_then_the_internal_username():
    client, outcome = _srp_login(_auth_result(refresh="synthetic-srp-rt"))

    assert isinstance(outcome, AuthSuccess)
    assert outcome.tokens.refresh_token == "synthetic-srp-rt"
    (start,) = client.initiate_calls
    assert start["AuthFlow"] == "USER_SRP_AUTH"
    assert start["ClientId"] == CLIENT_ID
    auth_parameters = start["AuthParameters"]
    assert auth_parameters["USERNAME"] == USERNAME
    assert auth_parameters["SECRET_HASH"] == secret_hash(
        USERNAME, CLIENT_ID, CLIENT_SECRET
    )
    assert int(auth_parameters["SRP_A"], 16) > 0
    (verifier,) = client.respond_calls
    assert verifier["ClientId"] == CLIENT_ID
    assert verifier["ChallengeName"] == "PASSWORD_VERIFIER"
    answers = verifier["ChallengeResponses"]
    assert answers["USERNAME"] == INTERNAL_USERNAME
    assert answers["SECRET_HASH"] == secret_hash(
        INTERNAL_USERNAME, CLIENT_ID, CLIENT_SECRET
    )
    assert answers["PASSWORD_CLAIM_SIGNATURE"]
    assert answers["PASSWORD_CLAIM_SECRET_BLOCK"]


@pytest.mark.parametrize("client_secret", ["", None])
def test_srp_login_public_client_omits_secret_hash(client_secret):
    client, outcome = _srp_login(
        _auth_result(refresh="synthetic-srp-rt"), client_secret=client_secret
    )

    assert isinstance(outcome, AuthSuccess)
    (start,) = client.initiate_calls
    assert "SECRET_HASH" not in start["AuthParameters"]
    (verifier,) = client.respond_calls
    assert "SECRET_HASH" not in verifier["ChallengeResponses"]


@pytest.mark.parametrize(
    ("claims", "subject"),
    [
        ({"cognito:username": INTERNAL_USERNAME}, INTERNAL_USERNAME),
        ({}, USER_SUB),
    ],
)
def test_srp_success_reports_the_refresh_subject(claims, subject):
    _, outcome = _srp_login(_auth_result(id_token=_id_token(**claims)))

    assert isinstance(outcome, AuthSuccess)
    assert outcome.subject == subject
    assert outcome.subject != USERNAME


@pytest.mark.parametrize(
    ("username", "client_secret", "password", "expected"),
    [
        ("", CLIENT_SECRET, PASSWORD, CognitoConfigurationError),
        (None, CLIENT_SECRET, PASSWORD, CognitoConfigurationError),
        (USERNAME, CLIENT_SECRET, "", CognitoCredentialRejected),
        (USERNAME, CLIENT_SECRET, None, CognitoCredentialRejected),
    ],
)
def test_srp_login_prerequisites_fail_without_calling_cognito(
    username, client_secret, password, expected
):
    authenticator, factory_calls = _counting_authenticator()

    with pytest.raises(CognitoAuthError) as caught:
        authenticator.login_srp(username, password, client_secret)

    assert type(caught.value) is expected
    assert factory_calls == []


@pytest.mark.parametrize(
    "challenge",
    [
        "SOFTWARE_TOKEN_MFA",
        "SMS_MFA",
        "EMAIL_OTP",
        "SMS_OTP",
        CHALLENGE_NEW_PASSWORD_REQUIRED,
    ],
)
def test_supported_challenge_after_the_password_step_is_surfaced(challenge):
    assert challenge in SUPPORTED_CHALLENGES

    _, outcome = _srp_login(_challenge(challenge))

    assert isinstance(outcome, ChallengeRequired)
    assert outcome.state == ChallengeState(
        challenge_name=challenge, username=INTERNAL_USERNAME, session=SESSION, depth=0
    )


def test_challenge_without_an_internal_username_falls_back_to_the_login_name():
    _, outcome = _srp_login(_challenge("SOFTWARE_TOKEN_MFA", internal=None))

    assert isinstance(outcome, ChallengeRequired)
    assert outcome.state.username == USERNAME


@pytest.mark.parametrize(
    "response",
    [
        pytest.param(_challenge("MFA_SETUP"), id="mfa-setup"),
        pytest.param(_challenge("SELECT_MFA_TYPE"), id="select-mfa-type"),
        pytest.param(_challenge("CUSTOM_CHALLENGE"), id="custom"),
        pytest.param(_challenge("DEVICE_SRP_AUTH"), id="device"),
        pytest.param(_challenge("SOFTWARE_TOKEN_MFA", session=None), id="no-session"),
    ],
)
def test_unusable_challenge_after_the_password_step_is_unsupported(response):
    with pytest.raises(CognitoChallengeRequired) as caught:
        _srp_login(response)

    assert caught.value.challenge_name == response["ChallengeName"]
    assert caught.value.supported is False


@pytest.mark.parametrize(
    ("initiated", "label"),
    [
        (_challenge("CUSTOM_CHALLENGE"), "CUSTOM_CHALLENGE"),
        ({"ChallengeParameters": {}}, "UNKNOWN"),
    ],
)
def test_srp_start_without_a_password_verifier_is_unsupported(initiated, label):
    client = FakeCognitoClient(initiate=[initiated])

    with pytest.raises(CognitoChallengeRequired) as caught:
        _authenticator(client).login_srp(USERNAME, PASSWORD, CLIENT_SECRET)

    assert caught.value.challenge_name == label
    assert caught.value.supported is False
    assert client.respond_calls == []


def test_password_verifier_without_parameters_is_a_protocol_error():
    client = FakeCognitoClient(initiate=[{"ChallengeName": "PASSWORD_VERIFIER"}])

    with pytest.raises(CognitoProtocolError):
        _authenticator(client).login_srp(USERNAME, PASSWORD, CLIENT_SECRET)
    assert client.respond_calls == []


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("NotAuthorizedException", CognitoCredentialRejected),
        ("UserNotFoundException", CognitoCredentialRejected),
        ("UserNotConfirmedException", CognitoCredentialRejected),
        ("PasswordResetRequiredException", CognitoCredentialRejected),
        ("InvalidPasswordException", CognitoCredentialRejected),
        *[(code, CognitoTransientError) for code in THROTTLE_AND_OUTAGE_CODES],
        *[(code, CognitoConfigurationError) for code in CONFIGURATION_CODES],
        # A missing pool/client is configuration on a login, not a bad token.
        ("ResourceNotFoundException", CognitoConfigurationError),
        ("SomeBrandNewCognitoException", CognitoTransientError),
    ],
)
@pytest.mark.parametrize("leg", ["initiate", "verifier"])
def test_srp_login_client_errors_are_classified(leg, code, expected):
    if leg == "initiate":
        client = FakeCognitoClient(initiate=[_client_error(code)])
    else:
        client = FakeCognitoClient(
            initiate=[_password_verifier()],
            respond=[_client_error(code, "RespondToAuthChallenge")],
        )

    with pytest.raises(CognitoAuthError) as caught:
        _authenticator(client).login_srp(USERNAME, PASSWORD, CLIENT_SECRET)

    assert type(caught.value) is expected
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


@pytest.mark.parametrize("make_error", TRANSPORT_FAILURES)
def test_srp_login_transport_failures_are_transient(make_error):
    client = FakeCognitoClient(initiate=[_password_verifier()], respond=[make_error()])

    with pytest.raises(CognitoTransientError):
        _authenticator(client).login_srp(USERNAME, PASSWORD, CLIENT_SECRET)


def test_srp_login_unexpected_failure_is_a_protocol_error():
    client = FakeCognitoClient(initiate=[_password_verifier()], respond=[KeyError("x")])

    with pytest.raises(CognitoProtocolError):
        _authenticator(client).login_srp(USERNAME, PASSWORD, CLIENT_SECRET)


@pytest.mark.parametrize(
    "response",
    [
        pytest.param({}, id="no-authentication-result"),
        pytest.param(_auth_result(refresh=None), id="no-refresh-token"),
        pytest.param(_auth_result(refresh=""), id="empty-refresh-token"),
        pytest.param(_auth_result(id_token="opaque"), id="malformed-id-token"),
        pytest.param(
            _auth_result(id_token=_jwt({"exp": int(time.time()) + 3600})),
            id="id-token-without-subject",
        ),
    ],
)
def test_srp_login_malformed_results_are_protocol_errors(response):
    with pytest.raises(CognitoProtocolError):
        _srp_login(response)


# -- challenge responses ---------------------------------------------------------
@pytest.mark.parametrize(
    ("challenge", "answer_key"),
    [
        ("SMS_MFA", "SMS_MFA_CODE"),
        ("SOFTWARE_TOKEN_MFA", "SOFTWARE_TOKEN_MFA_CODE"),
        ("EMAIL_OTP", "EMAIL_OTP_CODE"),
        ("SMS_OTP", "SMS_OTP_CODE"),
        (CHALLENGE_NEW_PASSWORD_REQUIRED, "NEW_PASSWORD"),
    ],
)
def test_challenge_answer_echoes_the_session_and_internal_username(
    challenge, answer_key
):
    client = FakeCognitoClient(respond=[_auth_result(refresh="synthetic-answer-rt")])

    outcome = _authenticator(client).respond_to_challenge(
        _state(challenge), "synthetic-answer", CLIENT_SECRET
    )

    assert isinstance(outcome, AuthSuccess)
    assert outcome.tokens.refresh_token == "synthetic-answer-rt"
    # Answered against the session: no SRP re-run, no password re-sent.
    assert client.initiate_calls == []
    (call,) = client.respond_calls
    assert call["ClientId"] == CLIENT_ID
    assert call["ChallengeName"] == challenge
    assert call["Session"] == SESSION
    assert call["ChallengeResponses"] == {
        "USERNAME": INTERNAL_USERNAME,
        answer_key: "synthetic-answer",
        "SECRET_HASH": secret_hash(INTERNAL_USERNAME, CLIENT_ID, CLIENT_SECRET),
    }


def test_challenge_answer_without_a_client_secret_sends_no_secret_hash():
    client = FakeCognitoClient(respond=[_auth_result()])

    _authenticator(client).respond_to_challenge(_state(), "123456", "")

    assert "SECRET_HASH" not in client.respond_calls[0]["ChallengeResponses"]


@pytest.mark.parametrize("code", ["CodeMismatchException", "ExpiredCodeException"])
def test_wrong_one_time_code_is_retryable_not_a_rejected_credential(code):
    client = FakeCognitoClient(respond=[_client_error(code, "RespondToAuthChallenge")])

    with pytest.raises(CognitoAuthError) as caught:
        _authenticator(client).respond_to_challenge(_state(), "000000", CLIENT_SECRET)

    assert type(caught.value) is CognitoChallengeResponseRejected
    assert caught.value.__cause__ is None


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("NotAuthorizedException", CognitoCredentialRejected),
        ("TooManyRequestsException", CognitoTransientError),
        ("InternalErrorException", CognitoTransientError),
        ("InvalidParameterException", CognitoConfigurationError),
        ("SomeBrandNewCognitoException", CognitoTransientError),
    ],
)
def test_challenge_answer_client_errors_are_classified(code, expected):
    client = FakeCognitoClient(respond=[_client_error(code, "RespondToAuthChallenge")])

    with pytest.raises(CognitoAuthError) as caught:
        _authenticator(client).respond_to_challenge(_state(), "000000", CLIENT_SECRET)

    assert type(caught.value) is expected


@pytest.mark.parametrize("make_error", TRANSPORT_FAILURES)
def test_challenge_answer_transport_failures_are_transient(make_error):
    client = FakeCognitoClient(respond=[make_error()])

    with pytest.raises(CognitoTransientError):
        _authenticator(client).respond_to_challenge(_state(), "000000", CLIENT_SECRET)


def test_challenge_answer_unexpected_failure_is_a_protocol_error():
    client = FakeCognitoClient(respond=[KeyError("synthetic")])

    with pytest.raises(CognitoProtocolError):
        _authenticator(client).respond_to_challenge(_state(), "000000", CLIENT_SECRET)


@pytest.mark.parametrize(
    "state",
    [
        pytest.param(_state(session=None), id="missing-session"),
        pytest.param(_state("MFA_SETUP"), id="unmodelled-challenge"),
        pytest.param(_state(depth=MAX_CHALLENGE_CHAIN), id="chain-exhausted"),
    ],
)
def test_unanswerable_challenge_state_is_unsupported_without_calling_cognito(state):
    client = FakeCognitoClient()

    with pytest.raises(CognitoChallengeRequired) as caught:
        _authenticator(client).respond_to_challenge(state, "000000", CLIENT_SECRET)

    assert caught.value.supported is False
    assert client.respond_calls == []


def test_challenge_answer_can_chain_into_another_supported_challenge():
    client = FakeCognitoClient(
        respond=[_challenge("SMS_MFA", session="next-session", internal="next-user")]
    )

    outcome = _authenticator(client).respond_to_challenge(
        _state("SOFTWARE_TOKEN_MFA", depth=1), "123456", CLIENT_SECRET
    )

    assert isinstance(outcome, ChallengeRequired)
    assert outcome.state == ChallengeState(
        challenge_name="SMS_MFA", username="next-user", session="next-session", depth=2
    )


def _answer_every_challenge(authenticator, state, *, limit):
    for _ in range(limit):
        state = authenticator.respond_to_challenge(state, "1", CLIENT_SECRET).state
    return state


def test_challenge_chain_is_bounded():
    """A server that keeps issuing challenges cannot loop the flow forever."""
    client = FakeCognitoClient(
        respond=[_challenge("SMS_MFA", session=f"s{i}") for i in range(10)]
    )

    with pytest.raises(CognitoChallengeRequired) as caught:
        _answer_every_challenge(_authenticator(client), _state(), limit=10)

    assert caught.value.supported is False
    assert len(client.respond_calls) == MAX_CHALLENGE_CHAIN


@pytest.mark.parametrize(
    "response",
    [
        pytest.param(_challenge("MFA_SETUP"), id="unmodelled-next-challenge"),
        pytest.param(_challenge("SMS_MFA", session=None), id="next-without-session"),
    ],
)
def test_challenge_answer_chaining_into_an_unusable_challenge_is_unsupported(response):
    client = FakeCognitoClient(respond=[response])

    with pytest.raises(CognitoChallengeRequired) as caught:
        _authenticator(client).respond_to_challenge(_state(), "123456", CLIENT_SECRET)

    assert caught.value.challenge_name == response["ChallengeName"]
    assert caught.value.supported is False


@pytest.mark.parametrize(
    "response",
    [
        pytest.param({}, id="empty-response"),
        pytest.param(_auth_result(refresh=None), id="no-refresh-token"),
    ],
)
def test_challenge_answer_without_usable_tokens_is_a_protocol_error(response):
    client = FakeCognitoClient(respond=[response])

    with pytest.raises(CognitoProtocolError):
        _authenticator(client).respond_to_challenge(_state(), "123456", CLIENT_SECRET)


# -- requests validated by the real botocore service model -----------------------
def test_refresh_request_passes_botocore_model_validation():
    client = _local_botocore_client()
    with Stubber(client) as stubber:
        stubber.add_response(
            "initiate_auth",
            _auth_result(refresh=None),
            {
                "ClientId": CLIENT_ID,
                "AuthFlow": "REFRESH_TOKEN_AUTH",
                "AuthParameters": {
                    "REFRESH_TOKEN": "synthetic-current-rt",
                    "SECRET_HASH": secret_hash(USER_SUB, CLIENT_ID, CLIENT_SECRET),
                },
            },
        )

        tokens = _authenticator(client).refresh(
            "synthetic-current-rt", USER_SUB, CLIENT_SECRET
        )

        stubber.assert_no_pending_responses()
    assert tokens.is_fresh


def test_srp_login_and_mfa_answer_pass_botocore_model_validation():
    client = _local_botocore_client()
    authenticator = _authenticator(client)
    with Stubber(client) as stubber:
        stubber.add_response(
            "initiate_auth",
            _password_verifier(),
            {"ClientId": CLIENT_ID, "AuthFlow": "USER_SRP_AUTH", "AuthParameters": ANY},
        )
        stubber.add_response(
            "respond_to_auth_challenge",
            _challenge("SOFTWARE_TOKEN_MFA"),
            {
                "ClientId": CLIENT_ID,
                "ChallengeName": "PASSWORD_VERIFIER",
                "ChallengeResponses": ANY,
            },
        )
        stubber.add_response(
            "respond_to_auth_challenge",
            _auth_result(refresh="synthetic-mfa-rt"),
            {
                "ClientId": CLIENT_ID,
                "ChallengeName": "SOFTWARE_TOKEN_MFA",
                "Session": SESSION,
                "ChallengeResponses": {
                    "USERNAME": INTERNAL_USERNAME,
                    "SOFTWARE_TOKEN_MFA_CODE": "123456",
                    "SECRET_HASH": secret_hash(
                        INTERNAL_USERNAME, CLIENT_ID, CLIENT_SECRET
                    ),
                },
            },
        )

        challenge = authenticator.login_srp(USERNAME, PASSWORD, CLIENT_SECRET)
        outcome = authenticator.respond_to_challenge(
            challenge.state, "123456", CLIENT_SECRET
        )

        stubber.assert_no_pending_responses()
    assert isinstance(outcome, AuthSuccess)
    assert outcome.tokens.refresh_token == "synthetic-mfa-rt"


def test_stubbed_botocore_service_error_is_classified():
    client = _local_botocore_client()
    with Stubber(client) as stubber:
        stubber.add_client_error(
            "initiate_auth", service_error_code="NotAuthorizedException"
        )

        with pytest.raises(CognitoCredentialRejected):
            _authenticator(client).refresh("rt", USER_SUB, CLIENT_SECRET)


# -- exception contract ----------------------------------------------------------------
def test_challenge_required_carries_only_a_label_and_a_support_flag():
    error = CognitoChallengeRequired("SMS_MFA", supported=True)

    assert error.challenge_name == "SMS_MFA"
    assert error.supported is True
    assert "SMS_MFA" not in str(error)


@pytest.mark.parametrize(
    "error_type",
    [
        CognitoCredentialRejected,
        CognitoChallengeResponseRejected,
        CognitoTransientError,
        CognitoProtocolError,
        CognitoConfigurationError,
        CognitoChallengeRequired,
    ],
)
def test_every_typed_failure_shares_the_cognito_base_class(error_type):
    assert issubclass(error_type, CognitoAuthError)
    # Callers branch on the exact type: transient is never a rejection.
    if error_type is not CognitoTransientError:
        assert not issubclass(error_type, CognitoTransientError)
    if error_type is not CognitoCredentialRejected:
        assert not issubclass(error_type, CognitoCredentialRejected)
