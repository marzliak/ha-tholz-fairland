import json

import pytest

from custom_components.tholz_fairland.cloud.client import (
    FairlandCloudClient,
    FairlandCloudProtocolError,
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
    requested = json.loads(json.dumps(BASELINE))
    requested["heatings"]["heat0"]["opMode"] = 2

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

    requested = json.loads(json.dumps(BASELINE))
    requested["heatings"]["heat0"]["opMode"] = 2
    client.set_status(requested)

    assert published == [{"opMode": 2}]


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
