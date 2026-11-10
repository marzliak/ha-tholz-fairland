import json
import urllib.parse
from types import SimpleNamespace

import pytest

from custom_components.tholz_fairland.cloud import client as client_module
from custom_components.tholz_fairland.cloud.client import (
    FairlandCloudAuthError,
    FairlandCloudClient,
    FairlandCloudError,
    FairlandCloudProtocolError,
    IotAuthorizer,
    build_shadow_update,
    extract_mutable_changes,
    normalize_schedule_document,
    summarize_metrics_document,
)


BASELINE = {
    "id": 1496,
    "connected": True,
    "error": 0,
    "heatings": {
        "heat0": {
            "type": 7,
            "on": False,
            "opMode": 0,
            "sp": 330,
            "step": 10,
            "fanMode": 1,
            "t1": 180,
            "t2": 240,
            "t3": 230,
        }
    },
}

SCHEDULE_DOCUMENT = {
    "version": 42,
    "timestamp": 1_777_777_777,
    "state": {
        "reported": {
            f"event{index}": {
                "active": index == 0,
                "turnOn": {"hr": 12, "min": index},
                "turnOff": {"hr": 23, "min": 59},
                "rept": 127,
            }
            for index in range(5)
        }
    },
}


class _CompletedFuture:
    def result(self, timeout=None):
        del timeout


class _FakeConnection:
    def __init__(self):
        self.subscriptions = []
        self.disconnected = False

    def connect(self):
        return _CompletedFuture()

    def subscribe(self, *, topic, qos, callback):
        self.subscriptions.append((topic, qos, callback))
        return _CompletedFuture(), 1

    def disconnect(self):
        self.disconnected = True
        return _CompletedFuture()


class _FakeAuthManager:
    id_token_expiry = 1_600.0
    user_sub = "subject-123"

    def __init__(self):
        self.authorization_rejected = 0

    @staticmethod
    def get_valid_id_token():
        return "id-token"

    def report_authorization_rejected(self):
        self.authorization_rejected += 1


def test_iot_authorizer_uses_raw_id_token_header(monkeypatch):
    calls = []

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"authorizer": "fairland-authorizer", "signature": "sig+/="}

    def fake_get(url, *, headers, timeout):
        calls.append((url, headers, timeout))
        return Response()

    monkeypatch.setattr(client_module.requests, "get", fake_get)
    client = FairlandCloudClient(thing_id="thing", auth_manager=_FakeAuthManager())

    result = client._fetch_iot_authorizer("id-token")

    assert result == IotAuthorizer("fairland-authorizer", "sig+/=")
    assert calls == [
        (
            client_module.IOT_AUTHORIZER_URL,
            {"Authorization": "id-token"},
            15.0,
        )
    ]


@pytest.mark.parametrize("status_code", [401, 403])
def test_iot_authorizer_rejection_invalidates_auth_and_requests_reauth(
    monkeypatch, status_code
):
    class Response:
        def __init__(self):
            self.status_code = status_code

    monkeypatch.setattr(
        client_module.requests, "get", lambda *_args, **_kwargs: Response()
    )
    auth_manager = _FakeAuthManager()
    client = FairlandCloudClient(thing_id="thing", auth_manager=auth_manager)

    with pytest.raises(FairlandCloudAuthError):
        client._fetch_iot_authorizer("id-token")

    assert auth_manager.authorization_rejected == 1


def test_iot_authorizer_server_error_is_retryable_protocol_failure(monkeypatch):
    class Response:
        status_code = 503

    monkeypatch.setattr(
        client_module.requests, "get", lambda *_args, **_kwargs: Response()
    )
    auth_manager = _FakeAuthManager()
    client = FairlandCloudClient(thing_id="thing", auth_manager=auth_manager)

    with pytest.raises(FairlandCloudProtocolError):
        client._fetch_iot_authorizer("id-token")

    assert auth_manager.authorization_rejected == 0


def test_iot_authorizer_signature_is_redacted_from_repr():
    signature = "sensitive-signature"

    rendered = repr(IotAuthorizer("authorizer", signature))

    assert "authorizer" in rendered
    assert signature not in rendered


@pytest.mark.parametrize(
    "document",
    [None, {}, {"authorizer": "name"}, {"signature": "signature"}],
)
def test_iot_authorizer_rejects_invalid_response(monkeypatch, document):
    class Response:
        status_code = 200

        @staticmethod
        def json():
            return document

    monkeypatch.setattr(
        client_module.requests, "get", lambda *_args, **_kwargs: Response()
    )
    client = FairlandCloudClient(thing_id="thing", auth_manager=_FakeAuthManager())

    with pytest.raises(FairlandCloudProtocolError):
        client._fetch_iot_authorizer("id-token")


def test_iot_authorizer_network_failure_is_retryable(monkeypatch):
    offline = "offline"

    def fail(*_args, **_kwargs):
        raise client_module.requests.ConnectionError(offline)

    monkeypatch.setattr(client_module.requests, "get", fail)
    client = FairlandCloudClient(thing_id="thing", auth_manager=_FakeAuthManager())

    with pytest.raises(FairlandCloudError):
        client._fetch_iot_authorizer("id-token")


def test_mqtt_query_matches_tholz_41_protocol(monkeypatch):
    monkeypatch.setattr(client_module.time, "time", lambda: 1_000.0)
    client = FairlandCloudClient(
        thing_id="thing/with space", auth_manager=_FakeAuthManager()
    )

    query = client._mqtt_query(
        "id.token/value", IotAuthorizer("auth name", "sig+/="), 1_600.0
    )

    assert urllib.parse.parse_qs(query, strict_parsing=True) == {
        "x-amz-customauthorizer-name": ["auth name"],
        "x-amz-customauthorizer-signature": ["sig+/="],
        "token": ["id.token/value"],
        "thingIds": ["thing/with space"],
        "disconnectAfterInSeconds": ["600"],
    }


def test_connection_uses_custom_authorizer_websocket(monkeypatch):
    monkeypatch.setattr(client_module.time, "time", lambda: 1_000.0)
    fake_connection = _FakeConnection()
    builder_calls = []

    def fake_builder(**kwargs):
        builder_calls.append(kwargs)
        return fake_connection

    monkeypatch.setattr(
        client_module.mqtt_connection_builder,
        "websockets_with_custom_handshake",
        fake_builder,
    )
    client = FairlandCloudClient(thing_id="thing", auth_manager=_FakeAuthManager())
    monkeypatch.setattr(
        client,
        "_fetch_iot_authorizer",
        lambda _token: IotAuthorizer("authorizer", "signature"),
    )

    client._ensure_connection()

    assert len(builder_calls) == 1
    kwargs = builder_calls[0]
    assert kwargs["endpoint"] == client_module.IOT_ENDPOINT
    assert kwargs["client_id"] == "#tholz-app_subject-123_primary_1000000"
    assert len(fake_connection.subscriptions) == 4
    done = []
    request = SimpleNamespace(path="/mqtt")
    transform_args = SimpleNamespace(
        http_request=request,
        set_done=lambda error=None: done.append(error),
    )
    kwargs["websocket_handshake_transform"](transform_args)
    assert done == [None]
    assert request.path.startswith("/mqtt?")
    parsed_query = urllib.parse.parse_qs(request.path.split("?", 1)[1])
    assert parsed_query["token"] == ["id-token"]
    assert parsed_query["thingIds"] == ["thing"]
    assert parsed_query["disconnectAfterInSeconds"] == ["600"]


def test_interruption_stops_stale_reconnect_and_redacts_error(caplog):
    connection = _FakeConnection()
    client = FairlandCloudClient(thing_id="thing", auth_manager=_FakeAuthManager())
    client._connection = connection
    client._connection_expiry = 1_600.0
    secret = "token-must-not-be-logged"

    with caplog.at_level("WARNING"):
        client._on_interrupted(connection, RuntimeError(secret))

    assert connection.disconnected is True
    assert client._connection_interrupted is True
    assert client._connection_expiry == 0.0
    assert secret not in caplog.text
    assert "RuntimeError" in caplog.text


def test_interrupted_connection_is_rebuilt_with_fresh_subscriptions(monkeypatch):
    monkeypatch.setattr(client_module.time, "time", lambda: 1_000.0)
    stale_connection = _FakeConnection()
    fresh_connection = _FakeConnection()
    builder_calls = []

    def fake_builder(**kwargs):
        builder_calls.append(kwargs)
        return fresh_connection

    monkeypatch.setattr(
        client_module.mqtt_connection_builder,
        "websockets_with_custom_handshake",
        fake_builder,
    )
    client = FairlandCloudClient(thing_id="thing", auth_manager=_FakeAuthManager())
    client._connection = stale_connection
    client._connection_expiry = 1_600.0
    client._connection_interrupted = True
    monkeypatch.setattr(
        client,
        "_fetch_iot_authorizer",
        lambda _token: IotAuthorizer("new-authorizer", "new-signature"),
    )

    client._ensure_connection()

    assert stale_connection.disconnected is True
    assert client._connection is fresh_connection
    assert client._connection_interrupted is False
    assert len(builder_calls) == 1
    assert len(fresh_connection.subscriptions) == 4


def test_interruption_during_last_subscription_is_never_published(monkeypatch):
    monkeypatch.setattr(client_module.time, "time", lambda: 1_000.0)

    class InterruptingConnection(_FakeConnection):
        def __init__(self, callback):
            super().__init__()
            self._callback = callback

        def subscribe(self, *, topic, qos, callback):
            result = super().subscribe(topic=topic, qos=qos, callback=callback)
            if len(self.subscriptions) == 4:
                self._callback(self, RuntimeError("synthetic interruption"))
            return result

    connection_holder = {}

    def fake_builder(**kwargs):
        connection = InterruptingConnection(kwargs["on_connection_interrupted"])
        connection_holder["connection"] = connection
        return connection

    monkeypatch.setattr(
        client_module.mqtt_connection_builder,
        "websockets_with_custom_handshake",
        fake_builder,
    )
    client = FairlandCloudClient(thing_id="thing", auth_manager=_FakeAuthManager())
    monkeypatch.setattr(
        client,
        "_fetch_iot_authorizer",
        lambda _token: IotAuthorizer("authorizer", "signature"),
    )

    with pytest.raises(FairlandCloudProtocolError):
        client._ensure_connection()

    assert connection_holder["connection"].disconnected is True
    assert client._connection is None
    assert client._connecting_connection is None
    assert client._connection_expiry == 0.0


def test_late_interruption_from_old_connection_does_not_poison_current():
    old_connection = _FakeConnection()
    current_connection = _FakeConnection()
    client = FairlandCloudClient(thing_id="thing", auth_manager=_FakeAuthManager())
    client._connection = current_connection
    client._connection_expiry = 1_600.0

    client._on_interrupted(old_connection, RuntimeError("old connection"))

    assert old_connection.disconnected is True
    assert client._connection is current_connection
    assert client._connection_interrupted is False
    assert client._connection_expiry == 1_600.0


@pytest.mark.parametrize("status_code", [401, 403])
def test_metrics_auth_rejection_is_not_hidden_by_optional_metrics(
    monkeypatch, status_code
):
    class Response:
        def __init__(self):
            self.status_code = status_code

    monkeypatch.setattr(
        client_module.requests, "get", lambda *_args, **_kwargs: Response()
    )
    auth_manager = _FakeAuthManager()
    client = FairlandCloudClient(thing_id="thing", auth_manager=auth_manager)
    monkeypatch.setattr(
        client,
        "_request_shadow",
        lambda: {"state": {"reported": BASELINE}},
    )

    with pytest.raises(FairlandCloudAuthError):
        client.get_status()

    assert auth_manager.authorization_rejected == 1


def test_metrics_server_error_keeps_read_only_state_available(monkeypatch):
    class Response:
        status_code = 503

    monkeypatch.setattr(
        client_module.requests, "get", lambda *_args, **_kwargs: Response()
    )
    auth_manager = _FakeAuthManager()
    client = FairlandCloudClient(thing_id="thing", auth_manager=auth_manager)
    monkeypatch.setattr(
        client,
        "_request_shadow",
        lambda: {"state": {"reported": BASELINE}},
    )
    monkeypatch.setattr(
        client,
        "_request_schedule",
        lambda: SCHEDULE_DOCUMENT,
    )

    state = client.get_status()

    assert state["metricsSummary"] == {}
    assert auth_manager.authorization_rejected == 0


def test_extract_only_changed_allowlisted_field():
    requested = json.loads(json.dumps(BASELINE))
    requested["heatings"]["heat0"]["opMode"] = 2

    changes = extract_mutable_changes(requested, BASELINE)

    assert changes == {"opMode": 2}


def test_extract_ignores_read_only_telemetry():
    requested = json.loads(json.dumps(BASELINE))
    requested["heatings"]["heat0"]["on"] = True

    with pytest.raises(FairlandCloudProtocolError):
        extract_mutable_changes(requested, BASELINE)


def test_extract_rejects_unverified_values():
    requested = {"heatings": {"heat0": {"opMode": 3}}}
    with pytest.raises(FairlandCloudProtocolError):
        extract_mutable_changes(requested, BASELINE)

    requested = {"heatings": {"heat0": {"fanMode": 2}}}
    with pytest.raises(FairlandCloudProtocolError):
        extract_mutable_changes(requested, BASELINE)

    requested = {"heatings": {"heat0": {"sp": 410}}}
    with pytest.raises(FairlandCloudProtocolError):
        extract_mutable_changes(requested, BASELINE)


def test_build_shadow_update_uses_nested_envelope():
    payload = json.loads(build_shadow_update({"opMode": 4}))

    assert payload == {"state": {"desired": {"heatings": {"heat0": {"opMode": 4}}}}}
    assert "heatings-heat0" not in json.dumps(payload)


def test_build_shadow_update_can_correlate_acknowledgement():
    payload = json.loads(build_shadow_update({"sp": 350}, "request-123"))

    assert payload["clientToken"] == "request-123"
    assert payload["state"]["desired"]["heatings"]["heat0"] == {"sp": 350}


def test_mqtt_callback_ignores_unrelated_acknowledgement():
    dummy_credential = "not-a-real-credential"
    request_id = "expected"
    client = FairlandCloudClient(
        thing_id="thing",
        user_sub="user",
        client_secret=dummy_credential,
        refresh_token=dummy_credential,
    )
    client._get_client_token = request_id

    client._on_message(
        client._get_accepted_topic,
        json.dumps({"clientToken": "unrelated", "state": {}}).encode(),
    )
    assert not client._get_event.is_set()

    client._on_message(
        client._get_accepted_topic,
        json.dumps({"clientToken": request_id, "state": {}}).encode(),
    )
    assert client._get_event.is_set()


def test_external_reported_update_notifies_manager():
    dummy_credential = "not-a-real-credential"
    client = FairlandCloudClient(
        thing_id="thing",
        user_sub="user",
        client_secret=dummy_credential,
        refresh_token=dummy_credential,
    )
    notifications = []
    client.set_state_change_callback(lambda: notifications.append(True))

    client._on_message(
        client._update_accepted_topic,
        json.dumps(
            {"state": {"reported": {"heatings": {"heat0": {"sp": 360}}}}}
        ).encode(),
    )

    assert notifications == [True]


def test_external_schedule_update_notifies_manager():
    dummy_credential = "not-a-real-credential"
    client = FairlandCloudClient(
        thing_id="thing",
        user_sub="user",
        client_secret=dummy_credential,
        refresh_token=dummy_credential,
    )
    notifications = []
    client.set_state_change_callback(lambda: notifications.append(True))

    client._on_message(
        client._schedule_update_accepted_topic,
        json.dumps(SCHEDULE_DOCUMENT).encode(),
    )

    assert notifications == [True]


def test_schedule_callback_correlates_client_token():
    dummy_credential = "not-a-real-credential"
    client = FairlandCloudClient(
        thing_id="thing",
        user_sub="user",
        client_secret=dummy_credential,
        refresh_token=dummy_credential,
    )
    request_id = "schedule-request"
    client._schedule_get_client_token = request_id

    unrelated = {**SCHEDULE_DOCUMENT, "clientToken": "other"}
    client._on_message(
        client._schedule_get_accepted_topic,
        json.dumps(unrelated).encode(),
    )
    assert not client._schedule_get_event.is_set()

    expected = {**SCHEDULE_DOCUMENT, "clientToken": request_id}
    client._on_message(
        client._schedule_get_accepted_topic,
        json.dumps(expected).encode(),
    )
    assert client._schedule_get_event.is_set()


def test_schedule_document_validates_all_five_slots():
    schedule = normalize_schedule_document(SCHEDULE_DOCUMENT)

    assert len(schedule) == 5
    assert schedule["event0"] == {
        "active": True,
        "turnOn": {"hr": 12, "min": 0},
        "turnOff": {"hr": 23, "min": 59},
        "rept": 127,
    }


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("event4",), None),
        (("event0", "active"), 1),
        (("event0", "turnOn", "hr"), 24),
        (("event0", "turnOff", "min"), 60),
        (("event0", "rept"), 128),
    ],
)
def test_schedule_document_rejects_invalid_values(path, value):
    document = json.loads(json.dumps(SCHEDULE_DOCUMENT))
    target = document["state"]["reported"]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    with pytest.raises(FairlandCloudProtocolError):
        normalize_schedule_document(document)


def test_reported_requires_fairland_heat_path():
    assert FairlandCloudClient._reported({"state": {"reported": BASELINE}}) == BASELINE

    with pytest.raises(FairlandCloudProtocolError):
        FairlandCloudClient._reported({"state": {"reported": {}}})


def test_matches_requested_reported_values():
    assert FairlandCloudClient._matches(BASELINE, {"opMode": 0, "sp": 330})
    assert not FairlandCloudClient._matches(BASELINE, {"opMode": 2})


def test_convergence_requires_reported_value_and_no_requested_delta():
    document = {"state": {"reported": BASELINE}}
    assert FairlandCloudClient._converged(document, {"opMode": 0})

    document["state"]["delta"] = {"heatings": {"heat0": {"opMode": 0}}}
    assert not FairlandCloudClient._converged(document, {"opMode": 0})

    document["state"]["delta"] = {"heatings": {"heat0": {"sp": 340}}}
    assert not FairlandCloudClient._converged(document, {"opMode": 0})


def test_failed_convergence_restores_baseline(monkeypatch):
    dummy_credential = "not-a-real-credential"
    simulated_failure = "simulated failure"
    client = FairlandCloudClient(
        thing_id="thing",
        user_sub="user",
        client_secret=dummy_credential,
        refresh_token=dummy_credential,
    )
    client._last_reported = BASELINE
    published = []

    monkeypatch.setattr(client, "get_status", lambda: BASELINE)
    monkeypatch.setattr(client, "_ensure_connection", lambda: None)
    monkeypatch.setattr(client, "_publish_changes", published.append)

    def wait(changes):
        if changes == {"opMode": 2}:
            raise FairlandCloudProtocolError(simulated_failure)
        return BASELINE

    monkeypatch.setattr(client, "_wait_for_convergence", wait)
    requested = {"heatings": {"heat0": {"opMode": 2}}}

    with pytest.raises(FairlandCloudProtocolError):
        client.set_status(requested)

    assert published == [{"opMode": 2}, {"opMode": 0}]


def test_fresh_baseline_does_not_turn_stale_fields_into_commands(monkeypatch):
    dummy_credential = "not-a-real-credential"
    client = FairlandCloudClient(
        thing_id="thing",
        user_sub="user",
        client_secret=dummy_credential,
        refresh_token=dummy_credential,
    )
    client._last_reported = BASELINE
    fresh = json.loads(json.dumps(BASELINE))
    fresh["heatings"]["heat0"]["sp"] = 340
    published = []

    monkeypatch.setattr(client, "get_status", lambda: fresh)
    monkeypatch.setattr(client, "_ensure_connection", lambda: None)
    monkeypatch.setattr(client, "_publish_changes", published.append)
    monkeypatch.setattr(client, "_wait_for_convergence", lambda _changes: fresh)

    requested = {"heatings": {"heat0": {"opMode": 2}}}
    client.set_status(requested)

    assert published == [{"opMode": 2}]


@pytest.mark.parametrize(
    "transient_error",
    [FairlandCloudError("transient token failure"), FairlandCloudAuthError("auth")],
)
def test_non_protocol_failure_after_publish_still_restores_baseline(
    monkeypatch, transient_error
):
    dummy_credential = "not-a-real-credential"
    client = FairlandCloudClient(
        thing_id="thing",
        user_sub="user",
        client_secret=dummy_credential,
        refresh_token=dummy_credential,
    )
    client._last_reported = BASELINE
    published = []

    monkeypatch.setattr(client, "get_status", lambda: BASELINE)
    monkeypatch.setattr(client, "_ensure_connection", lambda: None)
    monkeypatch.setattr(client, "_publish_changes", published.append)

    def wait(changes):
        if changes == {"opMode": 2}:
            raise transient_error
        return BASELINE

    monkeypatch.setattr(client, "_wait_for_convergence", wait)
    requested = {"heatings": {"heat0": {"opMode": 2}}}

    with pytest.raises(type(transient_error)):
        client.set_status(requested)

    assert published == [{"opMode": 2}, {"opMode": 0}]


def test_metrics_summary_filters_heat0_and_rolling_window():
    now_ms = 200_000_000
    cutoff_ms = now_ms - (24 * 60 * 60 * 1000)
    document = {
        "metrics": {
            "activeTime": {
                "columns": ["key", "time", "active", "state", "aux"],
                "rows": [
                    ["heat0", cutoff_ms - 1, 7_200_000, True, 1],
                    ["conn", cutoff_ms + 1, 3_600_000, True, 1],
                    ["heat0", cutoff_ms + 1, 3_600_000, True, 1],
                ],
            }
        }
    }

    assert summarize_metrics_document(document, now_ms) == {
        "heatingActiveSeconds24h": 3600.0,
        "heatingDutyCycle24h": 4.17,
    }
