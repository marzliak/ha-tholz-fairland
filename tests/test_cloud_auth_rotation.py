"""Cognito refresh, rotation adoption and seed-recovery regression tests."""

import base64
import json
import time

import pytest
from botocore.exceptions import ClientError

from custom_components.tholz_fairland.cloud.client import (
    FairlandCloudAuthError,
    FairlandCloudClient,
)


def _id_token(ttl: float = 3600.0) -> str:
    """Build a JWT-shaped token whose payload only carries an expiry."""
    payload = (
        base64.urlsafe_b64encode(json.dumps({"exp": time.time() + ttl}).encode())
        .decode()
        .rstrip("=")
    )
    return f"header.{payload}.signature"


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, "InitiateAuth")


def _client(live: str = "live-token", seed: str = "seed-token") -> FairlandCloudClient:
    return FairlandCloudClient(
        thing_id="thing-uuid",
        user_sub="user-sub",
        client_secret="client-secret",
        refresh_token=live,
        initial_refresh_token=seed,
    )


def _stub_refresh(client, behaviour):
    """Replace the single Cognito call with a scripted stub, recording tokens."""
    attempts = []

    def _attempt(refresh_token):
        attempts.append(refresh_token)
        return behaviour(refresh_token)

    client._attempt_refresh = _attempt
    return attempts


def test_rotated_token_is_adopted_and_reported_once():
    client = _client()
    adopted = []
    client.set_refresh_token_callback(adopted.append)
    _stub_refresh(
        client, lambda _token: {"id_token": _id_token(), "refresh_token": "rotated"}
    )

    client._refresh_id_token()

    assert adopted == ["rotated"]
    assert client._refresh_token == "rotated"


def test_unchanged_token_does_not_notify():
    client = _client(live="live-token")
    adopted = []
    client.set_refresh_token_callback(adopted.append)
    _stub_refresh(
        client, lambda _token: {"id_token": _id_token(), "refresh_token": "live-token"}
    )

    client._refresh_id_token()

    assert adopted == []


def test_seed_recovers_a_rejected_rotated_token():
    client = _client(live="stale", seed="seed-token")

    rejected = _client_error("NotAuthorizedException")

    def _behaviour(token):
        if token == "stale":
            raise rejected
        return {"id_token": _id_token(), "refresh_token": "fresh"}

    attempts = _stub_refresh(client, _behaviour)

    assert client._refresh_id_token()
    assert attempts == ["stale", "seed-token"]


def test_seed_becomes_the_live_token_when_cognito_issues_none():
    """Without adoption the next refresh would retry the rejected token."""
    client = _client(live="stale", seed="seed-token")
    adopted = []
    client.set_refresh_token_callback(adopted.append)

    expired = _client_error("ExpiredTokenException")

    def _behaviour(token):
        if token == "stale":
            raise expired
        return {"id_token": _id_token(), "refresh_token": ""}

    _stub_refresh(client, _behaviour)
    client._refresh_id_token()

    assert client._refresh_token == "seed-token"
    assert adopted == ["seed-token"]


def test_auth_failure_callback_fires_when_both_tokens_are_rejected():
    """Home Assistant only starts the reauth flow when this callback fires."""
    client = _client(live="stale", seed="also-stale")
    failures = []
    client.set_auth_failure_callback(failures.append)
    _stub_refresh(
        client, lambda _token: (_ for _ in ()).throw(_client_error("NotAuthorized"))
    )

    with pytest.raises(FairlandCloudAuthError):
        client._refresh_id_token()

    assert len(failures) == 1
    assert isinstance(failures[0], FairlandCloudAuthError)


def test_auth_failure_callback_fires_after_the_seed_also_fails():
    client = _client(live="stale", seed="seed-token")
    failures = []
    client.set_auth_failure_callback(failures.append)
    attempts = _stub_refresh(
        client,
        lambda _token: (_ for _ in ()).throw(_client_error("NotAuthorizedException")),
    )

    with pytest.raises(FairlandCloudAuthError):
        client._refresh_id_token()

    assert attempts == ["stale", "seed-token"]
    assert len(failures) == 1


def test_non_credential_errors_do_not_burn_the_seed():
    """Throttling must not be mistaken for a rejected credential."""
    client = _client(live="live-token", seed="seed-token")
    attempts = _stub_refresh(
        client,
        lambda _token: (_ for _ in ()).throw(_client_error("TooManyRequestsException")),
    )

    with pytest.raises(FairlandCloudAuthError):
        client._refresh_id_token()

    assert attempts == ["live-token"]


def test_transport_timeout_is_reported_as_an_auth_error():
    client = _client()
    failures = []
    client.set_auth_failure_callback(failures.append)
    _stub_refresh(client, lambda _token: (_ for _ in ()).throw(TimeoutError("boom")))

    with pytest.raises(FairlandCloudAuthError):
        client._refresh_id_token()

    assert len(failures) == 1


def test_a_failing_callback_never_breaks_the_refresh_path():
    client = _client()

    exploded = RuntimeError("listener exploded")

    def _explode(_token):
        raise exploded

    client.set_refresh_token_callback(_explode)
    _stub_refresh(
        client, lambda _token: {"id_token": _id_token(), "refresh_token": "rotated"}
    )

    assert client._refresh_id_token()
    assert client._refresh_token == "rotated"


def test_cached_token_short_circuits_the_cognito_call():
    client = _client()
    attempts = _stub_refresh(
        client, lambda _token: {"id_token": _id_token(), "refresh_token": ""}
    )

    first = client._refresh_id_token()
    second = client._refresh_id_token()

    assert first == second
    assert len(attempts) == 1


def test_expiring_token_is_refreshed_again():
    client = _client()
    attempts = _stub_refresh(
        client, lambda _token: {"id_token": _id_token(ttl=30), "refresh_token": ""}
    )

    client._refresh_id_token()
    client._refresh_id_token()

    assert len(attempts) == 2


def test_id_token_expiry_is_exposed_only_once_authenticated():
    client = _client()
    assert client.id_token_expiry is None

    _stub_refresh(client, lambda _token: {"id_token": _id_token(), "refresh_token": ""})
    client._refresh_id_token()

    assert client.id_token_expiry is not None
    assert client.id_token_expiry > time.time()
