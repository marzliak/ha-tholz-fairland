"""
The cloud client delegates token acquisition to the auth manager.

The recovery chain itself (current -> recovery seed -> SRP) lives in and is
tested against ``FairlandAuthManager``; here we only prove the client hands off
to it and maps its typed failures onto the transport errors the integration's
setup path already distinguishes (auth failure vs. retry-later).
"""

import time

import pytest

from custom_components.tholz_fairland.cloud.auth_manager import RecoveryMode
from custom_components.tholz_fairland.cloud.client import (
    FairlandCloudAuthError,
    FairlandCloudClient,
    FairlandCloudError,
    FairlandCloudProtocolError,
)
from custom_components.tholz_fairland.cloud.cognito_auth import (
    CognitoChallengeRequired,
    CognitoConfigurationError,
    CognitoCredentialRejected,
    CognitoProtocolError,
    CognitoTransientError,
)


class FakeAuthManager:
    """Stand-in token provider with a scriptable outcome."""

    def __init__(self, token="id-token", *, raises=None, expiry=1000.0):
        self._token = token
        self._raises = raises
        self._expiry = expiry
        self.calls = 0

    def get_valid_id_token(self):
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        return self._token

    @property
    def id_token_expiry(self):
        return self._expiry


def _client(manager):
    return FairlandCloudClient(thing_id="thing-uuid", auth_manager=manager)


def test_client_returns_the_managers_id_token():
    manager = FakeAuthManager(token="fresh-id")
    assert _client(manager)._id_token() == "fresh-id"
    assert manager.calls == 1


@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (CognitoCredentialRejected("x"), FairlandCloudAuthError),
        (CognitoChallengeRequired("SMS_MFA", supported=True), FairlandCloudAuthError),
        (CognitoConfigurationError("x"), FairlandCloudAuthError),
        (CognitoProtocolError("x"), FairlandCloudProtocolError),
    ],
)
def test_auth_style_failures_map_to_cloud_errors(raised, expected):
    client = _client(FakeAuthManager(raises=raised))
    with pytest.raises(expected):
        client._id_token()


def test_transient_failure_is_not_an_auth_error():
    """A throttle/outage must map to a non-auth error so setup retries later."""
    client = _client(FakeAuthManager(raises=CognitoTransientError("throttle")))
    with pytest.raises(FairlandCloudError) as caught:
        client._id_token()
    # Explicitly not the auth subclass that would trigger reauthentication.
    assert not isinstance(caught.value, FairlandCloudAuthError)


def test_id_token_expiry_is_read_from_the_manager():
    manager = FakeAuthManager(expiry=time.time() + 500)
    assert _client(manager).id_token_expiry == manager.id_token_expiry


def test_legacy_constructor_builds_a_refresh_only_manager():
    """Callers that pass raw credentials still get a working interactive manager."""
    client = FairlandCloudClient(
        thing_id="thing-uuid",
        user_sub="user-sub",
        client_secret="client-secret",
        refresh_token="live-token",
        initial_refresh_token="seed-token",
    )
    manager = client._auth
    assert manager._mode is RecoveryMode.INTERACTIVE
    assert manager._current_token == "live-token"
    assert manager._recovery_token == "seed-token"
    # No password means SRP never runs for a legacy-constructed client.
    assert manager._password is None
