"""
The manager stops re-sending known-bad tokens once it cannot make progress.

These cover the bounded-retry additions: latching the terminal "no SRP possible"
case (interactive mode / no stored password) and backing off when the SRP budget
refuses, so later polls short-circuit instead of hammering Cognito.
"""

import contextlib
import time

from custom_components.tholz_fairland.cloud import cognito_auth as ca
from custom_components.tholz_fairland.cloud.auth_manager import (
    FairlandAuthManager,
    RecoveryMode,
)

_REJECTED = "rejected"


def _tokens(ttl=3600, rt="rt"):
    return ca.AuthTokens(
        id_token="id", access_token="a", refresh_token=rt, expires_at=time.time() + ttl
    )


class FakeAuth:
    def __init__(self, refresh=None, login=None):
        self.refresh_calls = []
        self.login_calls = 0
        self._refresh = refresh
        self._login = login

    def refresh(self, rt, _sub, _secret):
        self.refresh_calls.append(rt)
        return self._refresh(rt)

    def login_srp(self, u, p, _secret):
        self.login_calls += 1
        return self._login(u, p)


def _reject(_rt):
    raise ca.CognitoCredentialRejected(_REJECTED)


def test_interactive_mode_latches_after_both_tokens_are_rejected():
    """A later poll must not re-send the two known-bad refresh tokens."""
    auth = FakeAuth(refresh=_reject)
    reauth = []
    manager = FairlandAuthManager(
        auth,
        user_sub="s",
        client_secret="s",
        current_token="cur",
        recovery_token="seed",
        mode=RecoveryMode.INTERACTIVE,
    )
    manager.set_reauth_callback(reauth.append)

    for _ in range(3):
        with contextlib.suppress(ca.CognitoAuthError):
            manager.get_valid_id_token()

    # Only the first cycle contacted Cognito (current + recovery); the latch
    # short-circuits the rest.
    assert auth.refresh_calls == ["cur", "seed"]
    # Exactly one reauth is opened despite repeated calls.
    assert len(reauth) == 1


def test_srp_budget_refusal_backs_off_before_rehitting_cognito():
    def _explode(_u, _p):
        raise RuntimeError

    auth = FakeAuth(refresh=_reject, login=_explode)
    # A ledger already at the daily cap refuses SRP immediately.
    ledger = {"attempts": [time.monotonic()] * 3, "last": time.monotonic()}
    manager = FairlandAuthManager(
        auth,
        user_sub="s",
        client_secret="s",
        current_token="cur",
        recovery_token="seed",
        username="u",
        password="pw",
        mode=RecoveryMode.AUTOMATIC,
        srp_ledger=ledger,
        srp_min_interval=900,
    )

    for _ in range(3):
        with contextlib.suppress(ca.CognitoTransientError):
            manager.get_valid_id_token()

    # Budget refuses SRP (no login attempts) and the backoff means only the
    # first cycle re-sent the refresh tokens.
    assert auth.login_calls == 0
    assert auth.refresh_calls == ["cur", "seed"]
