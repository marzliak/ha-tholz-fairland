"""Setpoint policy and command-boundary regressions with no real transport."""

from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.tholz_fairland.cloud.client import (
    FairlandCloudClient,
    FairlandCloudProtocolError,
    extract_mutable_changes,
)
from custom_components.tholz_fairland.cloud.temperature_policy import (
    FairlandTemperatureError,
    temperature_policy,
)
from custom_components.tholz_fairland.entities.heating.heating_water_heater import (
    HeatingWaterHeater,
)
from custom_components.tholz_fairland.entities.heating.heating_fan_mode_select import (
    HeatingFanModeSelect,
)


def _heat(**changes):
    return {"type": 7, "opMode": 2, "sp": 330, "step": 10, "fanMode": 1, **changes}


def _reported(**changes):
    return {"heatings": {"heat0": _heat(**changes)}}


@pytest.mark.parametrize(
    ("mode", "minimum", "maximum"),
    [(0, 120, 400), (2, 120, 400), (4, 180, 400), (5, 120, 300)],
)
def test_mode_boundaries_and_exact_range(mode, minimum, maximum):
    policy = temperature_policy(_heat(opMode=mode))
    assert (policy.minimum, policy.maximum, policy.step) == (minimum, maximum, 10)
    for raw in range(100, 421):
        expected = minimum <= raw <= maximum and (raw - minimum) % 10 == 0
        if expected:
            assert policy.validate_raw(raw) == raw
            assert policy.to_raw(raw / 10) == raw
        else:
            with pytest.raises(FairlandTemperatureError):
                policy.validate_raw(raw)


@pytest.mark.parametrize(
    "value",
    [
        True,
        False,
        "20",
        None,
        float("nan"),
        float("inf"),
        float("-inf"),
        20.04,
        20.05,
        Decimal("20.0000000000000000000000000000000001"),
    ],
)
def test_no_rounding_or_invalid_temperature(value):
    with pytest.raises(FairlandTemperatureError):
        temperature_policy(_heat()).to_raw(value)


@pytest.mark.parametrize("value", [True, False, 200.0, "200", None])
def test_raw_value_must_be_an_integer_not_bool(value):
    with pytest.raises(FairlandTemperatureError):
        temperature_policy(_heat()).validate_raw(value)


def test_step_precedence_and_reported_restrictions():
    policy = temperature_policy(
        _heat(minSp=185, maxSp=365, stepSp=5, step=10, opMode=4)
    )
    assert (policy.minimum, policy.maximum, policy.step) == (185, 365, 5)
    assert policy.to_raw(18.5) == 185
    assert policy.to_raw(19) == 190
    with pytest.raises(FairlandTemperatureError):
        policy.to_raw(18.6)


def test_step_grid_is_anchored_at_effective_minimum():
    policy = temperature_policy(_heat(minSp=185, stepSp=10, opMode=4))
    assert policy.to_raw(18.5) == 185
    assert policy.to_raw(19.5) == 195
    with pytest.raises(FairlandTemperatureError):
        policy.to_raw(19)


def test_reported_limits_never_expand_verified_mode_range():
    policy = temperature_policy(_heat(minSp=50, maxSp=500, opMode=5))
    assert (policy.minimum, policy.maximum) == (120, 300)


def test_missing_step_preserves_explicit_legacy_default():
    state = _heat()
    state.pop("step")
    assert temperature_policy(state).step == 10


@pytest.mark.parametrize("field", ["stepSp", "step", "minSp", "maxSp"])
@pytest.mark.parametrize("value", [None, True, False, 0, -1, "10", 10.0, float("nan")])
def test_malformed_present_capability_fails_closed(field, value):
    state = _heat(**{field: value})
    with pytest.raises(FairlandTemperatureError):
        temperature_policy(state)


@pytest.mark.parametrize(
    "changes",
    [
        {"opMode": 99},
        {"opMode": True},
        {"opMode": 2.0},
        {"type": 15},
        {"type": 16},
        {"type": 17},
        {"type": 7.0},
        {"minSp": 390, "maxSp": 200},
        {"minSp": 410},
        {"maxSp": 100},
        {"stepSp": 1000},
    ],
)
def test_unknown_modes_models_and_inconsistent_capabilities_are_not_added(changes):
    with pytest.raises(FairlandTemperatureError):
        temperature_policy(_heat(**changes))


def _command_client(monkeypatch, cached, fresh):
    client = FairlandCloudClient("synthetic-device")
    client._last_reported = deepcopy(cached)
    calls = {"published": [], "read": 0}

    def get_status():
        calls["read"] += 1
        return deepcopy(fresh)

    monkeypatch.setattr(client, "get_status", get_status)
    monkeypatch.setattr(client, "_ensure_connection", lambda: None)
    monkeypatch.setattr(client, "_publish_changes", calls["published"].append)
    monkeypatch.setattr(client, "_wait_for_convergence", lambda _changes: fresh)
    return client, calls


@pytest.mark.parametrize(
    ("fresh_changes", "requested"),
    [
        ({"opMode": 5}, {"sp": 340}),
        ({"opMode": 4}, {"sp": 170}),
        ({"stepSp": 20}, {"sp": 350}),
        ({"stepSp": 0}, {"sp": 340}),
        ({"opMode": 99}, {"sp": 340}),
        ({"type": 15}, {"sp": 340}),
        ({"maxSp": 335}, {"sp": 340}),
    ],
)
def test_fresh_capabilities_reject_invalid_command_before_any_publish(
    monkeypatch, fresh_changes, requested
):
    client, calls = _command_client(
        monkeypatch, _reported(), _reported(**fresh_changes)
    )
    with pytest.raises(FairlandCloudProtocolError):
        client.set_status({"heatings": {"heat0": requested}})
    assert calls == {"published": [], "read": 1}


def test_combined_command_validates_against_requested_mode(monkeypatch):
    client, calls = _command_client(
        monkeypatch, _reported(opMode=0), _reported(opMode=5)
    )
    client.set_status({"heatings": {"heat0": {"opMode": 4, "sp": 180}}})
    assert calls["published"] == [{"opMode": 4, "sp": 180}]


def test_combined_command_rejects_target_that_only_old_mode_accepts(monkeypatch):
    client, calls = _command_client(monkeypatch, _reported(), _reported())
    with pytest.raises(FairlandCloudProtocolError):
        client.set_status({"heatings": {"heat0": {"opMode": 5, "sp": 340}}})
    assert not calls["published"]


def test_mode_only_never_adds_or_normalizes_setpoint(monkeypatch):
    cached = _reported(opMode=2, sp=350)
    client, calls = _command_client(
        monkeypatch, cached, _reported(opMode=2, sp=360, stepSp=0)
    )
    requested = deepcopy(cached)
    requested["heatings"]["heat0"]["opMode"] = 5
    client.set_status(requested)
    assert calls["published"] == [{"opMode": 5}]


def test_fresh_safe_mode_can_accept_an_intent_from_an_older_mode(monkeypatch):
    client, calls = _command_client(
        monkeypatch, _reported(opMode=4), _reported(opMode=5)
    )
    client.set_status({"heatings": {"heat0": {"sp": 150}}})
    assert calls["published"] == [{"sp": 150}]


def test_off_setpoint_does_not_turn_on_equipment(monkeypatch):
    client, calls = _command_client(
        monkeypatch, _reported(opMode=0), _reported(opMode=0)
    )
    client.set_status({"heatings": {"heat0": {"sp": 340}}})
    assert calls["published"] == [{"sp": 340}]


def test_temperature_failure_preserves_exact_baseline_rollback(monkeypatch):
    cached = _reported(sp=330)
    fresh = _reported(sp=320)
    client, calls = _command_client(monkeypatch, cached, fresh)

    def converge(changes):
        if changes == {"sp": 340}:
            message = "synthetic timeout"
            raise FairlandCloudProtocolError(message)
        return fresh

    monkeypatch.setattr(client, "_wait_for_convergence", converge)
    with pytest.raises(FairlandCloudProtocolError):
        client.set_status({"heatings": {"heat0": {"sp": 340}}})
    assert calls["published"] == [{"sp": 340}, {"sp": 320}]


def test_equal_boolean_cannot_hide_behind_unchanged_mode():
    with pytest.raises(FairlandCloudProtocolError):
        extract_mutable_changes(
            {"heatings": {"heat0": {"opMode": False, "sp": 340}}}, _reported(opMode=0)
        )


class _Manager:
    def __init__(self):
        self.payloads = []

    async def set_status(self, payload):
        self.payloads.append(payload)
        return payload


def _entity(state, manager):
    return HeatingWaterHeater(
        None,
        SimpleNamespace(data={"name": "Synthetic"}, entry_id="test"),
        manager,
        {},
        ["heatings", "heat0"],
        state,
    )


@pytest.mark.parametrize(
    ("mode", "minimum", "maximum"), [(0, 12, 40), (2, 12, 40), (4, 18, 40), (5, 12, 30)]
)
def test_entity_and_transport_expose_the_same_policy(mode, minimum, maximum):
    entity = _entity(_heat(opMode=mode, stepSp=5), _Manager())
    assert (entity.min_temp, entity.max_temp, entity.target_temperature_step) == (
        minimum,
        maximum,
        0.5,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "temperature", [True, float("nan"), float("inf"), 17, 34.04, 34.5]
)
async def test_entity_refuses_invalid_request_without_calling_manager(temperature):
    manager = _Manager()
    entity = _entity(_heat(opMode=4), manager)
    with pytest.raises(HomeAssistantError):
        await entity.async_set_temperature(temperature=temperature)
    assert manager.payloads == []


@pytest.mark.asyncio
async def test_entity_does_not_mutate_state_or_silently_round():
    state = _heat(opMode=5, sp=250, stepSp=5)
    manager = _Manager()
    entity = _entity(state, manager)
    await entity.async_set_temperature(temperature=25.5)
    assert state["sp"] == 250
    assert manager.payloads[0]["heatings"]["heat0"]["sp"] == 255


def test_invalid_capability_is_not_exposed_as_invented_limits():
    entity = _entity(_heat(stepSp=0), _Manager())
    assert entity.min_temp is None
    assert entity.max_temp is None
    assert entity.target_temperature_step is None


@pytest.mark.parametrize("value", [None, True, False, 0, -1, "10", 10.0, 1000])
def test_valid_step_sp_does_not_hide_malformed_legacy_step(value):
    with pytest.raises(FairlandTemperatureError):
        temperature_policy(_heat(stepSp=5, step=value))


@pytest.mark.parametrize(
    ("cached", "fresh", "intent"),
    [
        ({"sp": 330}, {"sp": 320}, {"sp": 330}),
        ({"opMode": 2}, {"opMode": 4}, {"opMode": 2}),
        ({"fanMode": 1}, {"fanMode": 0}, {"fanMode": 1}),
    ],
)
def test_explicit_intent_is_not_dropped_against_stale_cache(
    monkeypatch, cached, fresh, intent
):
    client, calls = _command_client(
        monkeypatch, _reported(**cached), _reported(**fresh)
    )
    client.set_status({"heatings": {"heat0": intent}})
    assert calls["published"] == [intent]


def test_true_noop_is_confirmed_by_fresh_read_without_publish(monkeypatch):
    client, calls = _command_client(monkeypatch, _reported(), _reported())
    assert client.set_status({"heatings": {"heat0": {"sp": 330}}}) == _reported()
    assert calls == {"published": [], "read": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("control", "argument", "intent"),
    [
        ("temperature", 34, {"sp": 340}),
        ("mode", "off", {"opMode": 0}),
        ("fan", "Silent", {"fanMode": 0}),
    ],
)
async def test_stale_entity_cannot_replay_unrequested_fields(
    monkeypatch, control, argument, intent
):
    client, calls = _command_client(
        monkeypatch,
        _reported(opMode=4, sp=350, fanMode=0),
        _reported(opMode=4, sp=360, fanMode=1),
    )

    class Bridge:
        async def set_status(self, payload):
            return client.set_status(payload)

    manager = Bridge()
    state = _heat(opMode=2, sp=330, fanMode=1)
    entity = _entity(state, manager)
    if control == "temperature":
        await entity.async_set_temperature(temperature=argument)
    elif control == "mode":
        await entity.async_set_operation_mode(argument)
    else:
        select = HeatingFanModeSelect(
            None,
            SimpleNamespace(data={"name": "Synthetic"}, entry_id="test"),
            manager,
            {},
            ["heatings", "heat0"],
            state,
        )
        await select.async_select_option(argument)
    assert calls["published"] == [intent]
    assert state == _heat(opMode=2, sp=330, fanMode=1)
