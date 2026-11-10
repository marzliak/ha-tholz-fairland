"""Inert end-to-end diagnostic regressions; not physical-device acceptance."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from test_command_intent import harness as command_harness

from custom_components.tholz_fairland.cloud import client as client_module
from custom_components.tholz_fairland.cloud.client import (
    ERR_GET_TIMEOUT,
    ERR_ROLLBACK_FAILED,
    ERR_UPDATE_CONVERGENCE,
    ERR_WRITE_PATH,
    FairlandCloudProtocolError,
)

harness = command_harness


class CanonicalClock:
    """Only the client's clock is replaced; HA/asyncio clocks stay untouched."""

    def __init__(self):
        self.now = 100.0
        self.sleeps = []
        self.reads = 0

    def monotonic(self):
        self.reads += 1
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def prepare(harness, monkeypatch):
    controller, client, *_ = harness
    controller.state["heatings"]["heat0"]["sp"] = 320
    client._convergence_timeout = 1.0
    clock = CanonicalClock()
    monkeypatch.setattr(client_module, "time", clock)
    return controller, client, clock


def assert_baseline_controls(controller):
    heat = controller.state["heatings"]["heat0"]
    assert heat["sp"] == 320
    assert heat["opMode"] == 0
    assert heat["fanMode"] == 1
    assert heat["on"] is False


def _write_controls(client, controls):
    return client.set_status({"heatings": {"heat0": controls}})


def test_flat_payload_stays_invalid_before_io(harness, monkeypatch):
    controller, client, clock = prepare(harness, monkeypatch)
    with pytest.raises(FairlandCloudProtocolError, match=ERR_WRITE_PATH):
        client.set_status({"sp": 330})
    assert controller.published == []
    assert controller.reads == 0
    assert clock.reads == 0
    assert client.get_last_confirmation_diagnostics() is None


def test_success_records_detached_readonly_diagnostics(harness, monkeypatch):
    controller, client, clock = prepare(harness, monkeypatch)
    assert client.get_last_confirmation_diagnostics() is None
    result = _write_controls(client, {"sp": 330})
    assert result["heatings"]["heat0"]["sp"] == 330
    before = (controller.reads, deepcopy(controller.published), clock.reads)
    diagnostic = client.get_last_confirmation_diagnostics()
    assert diagnostic["outcome"] == "confirmed"
    assert diagnostic["publish"] == {"apply": "accepted", "restore": "not_requested"}
    assert diagnostic["primary_error"] is None
    assert diagnostic["capture_errors"] == 0
    assert len(diagnostic["phases"]) == 1
    first = diagnostic["phases"][0]["first"]
    assert first["reported_matches"] is True
    assert first["delta_empty"] is True
    assert first["reason"] == "confirmed"
    assert clock.sleeps == []
    diagnostic["phases"].clear()
    assert len(client.get_last_confirmation_diagnostics()["phases"]) == 1
    assert (controller.reads, controller.published, clock.reads) == before


def test_mismatch_records_apply_and_restore_without_replacing_error(
    harness, monkeypatch
):
    controller, client, clock = prepare(harness, monkeypatch)
    controller.apply_writes = False
    with pytest.raises(FairlandCloudProtocolError, match=ERR_UPDATE_CONVERGENCE):
        _write_controls(client, {"sp": 330})
    diagnostic = client.get_last_confirmation_diagnostics()
    assert diagnostic["outcome"] == "restored_after_failure"
    assert diagnostic["primary_error"] == "ERR_UPDATE_CONVERGENCE"
    assert diagnostic["restore_error"] is None
    apply, restore = diagnostic["phases"]
    assert (apply["phase"], apply["outcome"]) == ("apply", "timeout")
    assert (restore["phase"], restore["outcome"]) == ("restore", "confirmed")
    assert apply["sample_count"] == 2
    assert apply["first"]["reason"] == "reported_mismatch"
    assert apply["ever_reported_match"] is False
    assert apply["ever_delta_empty"] is True
    assert restore["first"]["reason"] == "confirmed"
    assert controller.published == [{"sp": 330}, {"sp": 320}]
    assert clock.sleeps == [0.5, 0.5]
    assert_baseline_controls(controller)


@pytest.mark.parametrize("foreign", [False, True])
def test_global_delta_still_blocks_even_matching_reported(
    harness, monkeypatch, foreign
):
    controller, client, _ = prepare(harness, monkeypatch)
    original_publish = controller.publish
    needle = "PRIVATE_UNKNOWN_DELTA_KEY_DO_NOT_RETAIN"

    def publish(**kwargs):
        result = original_publish(**kwargs)
        if controller.published[-1] == {"sp": 330}:
            controller.delta = (
                {needle: {"private_value": needle}}
                if foreign
                else {"heatings": {"heat0": {"sp": 330}}}
            )
        else:
            controller.delta = {}
        return result

    monkeypatch.setattr(controller, "publish", publish)
    with pytest.raises(FairlandCloudProtocolError, match=ERR_UPDATE_CONVERGENCE):
        _write_controls(client, {"sp": 330})
    diagnostic = client.get_last_confirmation_diagnostics()
    apply = diagnostic["phases"][0]
    assert apply["ever_reported_match"] is True
    assert apply["ever_delta_empty"] is False
    assert apply["first"]["reason"] == "pending_delta"
    assert apply["first"]["reported_matches"] is True
    assert apply["first"]["delta_empty"] is False
    assert needle not in json.dumps(diagnostic, allow_nan=False)
    assert controller.published == [{"sp": 330}, {"sp": 320}]
    assert_baseline_controls(controller)


def test_restore_failure_keeps_both_phase_results_and_original_exception_chain(
    harness, monkeypatch
):
    controller, client, _ = prepare(harness, monkeypatch)
    original_publish = controller.publish

    def publish(**kwargs):
        result = original_publish(**kwargs)
        if controller.published[-1] == {"sp": 320}:
            controller.state["heatings"]["heat0"]["sp"] = 330
        controller.delta = {"heatings": {"heat0": {"sp": 320}}}
        return result

    monkeypatch.setattr(controller, "publish", publish)
    with pytest.raises(FairlandCloudProtocolError, match=ERR_ROLLBACK_FAILED) as caught:
        _write_controls(client, {"sp": 330})
    assert str(caught.value.__cause__) == ERR_UPDATE_CONVERGENCE
    diagnostic = client.get_last_confirmation_diagnostics()
    assert diagnostic["outcome"] == "restore_failed"
    assert diagnostic["primary_error"] == "ERR_UPDATE_CONVERGENCE"
    assert diagnostic["restore_error"] == "ERR_UPDATE_CONVERGENCE"
    assert [phase["outcome"] for phase in diagnostic["phases"]] == [
        "timeout",
        "timeout",
    ]
    assert controller.published == [{"sp": 330}, {"sp": 320}]


@pytest.mark.parametrize("pending", [False, True])
def test_noop_never_publishes_or_attempts_restoration(harness, monkeypatch, pending):
    controller, client, _ = prepare(harness, monkeypatch)
    if pending:
        controller.delta = {"heatings": {"heat0": {"sp": 320}}}
        with pytest.raises(FairlandCloudProtocolError, match=ERR_UPDATE_CONVERGENCE):
            _write_controls(client, {"sp": 320})
    else:
        _write_controls(client, {"sp": 320})
    diagnostic = client.get_last_confirmation_diagnostics()
    assert diagnostic["outcome"] == ("noop_not_confirmed" if pending else "confirmed")
    assert diagnostic["publish"] == {
        "apply": "not_requested",
        "restore": "not_requested",
    }
    assert len(diagnostic["phases"]) == 1
    assert diagnostic["phases"][0]["phase"] == "noop"
    assert controller.published == []
    assert_baseline_controls(controller)


@pytest.mark.parametrize("unknown_error", [False, True])
def test_read_error_is_not_invented_as_reported_mismatch_or_logged_raw(
    harness, monkeypatch, caplog, unknown_error
):
    controller, client, _ = prepare(harness, monkeypatch)
    original_shadow = controller.shadow
    needle = "PRIVATE_PROVIDER_ERROR_TEXT_DO_NOT_LOG"
    message = needle if unknown_error else ERR_GET_TIMEOUT

    def shadow():
        if controller.published == [{"sp": 330}]:
            raise FairlandCloudProtocolError(message)
        return original_shadow()

    monkeypatch.setattr(client, "_request_shadow", shadow)
    with pytest.raises(FairlandCloudProtocolError) as caught:
        _write_controls(client, {"sp": 330})
    assert str(caught.value) == message
    diagnostic = client.get_last_confirmation_diagnostics()
    assert diagnostic["primary_error"] == (
        "UNCLASSIFIED" if unknown_error else "ERR_GET_TIMEOUT"
    )
    assert diagnostic["phases"][0]["outcome"] == "error"
    assert diagnostic["phases"][0]["sample_count"] == 0
    assert diagnostic["phases"][0]["first"] is None
    assert needle not in json.dumps(diagnostic, allow_nan=False)
    assert needle not in caplog.text
    assert controller.published == [{"sp": 330}, {"sp": 320}]
    assert_baseline_controls(controller)


@pytest.mark.parametrize("failure", ["factory", "observe", "finish"])
@pytest.mark.parametrize("succeeds", [False, True])
def test_recorder_failures_do_not_change_control_or_restore(
    harness, monkeypatch, failure, succeeds
):
    controller, client, clock = prepare(harness, monkeypatch)
    controller.apply_writes = succeeds

    def broken(*_args, **_kwargs):
        raise RuntimeError

    if failure == "factory":
        monkeypatch.setattr(client_module, "ConvergenceTrace", broken)
    else:
        monkeypatch.setattr(client_module.ConvergenceTrace, failure, broken)
    if succeeds:
        result = _write_controls(client, {"sp": 330})
        assert result["heatings"]["heat0"]["sp"] == 330
        assert controller.published == [{"sp": 330}]
        assert controller.reads == 2
        assert clock.sleeps == []
    else:
        with pytest.raises(FairlandCloudProtocolError, match=ERR_UPDATE_CONVERGENCE):
            _write_controls(client, {"sp": 330})
        assert controller.published == [{"sp": 330}, {"sp": 320}]
        assert controller.reads == 4
        assert clock.sleeps == [0.5, 0.5]
        assert_baseline_controls(controller)
    assert client.get_last_confirmation_diagnostics()["capture_errors"] > 0


def test_logger_failure_does_not_mask_primary_error_or_restore(harness, monkeypatch):
    controller, client, _ = prepare(harness, monkeypatch)
    controller.apply_writes = False

    def broken(*_args, **_kwargs):
        raise RuntimeError

    monkeypatch.setattr(client_module._LOGGER, "warning", broken)
    with pytest.raises(FairlandCloudProtocolError, match=ERR_UPDATE_CONVERGENCE):
        _write_controls(client, {"sp": 330})
    assert controller.published == [{"sp": 330}, {"sp": 320}]
    assert (
        client.get_last_confirmation_diagnostics()["outcome"]
        == "restored_after_failure"
    )
    assert_baseline_controls(controller)


def test_transaction_records_do_not_accumulate_or_reuse_old_reference(
    harness, monkeypatch
):
    controller, client, _ = prepare(harness, monkeypatch)
    first_refs = set()
    for target in (330, 340, 350, 340, 330):
        _write_controls(client, {"sp": target})
        diagnostic = client.get_last_confirmation_diagnostics()
        assert len(diagnostic["phases"]) == 1
        assert diagnostic["transaction_ref"] not in first_refs
        first_refs.add(diagnostic["transaction_ref"])
        assert diagnostic["phases"][0]["requested"] == {"sp": target}
    assert len(controller.published) == 5


def test_diagnostic_setup_failure_cannot_reuse_a_previous_transaction(
    harness, monkeypatch
):
    controller, client, _ = prepare(harness, monkeypatch)
    _write_controls(client, {"sp": 330})
    assert client.get_last_confirmation_diagnostics() is not None

    def broken(*_args, **_kwargs):
        raise OSError

    monkeypatch.setattr(client_module.secrets, "token_hex", broken)
    result = _write_controls(client, {"sp": 340})
    assert result["heatings"]["heat0"]["sp"] == 340
    assert controller.published == [{"sp": 330}, {"sp": 340}]
    assert client.get_last_confirmation_diagnostics() is None


@pytest.mark.parametrize("succeeds", [True, False])
def test_transient_setup_failure_stays_unavailable_until_next_transaction(
    harness, monkeypatch, succeeds
):
    controller, client, clock = prepare(harness, monkeypatch)
    calls = []

    def first_call_fails(byte_count):
        calls.append(byte_count)
        if len(calls) == 1:
            raise OSError
        return "0123456789abcdef"

    monkeypatch.setattr(
        client_module, "secrets", SimpleNamespace(token_hex=first_call_fails)
    )
    controller.apply_writes = succeeds
    if succeeds:
        result = _write_controls(client, {"sp": 330})
        assert result["heatings"]["heat0"]["sp"] == 330
        assert controller.published == [{"sp": 330}]
        assert controller.reads == 2
        assert clock.sleeps == []
    else:
        with pytest.raises(FairlandCloudProtocolError, match=ERR_UPDATE_CONVERGENCE):
            _write_controls(client, {"sp": 330})
        assert controller.published == [{"sp": 330}, {"sp": 320}]
        assert controller.reads == 4
        assert clock.sleeps == [0.5, 0.5]
        assert_baseline_controls(controller)
    assert calls == [8]
    assert client.get_last_confirmation_diagnostics() is None

    controller.apply_writes = True
    result = _write_controls(client, {"sp": 340})
    assert result["heatings"]["heat0"]["sp"] == 340
    assert calls == [8, 8]
    diagnostic = client.get_last_confirmation_diagnostics()
    assert diagnostic["outcome"] == "confirmed"
    assert diagnostic["publish"] == {"apply": "accepted", "restore": "not_requested"}
    assert diagnostic["capture_errors"] == 0
    assert diagnostic["phases"][0]["requested"] == {"sp": 340}


@pytest.mark.asyncio
async def test_real_entity_manager_client_path_records_confirmation(
    harness, monkeypatch
):
    controller, client, _clock = prepare(harness, monkeypatch)
    _controller, _client, manager, water, _fan = harness
    await manager.get_status()
    await water.async_set_temperature(temperature=33)
    assert controller.published == [{"sp": 330}]
    diagnostic = client.get_last_confirmation_diagnostics()
    assert diagnostic["phases"][0]["first"]["reason"] == "confirmed"
    assert diagnostic["outcome"] == "confirmed"
    assert controller.state["heatings"]["heat0"]["opMode"] == 0
    assert controller.state["heatings"]["heat0"]["on"] is False
