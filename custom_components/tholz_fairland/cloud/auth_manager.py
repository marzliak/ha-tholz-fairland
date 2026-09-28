"""
Bounded Cognito recovery state machine for one Fairland config entry.

The manager owns the fallback policy described in the recovery plan: reuse a
valid in-memory session, then the current refresh token, then the recovery
seed, then a single SRP login when automatic mode stored a password. It is
synchronous and every entry point is guarded by a re-entrant lock, so all
callers — the polling client and the config flow — share one in-flight
authentication instead of racing Cognito.

No Home Assistant imports live here. Persistence and reauthentication are
delegated to injected callbacks so this stays testable and loop-agnostic.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from .cognito_auth import (
    AuthSuccess,
    AuthTokens,
    ChallengeRequired,
    CognitoAuthError,
    CognitoChallengeRequired,
    CognitoConfigurationError,
    CognitoCredentialRejected,
    CognitoProtocolError,
    CognitoTransientError,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from .cognito_auth import CognitoAuthenticator

_LOGGER = logging.getLogger(__name__)

DEFAULT_TRANSIENT_BACKOFF_SECONDS = 30.0
# A fresh SRP login is expensive and, for MFA accounts, sends a code to the
# user. Bound how often it can run — across manager instances via a shared
# ledger — so a retry storm cannot hammer Cognito or the account.
DEFAULT_SRP_MIN_INTERVAL_SECONDS = 900.0
DEFAULT_SRP_MAX_PER_DAY = 3
_ONE_DAY_SECONDS = 24 * 60 * 60

ERR_AUTH_FAILED = "cloud authentication could not be recovered"


class ReauthReason(StrEnum):
    """Sanitized category handed to the reauth callback (never a raw error)."""

    CREDENTIAL_REJECTED = "credential_rejected"
    CHALLENGE_REQUIRED = "challenge_required"
    MISSING_PASSWORD = "missing_password"  # noqa: S105 — a category, not a secret
    CONFIGURATION = "configuration"
    PROTOCOL = "protocol"


class RecoveryMode(StrEnum):
    """Whether a stored password may drive unattended SRP recovery."""

    AUTOMATIC = "automatic"
    INTERACTIVE = "interactive"


@dataclass(frozen=True)
class CredentialUpdate:
    """
    A credential change the entry should persist.

    ``promote_recovery`` marks a fresh SRP login, where the new refresh token
    becomes both the current token and the recovery seed. Ordinary rotation
    leaves ``recovery_token`` unset so a still-valid seed is not erased.
    ``expected_current`` is the stored current token this write replaces; the
    persistence layer compares-and-swaps against it so a stale, late-arriving
    write from a superseded manager cannot clobber newer credentials.
    ``generation`` is a per-instance monotonic counter for diagnostics.
    """

    current_token: str = field(repr=False)
    generation: int = 0
    promote_recovery: bool = False
    recovery_token: str | None = field(default=None, repr=False)
    user_sub: str | None = field(default=None, repr=False)
    expected_current: str | None = field(default=None, repr=False)


class FairlandAuthManager:
    """Serialize Cognito recovery and hand out a valid id token."""

    def __init__(
        self,
        authenticator: CognitoAuthenticator,
        *,
        user_sub: str,
        client_secret: str | None,
        current_token: str,
        recovery_token: str | None,
        username: str | None = None,
        password: str | None = None,
        mode: RecoveryMode = RecoveryMode.INTERACTIVE,
        transient_backoff: float = DEFAULT_TRANSIENT_BACKOFF_SECONDS,
        srp_ledger: dict | None = None,
        srp_min_interval: float = DEFAULT_SRP_MIN_INTERVAL_SECONDS,
        srp_max_per_day: int = DEFAULT_SRP_MAX_PER_DAY,
    ) -> None:
        self._auth = authenticator
        self._user_sub = user_sub
        self._client_secret = client_secret
        self._current_token = current_token
        self._recovery_token = recovery_token or None
        self._username = username or None
        self._password = password or None
        self._mode = mode
        self._transient_backoff = transient_backoff
        # Shared across manager instances for one entry so the SRP budget
        # survives reloads and ConfigEntryNotReady retries.
        self._srp_ledger = srp_ledger if srp_ledger is not None else {}
        self._srp_min_interval = srp_min_interval
        self._srp_max_per_day = srp_max_per_day

        self._lock = threading.RLock()
        self._tokens: AuthTokens | None = None
        # Plain mirror of the cached expiry so the event loop can read it
        # without taking the auth lock (which a worker thread may hold across a
        # blocking Cognito call). Float assignment is atomic in CPython.
        self._expiry: float | None = None
        self._generation = 0
        # The stored current token at the start of the active recovery cycle,
        # used as the compare-and-swap baseline for every persist it triggers.
        self._cycle_baseline: str | None = None
        # Latched once the password/credentials are definitively rejected or a
        # challenge appears; blocks further background SRP until a reload brings
        # fresh credentials. Prevents a rejected password being retried forever.
        self._blocked = False
        self._reauth_opened = False
        self._backoff_until = 0.0

        self._persist_callback: Callable[[CredentialUpdate], None] | None = None
        self._reauth_callback: Callable[[ReauthReason], None] | None = None

    # -- wiring ------------------------------------------------------------
    def set_persist_callback(
        self, callback: Callable[[CredentialUpdate], None] | None
    ) -> None:
        """Register the callback that persists rotated/recovered credentials."""
        self._persist_callback = callback

    def set_reauth_callback(
        self, callback: Callable[[ReauthReason], None] | None
    ) -> None:
        """Register the callback that opens a single interactive reauth."""
        self._reauth_callback = callback

    @property
    def id_token_expiry(self) -> float | None:
        """Expiry epoch of the cached id token, if one is held (lock-free)."""
        return self._expiry

    @property
    def generation(self) -> int:
        """Monotonic counter bumped on each fresh SRP promotion."""
        return self._generation

    # -- public entry point ------------------------------------------------
    def get_valid_id_token(self) -> str:
        """
        Return a valid Cognito id token, recovering as policy allows.

        Raises a :class:`CognitoAuthError` subclass on failure. Only
        :class:`CognitoTransientError` should be treated as retry-later; every
        other failure has already requested reauthentication.
        """
        cached = self._cached_token()
        if cached is not None:
            return cached
        with self._lock:
            # Re-check under the lock: a concurrent caller may have refreshed.
            if self._tokens is not None and self._tokens.is_fresh:
                return self._tokens.id_token
            return self._recover_locked()

    def _cached_token(self) -> str | None:
        with self._lock:
            if self._tokens is not None and self._tokens.is_fresh:
                return self._tokens.id_token
            return None

    # -- recovery chain (holds the lock) -----------------------------------
    def _recover_locked(self) -> str:
        if self._blocked:
            # Credentials already known bad; do not retry, just keep asking for
            # one reauth until a reload replaces this manager.
            self._request_reauth(ReauthReason.CREDENTIAL_REJECTED)
            raise CognitoCredentialRejected(ERR_AUTH_FAILED)

        now = time.time()
        if now < self._backoff_until:
            raise CognitoTransientError(ERR_AUTH_FAILED)

        # The stored current token at the start of this cycle is the CAS
        # baseline: every persist below only lands if the entry still holds it.
        self._cycle_baseline = self._current_token

        # 1) current refresh token, once.
        try:
            return self._try_refresh(self._current_token, adopt_recovery=False)
        except CognitoCredentialRejected:
            _LOGGER.debug("Fairland current refresh token rejected; trying recovery")
        except CognitoTransientError:
            self._enter_backoff()
            raise
        except (CognitoConfigurationError, CognitoProtocolError) as exc:
            self._request_reauth(self._reason_for(exc))
            raise

        # 2) recovery seed, once, only when it differs from the current token.
        if self._recovery_token and self._recovery_token != self._current_token:
            try:
                return self._try_refresh(self._recovery_token, adopt_recovery=True)
            except CognitoCredentialRejected:
                _LOGGER.debug("Fairland recovery seed rejected; trying SRP login")
            except CognitoTransientError:
                self._enter_backoff()
                raise
            except (CognitoConfigurationError, CognitoProtocolError) as exc:
                self._request_reauth(self._reason_for(exc))
                raise

        # 3) SRP login, once per recovery cycle, only in automatic mode.
        if self._mode is RecoveryMode.AUTOMATIC and self._password and self._username:
            return self._try_srp_login()

        # No SRP is possible (interactive mode, or no stored password): both
        # stored tokens are rejected and nothing will change until the user
        # reauthenticates. Latch so later polls short-circuit here instead of
        # re-sending the two known-bad refresh tokens to Cognito every cycle.
        self._blocked = True
        self._request_reauth(ReauthReason.MISSING_PASSWORD)
        raise CognitoCredentialRejected(ERR_AUTH_FAILED)

    def _try_refresh(self, refresh_token: str, *, adopt_recovery: bool) -> str:
        tokens = self._auth.refresh(refresh_token, self._user_sub, self._client_secret)
        # A refresh grant may or may not rotate the refresh token. When it does
        # not, keep using the token we just proved valid.
        new_current = tokens.refresh_token or refresh_token
        self._adopt_tokens(tokens)
        if new_current != self._current_token:
            baseline = self._cycle_baseline
            self._current_token = new_current
            self._persist(
                CredentialUpdate(
                    current_token=new_current,
                    generation=self._generation,
                    expected_current=baseline,
                )
            )
        _LOGGER.debug(
            "Fairland session refreshed via %s token",
            "recovery" if adopt_recovery else "current",
        )
        return tokens.id_token

    def _try_srp_login(self) -> str:
        assert self._username is not None  # noqa: S101 — guarded by caller
        assert self._password is not None  # noqa: S101 — guarded by caller
        if not self._srp_allowed():
            # Budget exhausted: treat as retry-later rather than hammer Cognito
            # (or re-send an MFA code). Not a credential rejection, so no latch,
            # but back off so the next polls short-circuit instead of re-sending
            # the two already-rejected refresh tokens.
            self._enter_backoff()
            raise CognitoTransientError(ERR_AUTH_FAILED)
        self._record_srp_attempt()
        try:
            outcome = self._auth.login_srp(
                self._username, self._password, self._client_secret
            )
        except CognitoCredentialRejected:
            self._blocked = True
            self._request_reauth(ReauthReason.CREDENTIAL_REJECTED)
            raise
        except CognitoChallengeRequired:
            self._blocked = True
            self._request_reauth(ReauthReason.CHALLENGE_REQUIRED)
            raise
        except CognitoTransientError:
            self._enter_backoff()
            raise
        except (CognitoConfigurationError, CognitoProtocolError) as exc:
            self._blocked = True
            self._request_reauth(self._reason_for(exc))
            raise

        if isinstance(outcome, ChallengeRequired):
            # A supported challenge still needs a human; stop background retries
            # and route to one interactive reauth.
            self._blocked = True
            self._request_reauth(ReauthReason.CHALLENGE_REQUIRED)
            raise CognitoChallengeRequired(outcome.state.challenge_name, supported=True)

        return self._promote_srp_success(outcome)

    def _promote_srp_success(self, success: AuthSuccess) -> str:
        tokens = success.tokens
        assert tokens.refresh_token is not None  # noqa: S101 — validated in adapter
        subject = success.subject
        session = self._verify_srp_refresh(tokens, subject)
        # If the verification refresh rotated the token, that rotated token — not
        # the now-superseded SRP token — is the one to keep as current and seed.
        new_token = session.refresh_token or tokens.refresh_token
        baseline = self._cycle_baseline
        self._adopt_tokens(session)
        self._current_token = new_token
        self._recovery_token = new_token
        self._user_sub = subject
        self._generation += 1
        self._blocked = False
        self._reauth_opened = False
        self._persist(
            CredentialUpdate(
                current_token=new_token,
                recovery_token=new_token,
                user_sub=subject,
                promote_recovery=True,
                generation=self._generation,
                expected_current=baseline,
            )
        )
        _LOGGER.info("Fairland session recovered through a fresh SRP login")
        return session.id_token

    def _verify_srp_refresh(self, tokens: AuthTokens, subject: str) -> AuthTokens:
        """
        Confirm the SRP-issued token refreshes with ``subject`` before commit.

        A subject that Cognito will not accept for the refresh grant would cause
        an endless recovery loop; catching it here keeps a bad identity out of
        storage. A merely transient verification failure trusts the SRP result.
        """
        try:
            return self._auth.refresh(
                tokens.refresh_token, subject, self._client_secret
            )
        except CognitoTransientError:
            return tokens
        except CognitoAuthError as exc:
            self._blocked = True
            self._request_reauth(ReauthReason.CONFIGURATION)
            raise CognitoConfigurationError(ERR_AUTH_FAILED) from exc

    # -- helpers -----------------------------------------------------------
    def _adopt_tokens(self, tokens: AuthTokens) -> None:
        self._tokens = tokens
        self._expiry = tokens.expires_at

    def _srp_allowed(self) -> bool:
        now = time.monotonic()
        attempts = [
            stamp
            for stamp in self._srp_ledger.get("attempts", [])
            if now - stamp < _ONE_DAY_SECONDS
        ]
        self._srp_ledger["attempts"] = attempts
        if len(attempts) >= self._srp_max_per_day:
            return False
        last = self._srp_ledger.get("last")
        return last is None or (now - last) >= self._srp_min_interval

    def _record_srp_attempt(self) -> None:
        now = time.monotonic()
        self._srp_ledger.setdefault("attempts", []).append(now)
        self._srp_ledger["last"] = now

    def _enter_backoff(self) -> None:
        self._backoff_until = time.time() + self._transient_backoff

    @staticmethod
    def _reason_for(exc: CognitoAuthError) -> ReauthReason:
        if isinstance(exc, CognitoConfigurationError):
            return ReauthReason.CONFIGURATION
        return ReauthReason.PROTOCOL

    def _persist(self, update: CredentialUpdate) -> None:
        if self._persist_callback is None:
            return
        try:
            self._persist_callback(update)
        except Exception:
            _LOGGER.warning("Fairland credential persistence callback failed")

    def _request_reauth(self, reason: ReauthReason) -> None:
        if self._reauth_opened:
            return
        self._reauth_opened = True
        if self._reauth_callback is None:
            return
        try:
            self._reauth_callback(reason)
        except Exception:
            _LOGGER.warning("Fairland reauth callback failed")
