"""
Cognito authentication adapter for Fairland automatic recovery.

This module speaks only to AWS Cognito. It has no Home Assistant imports, does
no config-entry persistence, and knows nothing about AWS IoT or the heat pump.
Every call is synchronous and MUST run inside an executor thread.

The public surface is a small set of typed outcomes and exceptions. Raw AWS
payloads, tokens, SRP values, passwords, ``SECRET_HASH`` and Cognito ``Session``
strings never cross this boundary and are never logged. The SRP handshake is
driven directly (``AWSSRP`` supplies only the cryptographic helpers) so that
every challenge returned after the password step is surfaced uniformly with its
opaque session, rather than some being swallowed by the library.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import boto3
from botocore import UNSIGNED
from botocore.config import Config as BotoConfig
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ParamValidationError,
    ReadTimeoutError,
)
from pycognito.aws_srp import AWSSRP

if TYPE_CHECKING:
    from collections.abc import Callable

_LOGGER = logging.getLogger(__name__)

_SRP_CHALLENGE = "PASSWORD_VERIFIER"
_MAX_CHALLENGE_CHAIN = 3

# --- challenge names -----------------------------------------------------
CHALLENGE_SMS_MFA = "SMS_MFA"
CHALLENGE_SOFTWARE_TOKEN_MFA = "SOFTWARE_TOKEN_MFA"  # noqa: S105 — a challenge name
CHALLENGE_EMAIL_OTP = "EMAIL_OTP"
CHALLENGE_SMS_OTP = "SMS_OTP"
CHALLENGE_NEW_PASSWORD_REQUIRED = "NEW_PASSWORD_REQUIRED"  # noqa: S105 — a challenge name

# Challenges we can complete interactively with a single user-supplied value.
SUPPORTED_CHALLENGES = frozenset(
    {
        CHALLENGE_SMS_MFA,
        CHALLENGE_SOFTWARE_TOKEN_MFA,
        CHALLENGE_EMAIL_OTP,
        CHALLENGE_SMS_OTP,
        CHALLENGE_NEW_PASSWORD_REQUIRED,
    }
)
# The one-time-code entry field Cognito expects for each MFA/OTP challenge.
_CODE_RESPONSE_KEY = {
    CHALLENGE_SMS_MFA: "SMS_MFA_CODE",
    CHALLENGE_SOFTWARE_TOKEN_MFA: "SOFTWARE_TOKEN_MFA_CODE",
    CHALLENGE_EMAIL_OTP: "EMAIL_OTP_CODE",
    CHALLENGE_SMS_OTP: "SMS_OTP_CODE",
}

# --- error-code classification -------------------------------------------
# Codes that mean the presented username/password was itself rejected.
_REJECT_LOGIN_CODES = frozenset(
    {
        "NotAuthorizedException",
        "UserNotFoundException",
        "UserNotConfirmedException",
        "PasswordResetRequiredException",
        "InvalidPasswordException",
    }
)
# Codes that mean the stored refresh token is expired/revoked/unknown.
_REJECT_REFRESH_CODES = frozenset(
    {
        "ExpiredTokenException",
        "InvalidTokenException",
        "NotAuthorizedException",
        "ResourceNotFoundException",
    }
)
# Codes that mean "try again later" — never a rejected credential.
_TRANSIENT_CODES = frozenset(
    {
        "TooManyRequestsException",
        "ThrottlingException",
        "ThrottledException",
        "LimitExceededException",
        "InternalErrorException",
        "ServiceUnavailableException",
        "RequestTimeoutException",
    }
)
# Codes that mean the static configuration or account state is wrong and no
# amount of retrying will help.
_CONFIG_CODES = frozenset(
    {
        "InvalidParameterException",
        "InvalidLambdaResponseException",
        "UnexpectedLambdaException",
        "OperationNotEnabledException",
        "ResourceNotFoundException",
    }
)
# A wrong or expired one-time code: the user simply mistyped it.
_CHALLENGE_REJECT_CODES = frozenset(
    {"CodeMismatchException", "ExpiredCodeException", "InvalidPasswordException"}
)

ERR_INVALID_ID_TOKEN = "Cognito returned an invalid ID token"  # noqa: S105
ERR_NO_AUTH_RESULT = "Cognito returned no authentication result"
ERR_NO_REFRESH_TOKEN = "Cognito login issued no refresh token"  # noqa: S105
ERR_MISSING_USERNAME = "a Cognito username is required for SRP login"

ERR_CREDENTIAL_REJECTED = "Cognito rejected the stored credentials"
ERR_CHALLENGE_REJECTED = "the verification value was not accepted"
ERR_TRANSIENT = "the Cognito service was temporarily unavailable"
ERR_PROTOCOL = "the Cognito response did not match the expected protocol"
ERR_CONFIG = "the Cognito configuration is incomplete or inconsistent"
ERR_UNSUPPORTED_CHALLENGE = (
    "the account requires a sign-in step this integration cannot complete"
)

_EXPIRY_SKEW_SECONDS = 120

# A private, tightly-bounded client config. The SRP/refresh calls are public
# Cognito operations, so requests are unsigned; a single attempt avoids
# re-sending a one-time SRP proof, which Cognito would reject.
_BOTO_CONFIG = BotoConfig(
    signature_version=UNSIGNED,
    connect_timeout=5,
    read_timeout=10,
    retries={"total_max_attempts": 1},
)


class CognitoAuthError(Exception):
    """Base class for every Cognito authentication failure."""


class CognitoCredentialRejected(CognitoAuthError):  # noqa: N818 — reads as a state
    """A refresh token, or username/password, was rejected outright."""


class CognitoChallengeResponseRejected(CognitoAuthError):  # noqa: N818
    """A one-time code or new password was wrong; the user may retry."""


class CognitoTransientError(CognitoAuthError):
    """A network, throttling, or service failure that should be retried."""


class CognitoProtocolError(CognitoAuthError):
    """The Cognito response was malformed or missing required tokens."""


class CognitoConfigurationError(CognitoAuthError):
    """Static configuration (client id/secret, username) is inconsistent."""


class CognitoChallengeRequired(CognitoAuthError):  # noqa: N818 — reads as a state
    """
    Cognito requires an interactive challenge that stops background recovery.

    ``supported`` marks challenges the config flow can drive to completion;
    unsupported challenges route to a single sanitized reauthentication.
    ``challenge_name`` is a bare Cognito challenge label and carries no secret.
    """

    def __init__(self, challenge_name: str, *, supported: bool) -> None:
        super().__init__(ERR_UNSUPPORTED_CHALLENGE)
        self.challenge_name = challenge_name
        self.supported = supported


@dataclass(frozen=True)
class AuthTokens:
    """A validated set of Cognito tokens with a decoded expiry."""

    id_token: str = field(repr=False)
    access_token: str | None = field(repr=False)
    refresh_token: str | None = field(repr=False)
    expires_at: float = 0.0

    @property
    def is_fresh(self) -> bool:
        """True while the id token stays valid past the safety skew."""
        return self.expires_at > time.time() + _EXPIRY_SKEW_SECONDS


@dataclass(frozen=True)
class ChallengeState:
    """
    In-memory challenge context. Never persisted, never logged.

    ``username`` is the internal username Cognito expects echoed back (and used
    for this leg's ``SECRET_HASH``). ``session`` is the opaque Cognito session.
    ``depth`` bounds a challenge chain.
    """

    challenge_name: str
    username: str = field(repr=False)
    session: str | None = field(default=None, repr=False)
    depth: int = 0


@dataclass(frozen=True)
class AuthSuccess:
    """
    A completed authentication with its tokens and the refresh subject.

    ``subject`` is the value the refresh grant's ``SECRET_HASH`` must be computed
    against (``cognito:username``, falling back to ``sub``).
    """

    tokens: AuthTokens
    subject: str = field(repr=False)


@dataclass(frozen=True)
class ChallengeRequired:
    """A supported challenge the config flow must complete interactively."""

    state: ChallengeState


# A login/refresh either succeeds or asks for a supported challenge; every
# other condition raises a typed CognitoAuthError.
AuthOutcome = AuthSuccess | ChallengeRequired


def _decode_jwt_claims(token: str) -> dict[str, Any]:
    """Decode JWT claims without verification (used only to read exp/subject)."""
    try:
        encoded = token.split(".")[1]
        encoded += "=" * (-len(encoded) % 4)
        claims = json.loads(base64.urlsafe_b64decode(encoded))
    except (AttributeError, IndexError, ValueError, json.JSONDecodeError) as exc:
        raise CognitoProtocolError(ERR_INVALID_ID_TOKEN) from exc
    if not isinstance(claims, dict):
        raise CognitoProtocolError(ERR_INVALID_ID_TOKEN)
    return claims


def secret_hash(name: str, client_id: str, client_secret: str) -> str:
    """Compute the Cognito SECRET_HASH for ``name`` under the app client."""
    message = f"{name}{client_id}".encode()
    digest = hmac.new(client_secret.encode(), message, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def _tokens_from_result(result: dict[str, Any]) -> AuthTokens:
    """Validate an AuthenticationResult and read the id token expiry."""
    id_token = result.get("IdToken")
    if not id_token or not isinstance(id_token, str):
        raise CognitoProtocolError(ERR_INVALID_ID_TOKEN)
    claims = _decode_jwt_claims(id_token)
    expiry = claims.get("exp")
    if not isinstance(expiry, (int, float)) or isinstance(expiry, bool):
        raise CognitoProtocolError(ERR_INVALID_ID_TOKEN)
    refresh = result.get("RefreshToken")
    access = result.get("AccessToken")
    return AuthTokens(
        id_token=id_token,
        access_token=access if isinstance(access, str) else None,
        refresh_token=refresh if isinstance(refresh, str) and refresh else None,
        expires_at=float(expiry),
    )


def _refresh_subject(id_token: str) -> str:
    """
    Return the subject the refresh SECRET_HASH must use.

    Cognito validates a refresh grant's SECRET_HASH against the user's internal
    username, which is ``cognito:username`` — equal to ``sub`` only in pools
    where sign-in uses an alias. Prefer it, fall back to ``sub``.
    """
    claims = _decode_jwt_claims(id_token)
    subject = claims.get("cognito:username") or claims.get("sub")
    if not subject or not isinstance(subject, str):
        raise CognitoProtocolError(ERR_INVALID_ID_TOKEN)
    return subject


class CognitoAuthenticator:
    """
    Stateless Cognito protocol adapter.

    A ``client_factory`` may be injected so tests can supply a fake
    ``cognito-idp`` client. In production one private, tightly-bounded client is
    built lazily and reused. Instances hold no tokens and no session state.
    """

    def __init__(
        self,
        region: str,
        user_pool_id: str,
        client_id: str,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._region = region
        self._user_pool_id = user_pool_id
        self._client_id = client_id
        self._client_factory = client_factory
        self._cached_client: Any = None

    def _idp(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory()
        if self._cached_client is None:
            self._cached_client = boto3.session.Session().client(
                "cognito-idp", region_name=self._region, config=_BOTO_CONFIG
            )
        return self._cached_client

    # -- error translation -------------------------------------------------
    @staticmethod
    def _client_error_code(exc: ClientError) -> str:
        return str(exc.response.get("Error", {}).get("Code", ""))

    def _translate_client_error(
        self, exc: ClientError, *, context: str
    ) -> CognitoAuthError:
        """Map a botocore ClientError to a typed, sanitized error."""
        code = self._client_error_code(exc)
        if code in _TRANSIENT_CODES:
            return CognitoTransientError(ERR_TRANSIENT)
        # ResourceNotFoundException is a rejected token on a refresh, but a
        # missing pool/client (configuration) on a login.
        if context == "refresh" and code in _REJECT_REFRESH_CODES:
            return CognitoCredentialRejected(ERR_CREDENTIAL_REJECTED)
        if code in _CONFIG_CODES:
            return CognitoConfigurationError(ERR_CONFIG)
        if code in _REJECT_LOGIN_CODES:
            return CognitoCredentialRejected(ERR_CREDENTIAL_REJECTED)
        # Unknown codes are treated as transient so a novel throttle or outage is
        # never mistaken for a rejected credential.
        return CognitoTransientError(ERR_TRANSIENT)

    @staticmethod
    def _translate_transport_error(exc: Exception) -> CognitoAuthError | None:
        if isinstance(exc, ParamValidationError):
            return CognitoConfigurationError(ERR_CONFIG)
        if isinstance(
            exc,
            (
                EndpointConnectionError,
                ConnectTimeoutError,
                ReadTimeoutError,
                ConnectionClosedError,
                ConnectionError,
                TimeoutError,
                OSError,
                BotoCoreError,
            ),
        ):
            return CognitoTransientError(ERR_TRANSIENT)
        return None

    def _reraise_unexpected(self, exc: Exception) -> CognitoAuthError:
        translated = self._translate_transport_error(exc)
        return (
            translated if translated is not None else CognitoProtocolError(ERR_PROTOCOL)
        )

    # -- refresh grant -----------------------------------------------------
    def refresh(
        self, refresh_token: str, subject: str, client_secret: str | None
    ) -> AuthTokens:
        """
        Run one REFRESH_TOKEN_AUTH grant.

        The refresh-grant SECRET_HASH is computed against ``subject`` — the
        internal username this pool issued the token under — never the human
        login username.
        """
        if not refresh_token:
            raise CognitoCredentialRejected(ERR_CREDENTIAL_REJECTED)
        if not subject:
            raise CognitoConfigurationError(ERR_CONFIG)
        auth_parameters = {"REFRESH_TOKEN": refresh_token}
        if client_secret:
            auth_parameters["SECRET_HASH"] = secret_hash(
                subject, self._client_id, client_secret
            )
        try:
            response = self._idp().initiate_auth(
                ClientId=self._client_id,
                AuthFlow="REFRESH_TOKEN_AUTH",
                AuthParameters=auth_parameters,
            )
        except ClientError as exc:
            raise self._translate_client_error(exc, context="refresh") from None
        except Exception as exc:
            raise self._reraise_unexpected(exc) from None
        result = response.get("AuthenticationResult")
        if not isinstance(result, dict):
            raise CognitoProtocolError(ERR_NO_AUTH_RESULT)
        return _tokens_from_result(result)

    # -- SRP login ---------------------------------------------------------
    def login_srp(
        self, username: str, password: str, client_secret: str | None
    ) -> AuthOutcome:
        """
        Authenticate with USER_SRP_AUTH using the stored username/password.

        The two SRP requests are issued here; ``AWSSRP`` provides only the
        client proof math. Any challenge returned after the password step is
        surfaced with its session so the config flow can complete it.
        """
        if not username:
            raise CognitoConfigurationError(ERR_MISSING_USERNAME)
        if not password:
            raise CognitoCredentialRejected(ERR_CREDENTIAL_REJECTED)
        client = self._idp()
        srp = AWSSRP(
            username=username,
            password=password,
            pool_id=self._user_pool_id,
            client_id=self._client_id,
            client=client,
            client_secret=client_secret or None,
        )
        try:
            auth_params = srp.get_auth_params()
            initiated = client.initiate_auth(
                AuthFlow="USER_SRP_AUTH",
                AuthParameters=auth_params,
                ClientId=self._client_id,
            )
            if initiated.get("ChallengeName") != _SRP_CHALLENGE:
                raise CognitoChallengeRequired(  # noqa: TRY301 — sanitized, re-raised below
                    str(initiated.get("ChallengeName") or "UNKNOWN"), supported=False
                )
            responses = srp.process_challenge(
                initiated["ChallengeParameters"], auth_params
            )
            answered = client.respond_to_auth_challenge(
                ClientId=self._client_id,
                ChallengeName=_SRP_CHALLENGE,
                ChallengeResponses=responses,
            )
        except ClientError as exc:
            raise self._translate_client_error(exc, context="login") from None
        except CognitoAuthError:
            raise
        except Exception as exc:
            raise self._reraise_unexpected(exc) from None
        return self._outcome_from_response(answered, username, depth=0)

    # -- challenge responses ----------------------------------------------
    def respond_to_challenge(
        self, state: ChallengeState, response: str, client_secret: str | None
    ) -> AuthOutcome:
        """
        Answer a supported challenge with a single user-supplied value.

        For an MFA/OTP challenge ``response`` is the one-time code; for
        ``NEW_PASSWORD_REQUIRED`` it is the new password. Both are answered
        against the challenge session, so no password is re-sent and no SRP is
        re-run.
        """
        if state.depth >= _MAX_CHALLENGE_CHAIN or not state.session:
            raise CognitoChallengeRequired(state.challenge_name, supported=False)
        responses: dict[str, str] = {"USERNAME": state.username}
        if state.challenge_name == CHALLENGE_NEW_PASSWORD_REQUIRED:
            responses["NEW_PASSWORD"] = response
        else:
            code_key = _CODE_RESPONSE_KEY.get(state.challenge_name)
            if code_key is None:
                raise CognitoChallengeRequired(state.challenge_name, supported=False)
            responses[code_key] = response
        if client_secret:
            responses["SECRET_HASH"] = secret_hash(
                state.username, self._client_id, client_secret
            )
        try:
            answered = self._idp().respond_to_auth_challenge(
                ClientId=self._client_id,
                ChallengeName=state.challenge_name,
                Session=state.session,
                ChallengeResponses=responses,
            )
        except ClientError as exc:
            if self._client_error_code(exc) in _CHALLENGE_REJECT_CODES:
                raise CognitoChallengeResponseRejected(ERR_CHALLENGE_REJECTED) from None
            raise self._translate_client_error(exc, context="login") from None
        except CognitoAuthError:
            raise
        except Exception as exc:
            raise self._reraise_unexpected(exc) from None
        return self._outcome_from_response(
            answered, state.username, depth=state.depth + 1
        )

    # -- outcome parsing ---------------------------------------------------
    def _outcome_from_response(
        self, response: dict[str, Any], login_username: str, *, depth: int
    ) -> AuthOutcome:
        name = response.get("ChallengeName")
        if name:
            session = response.get("Session")
            internal = response.get("ChallengeParameters", {}).get("USERNAME")
            internal = (
                internal if isinstance(internal, str) and internal else login_username
            )
            if str(name) in SUPPORTED_CHALLENGES and isinstance(session, str):
                return ChallengeRequired(
                    ChallengeState(
                        challenge_name=str(name),
                        username=internal,
                        session=session,
                        depth=depth,
                    )
                )
            raise CognitoChallengeRequired(str(name), supported=False)
        result = response.get("AuthenticationResult")
        if not isinstance(result, dict):
            raise CognitoProtocolError(ERR_NO_AUTH_RESULT)
        tokens = _tokens_from_result(result)
        if not tokens.refresh_token:
            raise CognitoProtocolError(ERR_NO_REFRESH_TOKEN)
        return AuthSuccess(tokens=tokens, subject=_refresh_subject(tokens.id_token))
