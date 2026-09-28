"""
State-machine tests for ``FairlandAuthManager`` (``cloud/auth_manager.py``).

The manager is driven through a duck-typed authenticator whose outcomes are
scripted per refresh token, so every branch of the recovery chain (cached
session, current token, recovery seed, SRP login) is exercised without
Cognito. Credentials are synthetic.

Covers: rotation and promotion bookkeeping, bounded fallback, the rejected
password latch, reauth deduplication, transient backoff, the shared SRP
budget, single-flight concurrency and the compare-and-swap data each persist
carries to guard against stale writes.
"""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from custom_components.tholz_fairland import (
    AUTH_MANAGER_KEY,
    DOMAIN,
    _make_persist_callback,
)
from custom_components.tholz_fairland.cloud import auth_manager as auth_manager_module
from custom_components.tholz_fairland.cloud.auth_manager import (
    CredentialUpdate,
    FairlandAuthManager,
    ReauthReason,
    RecoveryMode,
)
from custom_components.tholz_fairland.cloud.cognito_auth import (
    AuthSuccess,
    AuthTokens,
    ChallengeRequired,
    ChallengeState,
    CognitoAuthError,
    CognitoChallengeRequired,
    CognitoConfigurationError,
    CognitoCredentialRejected,
    CognitoProtocolError,
    CognitoTransientError,
)
from custom_components.tholz_fairland.utils.const import (
    CONF_COGNITO_REFRESH_TOKEN_KEY,
    CONF_COGNITO_USER_SUB_KEY,
    CONF_CREDENTIAL_SEED_REFRESH_TOKEN_KEY,
    RECOVERY_MODE_AUTOMATIC,
    RECOVERY_MODE_INTERACTIVE,
)

USER_SUB = "synthetic-subject-before-srp"
NEW_SUBJECT = "synthetic-subject-after-srp"
CLIENT_SECRET = "synthetic-client-secret"
USERNAME = "owner@example.invalid"
PASSWORD = "synthetic-password"
CURRENT_RT = "rt-current"
RECOVERY_RT = "rt-recovery"
SRP_RT = "rt-srp"
SYNTHETIC_FAILURE = "synthetic failure"
WORKERS = 8
SRP_MIN_INTERVAL = auth_manager_module.DEFAULT_SRP_MIN_INTERVAL_SECONDS
ONE_DAY = 24 * 60 * 60


# -- scripted collaborators ----------------------------------------------------
def _tokens(label, *, rotated=None, ttl=3600.0):
    return AuthTokens(
        id_token=f"id-{label}",
        access_token=f"access-{label}",
        refresh_token=rotated,
        expires_at=time.time() + ttl,
    )


def _srp_success(*, rt=SRP_RT, subject=NEW_SUBJECT, ttl=3600.0):
    return AuthSuccess(tokens=_tokens("srp", rotated=rt, ttl=ttl), subject=subject)


def _as_script(outcomes):
    return list(outcomes) if isinstance(outcomes, (list, tuple)) else [outcomes]


def _play(script):
    """Return or raise the next scripted outcome; the last one repeats."""
    outcome = script[0] if len(script) == 1 else script.pop(0)
    if isinstance(outcome, type) and issubclass(outcome, BaseException):
        raise outcome(SYNTHETIC_FAILURE)
    if isinstance(outcome, BaseException):
        raise outcome
    return outcome


class ScriptedAuthenticator:
    """
    Duck-typed stand-in for ``CognitoAuthenticator``.

    ``refresh`` maps a refresh token to its scripted outcomes; tokens that are
    not scripted are rejected, like a revoked token. ``login`` scripts the SRP
    outcomes. Every call is recorded with its arguments.
    """

    def __init__(self, *, refresh=None, login=(), delay=0.0):
        self.refresh_calls = []
        self.login_calls = []
        self._refresh = {
            token: _as_script(outcomes) for token, outcomes in (refresh or {}).items()
        }
        self._login = _as_script(login)
        self._delay = delay

    def refresh(self, refresh_token, subject, client_secret):
        self.refresh_calls.append((refresh_token, subject, client_secret))
        time.sleep(self._delay)
        return _play(self._refresh.get(refresh_token, [CognitoCredentialRejected]))

    def login_srp(self, username, password, client_secret):
        self.login_calls.append((username, password, client_secret))
        time.sleep(self._delay)
        return _play(self._login)

    @property
    def refreshed(self):
        return [call[0] for call in self.refresh_calls]


def _manager(authenticator, **overrides):
    """Build a manager in automatic mode plus its recorded callback traffic."""
    options = {
        "user_sub": USER_SUB,
        "client_secret": CLIENT_SECRET,
        "current_token": CURRENT_RT,
        "recovery_token": RECOVERY_RT,
        "username": USERNAME,
        "password": PASSWORD,
        "mode": RecoveryMode.AUTOMATIC,
    }
    options.update(overrides)
    manager = FairlandAuthManager(authenticator, **options)
    persisted, reauths = [], []
    manager.set_persist_callback(persisted.append)
    manager.set_reauth_callback(reauths.append)
    return manager, persisted, reauths


def _both_tokens_rejected(**extra):
    """Script where the current token and the seed are both revoked."""
    return {
        CURRENT_RT: CognitoCredentialRejected,
        RECOVERY_RT: CognitoCredentialRejected,
        **extra,
    }


def _call_concurrently(manager, workers=WORKERS):
    """Release ``workers`` threads at once; return each result or exception."""
    barrier = threading.Barrier(workers)

    def worker():
        barrier.wait(timeout=5)
        return manager.get_valid_id_token()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(worker) for _ in range(workers)]
        outcomes = []
        for future in futures:
            error = future.exception(timeout=10)
            outcomes.append(error if error is not None else future.result())
    return outcomes


class FakeClock:
    """Deterministic replacement for the ``time`` module inside auth_manager."""

    def __init__(self, start=1_000_000.0):
        self.now = start

    def time(self):
        return self.now

    def monotonic(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr(auth_manager_module, "time", fake)
    return fake


# -- cached session and current token ------------------------------------------
def test_fresh_session_is_served_from_memory():
    auth = ScriptedAuthenticator(refresh={CURRENT_RT: _tokens("current")})
    manager, persisted, reauths = _manager(auth)

    assert manager.get_valid_id_token() == "id-current"
    assert manager.get_valid_id_token() == "id-current"

    assert auth.refresh_calls == [(CURRENT_RT, USER_SUB, CLIENT_SECRET)]
    assert auth.login_calls == []
    assert persisted == []
    assert reauths == []


def test_session_inside_the_expiry_skew_is_refreshed_again():
    auth = ScriptedAuthenticator(refresh={CURRENT_RT: _tokens("current", ttl=60)})
    manager, _, _ = _manager(auth)

    manager.get_valid_id_token()
    manager.get_valid_id_token()

    assert auth.refreshed == [CURRENT_RT, CURRENT_RT]


def test_rotated_current_token_is_persisted_as_current_only():
    auth = ScriptedAuthenticator(
        refresh={
            CURRENT_RT: _tokens("current", rotated="rt-rotated", ttl=60),
            "rt-rotated": _tokens("rotated"),
        }
    )
    manager, persisted, _ = _manager(auth)

    manager.get_valid_id_token()
    manager.get_valid_id_token()

    assert persisted == [
        CredentialUpdate(
            current_token="rt-rotated",
            generation=0,
            promote_recovery=False,
            recovery_token=None,
            user_sub=None,
            expected_current=CURRENT_RT,
        )
    ]
    # The rotated token is what the next cycle presents.
    assert auth.refreshed == [CURRENT_RT, "rt-rotated"]


def test_rotation_keeps_the_recovery_seed_in_reserve():
    auth = ScriptedAuthenticator(
        refresh={
            CURRENT_RT: _tokens("current", rotated="rt-rotated", ttl=60),
            "rt-rotated": CognitoCredentialRejected,
            RECOVERY_RT: _tokens("recovery"),
        }
    )
    manager, _, _ = _manager(auth)

    manager.get_valid_id_token()
    assert manager.get_valid_id_token() == "id-recovery"

    assert auth.refreshed == [CURRENT_RT, "rt-rotated", RECOVERY_RT]
    assert auth.login_calls == []


# -- recovery seed ---------------------------------------------------------------
def test_rejected_current_token_falls_back_to_the_recovery_seed():
    auth = ScriptedAuthenticator(
        refresh={CURRENT_RT: CognitoCredentialRejected, RECOVERY_RT: _tokens("seed")}
    )
    manager, persisted, reauths = _manager(auth)

    assert manager.get_valid_id_token() == "id-seed"

    assert auth.refreshed == [CURRENT_RT, RECOVERY_RT]
    assert auth.login_calls == []
    assert reauths == []
    (update,) = persisted
    assert update.current_token == RECOVERY_RT
    assert update.expected_current == CURRENT_RT
    assert update.promote_recovery is False
    assert update.recovery_token is None


def test_rotated_recovery_seed_is_persisted_as_the_new_current():
    auth = ScriptedAuthenticator(
        refresh={
            CURRENT_RT: CognitoCredentialRejected,
            RECOVERY_RT: _tokens("seed", rotated="rt-seed-rotated"),
        }
    )
    manager, persisted, _ = _manager(auth)

    manager.get_valid_id_token()

    (update,) = persisted
    assert update.current_token == "rt-seed-rotated"
    assert update.recovery_token is None


@pytest.mark.parametrize("seed", [CURRENT_RT, None, ""])
def test_recovery_seed_is_only_tried_when_it_differs_from_current(seed):
    auth = ScriptedAuthenticator(
        refresh={CURRENT_RT: CognitoCredentialRejected, SRP_RT: _tokens("verified")},
        login=_srp_success(),
    )
    manager, _, _ = _manager(auth, recovery_token=seed)

    manager.get_valid_id_token()

    # Only the current token and the post-SRP verification are refreshed.
    assert auth.refreshed == [CURRENT_RT, SRP_RT]
    assert len(auth.login_calls) == 1


# -- SRP fallback --------------------------------------------------------------------
def test_srp_fallback_promotes_the_new_token_to_current_and_recovery():
    auth = ScriptedAuthenticator(
        refresh=_both_tokens_rejected(**{SRP_RT: _tokens("verified")}),
        login=_srp_success(),
    )
    manager, persisted, reauths = _manager(auth)

    assert manager.get_valid_id_token() == "id-verified"

    assert auth.login_calls == [(USERNAME, PASSWORD, CLIENT_SECRET)]
    # The new token is proven refreshable with the new subject before commit.
    assert auth.refresh_calls[-1] == (SRP_RT, NEW_SUBJECT, CLIENT_SECRET)
    assert persisted == [
        CredentialUpdate(
            current_token=SRP_RT,
            generation=1,
            promote_recovery=True,
            recovery_token=SRP_RT,
            user_sub=NEW_SUBJECT,
            expected_current=CURRENT_RT,
        )
    ]
    assert manager.generation == 1
    assert reauths == []


def test_after_srp_the_next_refresh_uses_the_new_token_and_subject():
    auth = ScriptedAuthenticator(
        refresh=_both_tokens_rejected(
            **{SRP_RT: [_tokens("verified", ttl=60), _tokens("next")]}
        ),
        login=_srp_success(),
    )
    manager, _, _ = _manager(auth)

    manager.get_valid_id_token()
    assert manager.get_valid_id_token() == "id-next"

    assert auth.refresh_calls[-1] == (SRP_RT, NEW_SUBJECT, CLIENT_SECRET)
    assert len(auth.login_calls) == 1


def test_transient_verification_failure_trusts_the_srp_session():
    auth = ScriptedAuthenticator(
        refresh=_both_tokens_rejected(**{SRP_RT: CognitoTransientError}),
        login=_srp_success(),
    )
    manager, persisted, reauths = _manager(auth)

    assert manager.get_valid_id_token() == "id-srp"

    (update,) = persisted
    assert update.promote_recovery is True
    assert update.current_token == SRP_RT
    assert reauths == []


@pytest.mark.parametrize(
    "failure",
    [CognitoCredentialRejected, CognitoProtocolError, CognitoConfigurationError],
)
def test_failed_verification_keeps_the_new_identity_out_of_storage(failure):
    auth = ScriptedAuthenticator(
        refresh=_both_tokens_rejected(**{SRP_RT: failure}), login=_srp_success()
    )
    manager, persisted, reauths = _manager(auth)

    with pytest.raises(CognitoConfigurationError):
        manager.get_valid_id_token()
    calls_after_failure = len(auth.refresh_calls)
    with pytest.raises(CognitoAuthError):
        manager.get_valid_id_token()

    assert persisted == []
    assert reauths == [ReauthReason.CONFIGURATION]
    assert len(auth.login_calls) == 1
    assert len(auth.refresh_calls) == calls_after_failure


@pytest.mark.parametrize(
    ("verification", "survivor"),
    [
        pytest.param(
            {SRP_RT: [_tokens("verified", ttl=60), _tokens("next")]},
            SRP_RT,
            id="not-rotated",
        ),
        pytest.param(
            {
                SRP_RT: _tokens("verified", rotated="rt-srp-rotated", ttl=60),
                "rt-srp-rotated": _tokens("next"),
            },
            "rt-srp-rotated",
            id="rotated",
        ),
    ],
)
def test_srp_promotion_persists_the_token_that_survives_verification(
    verification, survivor
):
    """A rotation during the verification refresh supersedes the SRP token."""
    auth = ScriptedAuthenticator(
        refresh=_both_tokens_rejected(**verification), login=_srp_success()
    )
    manager, persisted, _ = _manager(auth)

    manager.get_valid_id_token()
    assert manager.get_valid_id_token() == "id-next"

    (update,) = persisted
    assert update.current_token == survivor
    assert update.recovery_token == survivor
    assert update.promote_recovery is True
    # The next cycle presents the surviving token with the new subject.
    assert auth.refresh_calls[-1] == (survivor, NEW_SUBJECT, CLIENT_SECRET)


# -- interactive mode and missing credentials ---------------------------------------
@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"mode": RecoveryMode.INTERACTIVE}, id="interactive"),
        pytest.param({"password": None}, id="automatic-without-password"),
        pytest.param({"password": ""}, id="automatic-with-empty-password"),
        pytest.param({"username": None}, id="automatic-without-username"),
    ],
)
def test_exhausted_tokens_without_a_usable_password_request_reauth(overrides):
    auth = ScriptedAuthenticator(refresh=_both_tokens_rejected())
    manager, persisted, reauths = _manager(auth, **overrides)

    with pytest.raises(CognitoCredentialRejected):
        manager.get_valid_id_token()

    assert auth.login_calls == []
    assert reauths == [ReauthReason.MISSING_PASSWORD]
    assert persisted == []


def test_repeated_failures_without_a_password_open_one_reauth_and_stay_bounded():
    auth = ScriptedAuthenticator(refresh=_both_tokens_rejected())
    manager, _, reauths = _manager(auth, mode=RecoveryMode.INTERACTIVE)
    attempts = 5

    for _ in range(attempts):
        with pytest.raises(CognitoCredentialRejected):
            manager.get_valid_id_token()

    assert reauths == [ReauthReason.MISSING_PASSWORD]
    assert auth.login_calls == []
    # At most the two stored tokens per cycle; never a password.
    assert len(auth.refresh_calls) <= 2 * attempts


# -- the rejected-password latch -----------------------------------------------------
def test_rejected_password_is_tried_once_and_latched():
    auth = ScriptedAuthenticator(
        refresh=_both_tokens_rejected(), login=CognitoCredentialRejected
    )
    manager, persisted, reauths = _manager(auth)

    with pytest.raises(CognitoCredentialRejected):
        manager.get_valid_id_token()
    calls_after_rejection = len(auth.refresh_calls)
    for _ in range(5):
        with pytest.raises(CognitoCredentialRejected):
            manager.get_valid_id_token()

    assert len(auth.login_calls) == 1
    # A latched manager stops talking to Cognito altogether.
    assert len(auth.refresh_calls) == calls_after_rejection
    assert reauths == [ReauthReason.CREDENTIAL_REJECTED]
    assert persisted == []


def test_supported_srp_challenge_requests_one_reauth_and_stops():
    challenge = ChallengeState(
        challenge_name="SOFTWARE_TOKEN_MFA", username=USERNAME, session="s-1"
    )
    auth = ScriptedAuthenticator(
        refresh=_both_tokens_rejected(), login=ChallengeRequired(challenge)
    )
    manager, persisted, reauths = _manager(auth)

    with pytest.raises(CognitoChallengeRequired) as caught:
        manager.get_valid_id_token()
    for _ in range(3):
        with pytest.raises(CognitoAuthError):
            manager.get_valid_id_token()

    assert caught.value.challenge_name == "SOFTWARE_TOKEN_MFA"
    assert caught.value.supported is True
    assert len(auth.login_calls) == 1
    assert reauths == [ReauthReason.CHALLENGE_REQUIRED]
    assert persisted == []


def test_unsupported_srp_challenge_is_latched():
    auth = ScriptedAuthenticator(
        refresh=_both_tokens_rejected(),
        login=CognitoChallengeRequired("CUSTOM_CHALLENGE", supported=False),
    )
    manager, _, reauths = _manager(auth)

    with pytest.raises(CognitoChallengeRequired):
        manager.get_valid_id_token()
    with pytest.raises(CognitoAuthError):
        manager.get_valid_id_token()

    assert len(auth.login_calls) == 1
    assert reauths == [ReauthReason.CHALLENGE_REQUIRED]


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (CognitoConfigurationError, ReauthReason.CONFIGURATION),
        (CognitoProtocolError, ReauthReason.PROTOCOL),
    ],
)
def test_srp_configuration_and_protocol_failures_are_latched(failure, reason):
    auth = ScriptedAuthenticator(refresh=_both_tokens_rejected(), login=failure)
    manager, _, reauths = _manager(auth)

    with pytest.raises(failure):
        manager.get_valid_id_token()
    with pytest.raises(CognitoAuthError):
        manager.get_valid_id_token()

    assert len(auth.login_calls) == 1
    assert reauths == [reason]


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (CognitoConfigurationError, ReauthReason.CONFIGURATION),
        (CognitoProtocolError, ReauthReason.PROTOCOL),
    ],
)
def test_refresh_configuration_and_protocol_failures_skip_the_fallbacks(
    failure, reason
):
    auth = ScriptedAuthenticator(refresh={CURRENT_RT: failure}, login=_srp_success())
    manager, _, reauths = _manager(auth)

    with pytest.raises(failure):
        manager.get_valid_id_token()

    assert auth.refreshed == [CURRENT_RT]
    assert auth.login_calls == []
    assert reauths == [reason]


# -- transient failures ------------------------------------------------------------
@pytest.mark.parametrize(
    ("refresh", "login", "expected_refreshes"),
    [
        pytest.param(
            {CURRENT_RT: CognitoTransientError}, (), [CURRENT_RT], id="current-token"
        ),
        pytest.param(
            {CURRENT_RT: CognitoCredentialRejected, RECOVERY_RT: CognitoTransientError},
            (),
            [CURRENT_RT, RECOVERY_RT],
            id="recovery-seed",
        ),
        pytest.param(
            _both_tokens_rejected(),
            CognitoTransientError,
            [CURRENT_RT, RECOVERY_RT],
            id="srp-login",
        ),
    ],
)
def test_transient_failure_never_reauths_latches_or_burns_the_next_step(
    refresh, login, expected_refreshes
):
    auth = ScriptedAuthenticator(refresh=refresh, login=login)
    manager, persisted, reauths = _manager(auth)

    with pytest.raises(CognitoTransientError):
        manager.get_valid_id_token()

    # No later fallback step runs after a transient failure.
    assert auth.refreshed == expected_refreshes
    assert len(auth.login_calls) == (1 if login else 0)
    assert reauths == []
    assert persisted == []
    assert manager._blocked is False

    # The backoff turns an immediate retry into a local, Cognito-free failure.
    with pytest.raises(CognitoTransientError):
        manager.get_valid_id_token()
    assert auth.refreshed == expected_refreshes
    assert len(auth.login_calls) == (1 if login else 0)


def test_recovery_resumes_once_the_backoff_expires(clock):
    auth = ScriptedAuthenticator(
        refresh={CURRENT_RT: [CognitoTransientError, _tokens("current")]}
    )
    manager, _, reauths = _manager(auth, transient_backoff=30.0)

    with pytest.raises(CognitoTransientError):
        manager.get_valid_id_token()
    clock.advance(29.9)
    with pytest.raises(CognitoTransientError):
        manager.get_valid_id_token()
    assert len(auth.refresh_calls) == 1

    clock.advance(0.1)
    assert manager.get_valid_id_token() == "id-current"
    assert len(auth.refresh_calls) == 2
    assert reauths == []


def test_transient_srp_failure_never_latches_the_password(clock):
    auth = ScriptedAuthenticator(
        refresh=_both_tokens_rejected(**{SRP_RT: _tokens("verified")}),
        login=[CognitoTransientError, _srp_success()],
    )
    manager, persisted, reauths = _manager(auth)

    with pytest.raises(CognitoTransientError):
        manager.get_valid_id_token()
    clock.advance(SRP_MIN_INTERVAL)

    assert manager.get_valid_id_token() == "id-verified"
    assert len(auth.login_calls) == 2
    assert reauths == []
    assert persisted[-1].promote_recovery is True


def test_transient_failure_then_rejection_still_opens_exactly_one_reauth(clock):
    auth = ScriptedAuthenticator(
        refresh=_both_tokens_rejected(),
        login=[CognitoTransientError, CognitoCredentialRejected],
    )
    manager, _, reauths = _manager(auth)

    with pytest.raises(CognitoTransientError):
        manager.get_valid_id_token()
    clock.advance(SRP_MIN_INTERVAL)
    with pytest.raises(CognitoCredentialRejected):
        manager.get_valid_id_token()
    with pytest.raises(CognitoCredentialRejected):
        manager.get_valid_id_token()

    assert reauths == [ReauthReason.CREDENTIAL_REJECTED]
    assert len(auth.login_calls) == 2


# -- SRP budget -------------------------------------------------------------------------
def test_srp_budget_enforces_a_minimum_interval(clock):
    auth = ScriptedAuthenticator(
        refresh=_both_tokens_rejected(**{SRP_RT: _tokens("verified")}),
        login=[CognitoTransientError, _srp_success()],
    )
    manager, _, reauths = _manager(auth, transient_backoff=1.0)

    with pytest.raises(CognitoTransientError):
        manager.get_valid_id_token()
    clock.advance(SRP_MIN_INTERVAL - 1)
    with pytest.raises(CognitoTransientError):
        manager.get_valid_id_token()
    assert len(auth.login_calls) == 1

    clock.advance(1)
    assert manager.get_valid_id_token() == "id-verified"
    assert len(auth.login_calls) == 2
    # A budget refusal is retry-later, never a reauth or a latch.
    assert reauths == []


def test_srp_budget_caps_attempts_per_day(clock):
    auth = ScriptedAuthenticator(
        refresh=_both_tokens_rejected(), login=CognitoTransientError
    )
    manager, _, reauths = _manager(
        auth, transient_backoff=0.0, srp_min_interval=0.0, srp_max_per_day=3
    )

    for _ in range(5):
        with pytest.raises(CognitoTransientError):
            manager.get_valid_id_token()
        clock.advance(60)
    assert len(auth.login_calls) == 3

    clock.advance(ONE_DAY)
    with pytest.raises(CognitoTransientError):
        manager.get_valid_id_token()
    assert len(auth.login_calls) == 4
    assert reauths == []


def test_srp_budget_is_shared_across_manager_instances(clock):
    """A reload or ConfigEntryNotReady retry must not reset the SRP budget."""
    ledger = {}
    first = ScriptedAuthenticator(
        refresh=_both_tokens_rejected(), login=CognitoTransientError
    )
    manager, _, _ = _manager(first, srp_ledger=ledger)
    with pytest.raises(CognitoTransientError):
        manager.get_valid_id_token()

    clock.advance(60)
    second = ScriptedAuthenticator(
        refresh=_both_tokens_rejected(), login=_srp_success()
    )
    replacement, _, reauths = _manager(second, srp_ledger=ledger)
    with pytest.raises(CognitoTransientError):
        replacement.get_valid_id_token()

    assert len(first.login_calls) == 1
    assert second.login_calls == []
    assert reauths == []


# -- single-flight concurrency ---------------------------------------------------------
def test_concurrent_callers_share_one_srp_login():
    auth = ScriptedAuthenticator(
        refresh=_both_tokens_rejected(**{SRP_RT: _tokens("verified")}),
        login=_srp_success(),
        delay=0.02,
    )
    manager, persisted, reauths = _manager(auth)

    outcomes = _call_concurrently(manager)

    assert outcomes == ["id-verified"] * WORKERS
    assert len(auth.login_calls) == 1
    assert auth.refreshed == [CURRENT_RT, RECOVERY_RT, SRP_RT]
    assert len(persisted) == 1
    assert reauths == []


def test_concurrent_callers_share_one_refresh():
    auth = ScriptedAuthenticator(refresh={CURRENT_RT: _tokens("current")}, delay=0.05)
    manager, _, _ = _manager(auth)

    outcomes = _call_concurrently(manager)

    assert outcomes == ["id-current"] * WORKERS
    assert auth.refreshed == [CURRENT_RT]


def test_concurrent_callers_share_one_rejected_login():
    auth = ScriptedAuthenticator(
        refresh=_both_tokens_rejected(), login=CognitoCredentialRejected, delay=0.02
    )
    manager, _, reauths = _manager(auth)

    outcomes = _call_concurrently(manager)

    assert all(isinstance(outcome, CognitoCredentialRejected) for outcome in outcomes)
    assert len(auth.login_calls) == 1
    assert reauths == [ReauthReason.CREDENTIAL_REJECTED]


def test_concurrent_callers_share_one_transient_failure():
    auth = ScriptedAuthenticator(
        refresh={CURRENT_RT: CognitoTransientError}, delay=0.05
    )
    manager, _, reauths = _manager(auth)

    outcomes = _call_concurrently(manager)

    assert all(isinstance(outcome, CognitoTransientError) for outcome in outcomes)
    assert auth.refreshed == [CURRENT_RT]
    assert reauths == []


def test_caller_that_missed_the_cache_rechecks_it_under_the_lock():
    """
    Pin the double-checked lock deterministically.

    A caller that found no session, then waited while another caller
    recovered, must reuse that session instead of recovering again.
    """
    auth = ScriptedAuthenticator(refresh={CURRENT_RT: _tokens("current")})
    manager, _, _ = _manager(auth)
    unlocked_check = manager._cached_token
    missed, resume = threading.Event(), threading.Event()

    def check_then_pause():
        cached = unlocked_check()
        if threading.current_thread().name == "late-caller":
            missed.set()
            resume.wait(timeout=5)
        return cached

    manager._cached_token = check_then_pause
    late_results = []
    late = threading.Thread(
        target=lambda: late_results.append(manager.get_valid_id_token()),
        name="late-caller",
    )
    late.start()
    assert missed.wait(timeout=5)

    assert manager.get_valid_id_token() == "id-current"
    resume.set()
    late.join(timeout=5)

    assert late_results == ["id-current"]
    assert auth.refreshed == [CURRENT_RT]


def test_expiry_is_readable_while_recovery_holds_the_lock():
    auth = ScriptedAuthenticator(refresh={CURRENT_RT: _tokens("current")})
    manager, _, _ = _manager(auth)
    assert manager.id_token_expiry is None
    manager.get_valid_id_token()
    expected = manager.id_token_expiry
    holding, release = threading.Event(), threading.Event()

    def hold_the_lock():
        with manager._lock:
            holding.set()
            release.wait(timeout=5)

    holder = threading.Thread(target=hold_the_lock)
    holder.start()
    assert holding.wait(timeout=5)
    with ThreadPoolExecutor(max_workers=1) as pool:
        reader = pool.submit(lambda: manager.id_token_expiry)
        try:
            observed = reader.result(timeout=1)
        finally:
            release.set()
    holder.join(timeout=5)

    assert observed == expected
    assert expected is not None


# -- stale-write protection ------------------------------------------------------------
def _rotation_then_srp_then_rotation():
    """Three short-lived cycles: rotate, SRP recover, rotate again."""
    return ScriptedAuthenticator(
        refresh={
            CURRENT_RT: _tokens("current", rotated="rt-rot-1", ttl=60),
            "rt-rot-1": CognitoCredentialRejected,
            RECOVERY_RT: CognitoCredentialRejected,
            SRP_RT: [
                _tokens("verified", ttl=60),
                _tokens("after", rotated="rt-rot-2", ttl=60),
            ],
        },
        login=_srp_success(ttl=60),
    )


def test_every_persist_names_the_token_it_replaces():
    manager, persisted, _ = _manager(_rotation_then_srp_then_rotation())

    for _ in range(3):
        manager.get_valid_id_token()

    assert [update.current_token for update in persisted] == [
        "rt-rot-1",
        SRP_RT,
        "rt-rot-2",
    ]
    # Each write's compare-and-swap baseline is the previous write's result.
    assert [update.expected_current for update in persisted] == [
        CURRENT_RT,
        "rt-rot-1",
        SRP_RT,
    ]
    generations = [update.generation for update in persisted]
    assert generations == sorted(generations)
    assert generations == [0, 1, 1]


class InlineLoop:
    """Queue thread-safe callbacks; run them when the test says so."""

    def __init__(self):
        self.pending = []

    def call_soon_threadsafe(self, callback, *args):
        self.pending.append((callback, args))

    def run(self):
        while self.pending:
            callback, args = self.pending.pop(0)
            callback(*args)


class RecordingConfigEntries:
    def __init__(self, entry):
        self._entry = entry
        self.updates = []

    def async_get_entry(self, entry_id):
        return self._entry if entry_id == self._entry.entry_id else None

    def async_update_entry(self, entry, **changes):
        self.updates.append(changes)
        for key, value in changes.items():
            setattr(entry, key, dict(value))
        return True


def _persisting_manager(authenticator):
    """Wire a manager to the integration's real persist path over fakes."""
    entry = SimpleNamespace(
        entry_id="synthetic-entry",
        title="Fairland",
        data={
            CONF_COGNITO_REFRESH_TOKEN_KEY: CURRENT_RT,
            CONF_CREDENTIAL_SEED_REFRESH_TOKEN_KEY: RECOVERY_RT,
            CONF_COGNITO_USER_SUB_KEY: USER_SUB,
        },
        options={},
    )
    hass = SimpleNamespace(data={}, loop=InlineLoop())
    hass.config_entries = RecordingConfigEntries(entry)
    manager, _, _ = _manager(authenticator)
    hass.data[DOMAIN] = {entry.entry_id: {AUTH_MANAGER_KEY: manager}}
    manager.set_persist_callback(_make_persist_callback(hass, entry, manager))
    return hass, entry, manager


def test_in_order_writes_all_land_through_the_real_persist_path():
    hass, entry, manager = _persisting_manager(_rotation_then_srp_then_rotation())

    for _ in range(3):
        manager.get_valid_id_token()
        hass.loop.run()

    assert entry.data[CONF_COGNITO_REFRESH_TOKEN_KEY] == "rt-rot-2"
    assert entry.data[CONF_CREDENTIAL_SEED_REFRESH_TOKEN_KEY] == SRP_RT
    assert entry.data[CONF_COGNITO_USER_SUB_KEY] == NEW_SUBJECT
    assert len(hass.config_entries.updates) == 3


def test_rotation_racing_an_external_credential_write_is_dropped():
    """A reauth that stored new credentials must win over a late rotation."""
    hass, entry, manager = _persisting_manager(
        ScriptedAuthenticator(refresh={CURRENT_RT: _tokens("current", rotated="late")})
    )

    manager.get_valid_id_token()
    entry.data = {**entry.data, CONF_COGNITO_REFRESH_TOKEN_KEY: "rt-from-reauth"}
    hass.loop.run()

    assert entry.data[CONF_COGNITO_REFRESH_TOKEN_KEY] == "rt-from-reauth"
    assert hass.config_entries.updates == []


def test_write_from_a_superseded_manager_is_dropped():
    hass, entry, manager = _persisting_manager(
        ScriptedAuthenticator(refresh={CURRENT_RT: _tokens("current", rotated="late")})
    )

    manager.get_valid_id_token()
    # A reload replaced the manager before the queued write reached the loop.
    hass.data[DOMAIN][entry.entry_id][AUTH_MANAGER_KEY] = object()
    hass.loop.run()

    assert entry.data[CONF_COGNITO_REFRESH_TOKEN_KEY] == CURRENT_RT
    assert hass.config_entries.updates == []


# -- callbacks ---------------------------------------------------------------------------
def test_persistence_failure_keeps_the_session_and_logs_a_generic_warning(caplog):
    auth = ScriptedAuthenticator(
        refresh=_both_tokens_rejected(**{SRP_RT: _tokens("verified")}),
        login=_srp_success(),
    )
    manager, _, reauths = _manager(auth)

    def broken_persist(_update):
        raise OSError(SYNTHETIC_FAILURE)

    manager.set_persist_callback(broken_persist)

    with caplog.at_level(logging.WARNING, logger=auth_manager_module.__name__):
        assert manager.get_valid_id_token() == "id-verified"

    assert "persistence callback failed" in caplog.text
    assert SYNTHETIC_FAILURE not in caplog.text
    assert reauths == []


def test_reauth_callback_failure_is_contained(caplog):
    auth = ScriptedAuthenticator(refresh=_both_tokens_rejected())
    manager, _, _ = _manager(auth, mode=RecoveryMode.INTERACTIVE)
    calls = []

    def broken_reauth(reason):
        calls.append(reason)
        raise RuntimeError(SYNTHETIC_FAILURE)

    manager.set_reauth_callback(broken_reauth)

    with caplog.at_level(logging.WARNING, logger=auth_manager_module.__name__):
        for _ in range(2):
            with pytest.raises(CognitoCredentialRejected):
                manager.get_valid_id_token()

    assert calls == [ReauthReason.MISSING_PASSWORD]
    assert "reauth callback failed" in caplog.text
    assert SYNTHETIC_FAILURE not in caplog.text


def test_manager_works_without_registered_callbacks():
    auth = ScriptedAuthenticator(
        refresh=_both_tokens_rejected(**{SRP_RT: _tokens("verified")}),
        login=_srp_success(),
    )
    manager = FairlandAuthManager(
        auth,
        user_sub=USER_SUB,
        client_secret=CLIENT_SECRET,
        current_token=CURRENT_RT,
        recovery_token=RECOVERY_RT,
        username=USERNAME,
        password=PASSWORD,
        mode=RecoveryMode.AUTOMATIC,
    )

    assert manager.get_valid_id_token() == "id-verified"


def test_successful_srp_rearms_reauthentication():
    auth = ScriptedAuthenticator(
        refresh={
            CURRENT_RT: [CognitoProtocolError, CognitoCredentialRejected],
            RECOVERY_RT: CognitoCredentialRejected,
            SRP_RT: [_tokens("verified", ttl=60), CognitoCredentialRejected],
        },
        login=[_srp_success(ttl=60), CognitoCredentialRejected],
    )
    manager, _, reauths = _manager(auth, srp_min_interval=0.0)

    with pytest.raises(CognitoProtocolError):
        manager.get_valid_id_token()
    assert manager.get_valid_id_token() == "id-verified"
    with pytest.raises(CognitoCredentialRejected):
        manager.get_valid_id_token()

    assert reauths == [ReauthReason.PROTOCOL, ReauthReason.CREDENTIAL_REJECTED]


def test_recovery_mode_values_match_the_stored_schema():
    assert RecoveryMode.AUTOMATIC == RECOVERY_MODE_AUTOMATIC
    assert RecoveryMode.INTERACTIVE == RECOVERY_MODE_INTERACTIVE
    assert {reason.value for reason in ReauthReason} == {
        "credential_rejected",
        "challenge_required",
        "missing_password",
        "configuration",
        "protocol",
    }
