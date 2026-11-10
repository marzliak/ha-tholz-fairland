"""Offline regressions for explicit command intent across the real entity/client path."""

import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.tholz_fairland.cloud.client import (
    ERR_INTEGER_FIELD,
    ERR_UPDATE_CONVERGENCE,
    ERR_WRITE_FIELDS,
    FairlandCloudClient,
    FairlandCloudProtocolError,
)
from custom_components.tholz_fairland.cloud.client_manager import FairlandClientManager
from custom_components.tholz_fairland.entities.heating.heating_fan_mode_select import (
    HeatingFanModeSelect,
)
from custom_components.tholz_fairland.entities.heating.heating_water_heater import (
    HeatingWaterHeater,
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


class Completed:
    def result(self, timeout=None):
        del timeout


class InertController:
    """Only the SDK transport boundary is mocked; no device/network is available."""

    def __init__(self):
        self.state = deepcopy(BASELINE)
        self.delta = {}
        self.published = []
        self.reads = 0
        self.client = None
        self.apply_writes = True

    def shadow(self):
        self.reads += 1
        return {
            "state": {"reported": deepcopy(self.state), "delta": deepcopy(self.delta)}
        }

    @staticmethod
    def schedule():
        return {
            "state": {
                "reported": {
                    f"event{index}": {
                        "active": False,
                        "turnOn": {"hr": 12, "min": 0},
                        "turnOff": {"hr": 23, "min": 59},
                        "rept": 127,
                    }
                    for index in range(5)
                }
            }
        }

    def publish(self, *, topic, payload, qos):
        del qos
        assert topic == self.client._update_topic
        document = json.loads(payload)
        change = document["state"]["desired"]["heatings"]["heat0"]
        self.published.append(deepcopy(change))
        if self.apply_writes:
            self.state["heatings"]["heat0"].update(change)
        # Exercise the real callback/token correlation and real convergence
        # reader, not a fabricated return from FairlandCloudClient.set_status.
        self.client._on_message(
            self.client._update_accepted_topic,
            json.dumps(
                {
                    "clientToken": document["clientToken"],
                    "state": {"reported": self.state},
                }
            ).encode(),
        )
        return Completed(), 1

    @staticmethod
    def disconnect():
        return Completed()


@pytest.fixture
def harness(monkeypatch):
    controller = InertController()
    client = FairlandCloudClient(
        thing_id="synthetic-heat-pump", auth_manager=SimpleNamespace()
    )
    controller.client = client
    client._connection = controller
    monkeypatch.setattr(client, "_ensure_connection", lambda: None)
    monkeypatch.setattr(client, "_request_shadow", controller.shadow)
    monkeypatch.setattr(client, "_get_metrics_summary", dict)
    monkeypatch.setattr(client, "_request_schedule", controller.schedule)
    manager = FairlandClientManager(client, polling_interval=30)
    entry = SimpleNamespace(entry_id="synthetic-entry", data={"name": "Pool"})
    water = HeatingWaterHeater(
        None,
        entry,
        manager,
        {},
        ["heatings", "heat0"],
        deepcopy(BASELINE["heatings"]["heat0"]),
    )
    fan = HeatingFanModeSelect(
        None,
        entry,
        manager,
        {},
        ["heatings", "heat0"],
        deepcopy(BASELINE["heatings"]["heat0"]),
    )
    yield controller, client, manager, water, fan
    client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command", "expected"),
    [("temperature", {"sp": 340}), ("mode", {"opMode": 2}), ("fan", {"fanMode": 1})],
)
async def test_stale_entity_sends_only_the_requested_field(harness, command, expected):
    controller, _client, manager, water, fan = harness
    await manager.get_status()
    # Another interface changed these after the entity's last update.
    controller.state["heatings"]["heat0"].update({"opMode": 4, "sp": 350, "fanMode": 0})
    before = deepcopy(controller.state["heatings"]["heat0"])
    if command == "temperature":
        await water.async_set_temperature(temperature=34.0)
    elif command == "mode":
        await water.async_set_operation_mode("automatic")
    else:
        await fan.async_select_option("Intelligent")
    assert controller.published == [expected]
    assert controller.state["heatings"]["heat0"] == {**before, **expected}


@pytest.mark.asyncio
async def test_cross_platform_concurrent_commands_keep_each_others_changes(harness):
    controller, _client, manager, water, fan = harness
    await manager.get_status()
    await asyncio.gather(
        water.async_set_operation_mode("heat"),
        fan.async_select_option("Silent"),
        water.async_set_temperature(temperature=34.0),
    )
    assert len(controller.published) == 3
    assert all(len(change) == 1 for change in controller.published)
    heat = controller.state["heatings"]["heat0"]
    assert (heat["opMode"], heat["sp"], heat["fanMode"]) == (4, 340, 0)
    assert controller.published == [{"opMode": 4}, {"fanMode": 0}, {"sp": 340}]


@pytest.mark.asyncio
async def test_value_equal_to_old_cache_is_not_discarded(harness):
    controller, client, manager, water, _fan = harness
    await manager.get_status()
    assert client._last_reported["heatings"]["heat0"]["sp"] == 330
    controller.state["heatings"]["heat0"]["sp"] = 350
    await water.async_set_temperature(temperature=33.0)
    assert controller.published == [{"sp": 330}]
    assert controller.state["heatings"]["heat0"]["sp"] == 330


@pytest.mark.asyncio
async def test_idempotent_command_sends_nothing_and_checks_convergence(harness):
    controller, _client, manager, water, _fan = harness
    await manager.get_status()
    reads = controller.reads
    await water.async_set_temperature(temperature=33.0)
    assert controller.published == []
    assert controller.reads >= reads + 2


def test_idempotent_command_cannot_hide_a_pending_delta(harness, monkeypatch):
    controller, client, _manager, _water, _fan = harness
    controller.delta = {"heatings": {"heat0": {"sp": 350}}}
    client._convergence_timeout = 0.005
    monkeypatch.setattr(
        "custom_components.tholz_fairland.cloud.client.time.sleep",
        lambda _seconds: None,
    )
    with pytest.raises(FairlandCloudProtocolError, match=ERR_UPDATE_CONVERGENCE):
        client.set_status({"heatings": {"heat0": {"sp": 330}}})
    assert controller.published == []
    assert controller.reads >= 2


@pytest.mark.parametrize(
    "intent", [{"sp": True}, {"sp": "330"}, {"opMode": False}, {"fanMode": True}]
)
def test_invalid_intent_is_rejected_before_transport(harness, intent):
    controller, client, _manager, _water, _fan = harness
    with pytest.raises(FairlandCloudProtocolError, match=ERR_INTEGER_FIELD):
        client.set_status({"heatings": {"heat0": intent}})
    assert controller.reads == 0
    assert controller.published == []


def test_full_cached_snapshot_is_not_a_command(harness):
    controller, client, _manager, _water, _fan = harness
    payload = deepcopy(BASELINE)
    payload["heatings"]["heat0"]["sp"] = 340
    with pytest.raises(FairlandCloudProtocolError, match=ERR_WRITE_FIELDS):
        client.set_status(payload)
    assert controller.reads == 0
    assert controller.published == []


@pytest.mark.parametrize("raw", [True, "330", float("nan"), float("inf")])
def test_invalid_restoration_baseline_is_never_published(harness, raw):
    controller, client, _manager, _water, _fan = harness
    controller.state["heatings"]["heat0"]["sp"] = raw
    with pytest.raises(FairlandCloudProtocolError):
        client.set_status({"heatings": {"heat0": {"sp": 340}}})
    assert controller.published == []


def test_failure_restores_fresh_baseline_not_the_old_cache(harness, monkeypatch):
    controller, client, _manager, _water, _fan = harness
    client._last_reported = deepcopy(BASELINE)
    controller.state["heatings"]["heat0"].update({"sp": 350, "opMode": 4, "fanMode": 0})
    original_wait = client._wait_for_convergence

    def fail_first(changes):
        if changes == {"sp": 340}:
            message = "synthetic convergence failure"
            raise FairlandCloudProtocolError(message)
        return original_wait(changes)

    monkeypatch.setattr(client, "_wait_for_convergence", fail_first)
    with pytest.raises(
        FairlandCloudProtocolError, match="synthetic convergence failure"
    ):
        client.set_status({"heatings": {"heat0": {"sp": 340}}})
    assert controller.published == [{"sp": 340}, {"sp": 350}]
    assert controller.state["heatings"]["heat0"]["opMode"] == 4
    assert controller.state["heatings"]["heat0"]["fanMode"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [True, "34", float("nan"), float("inf"), 10**1000])
async def test_invalid_temperature_input_never_reaches_the_transport(harness, raw):
    controller, _client, _manager, water, _fan = harness
    with pytest.raises(HomeAssistantError, match="Temperature must"):
        await water.async_set_temperature(temperature=raw)
    assert controller.reads == 0
    assert controller.published == []


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["t3", "sp"])
@pytest.mark.parametrize(
    "raw", [True, "230", float("nan"), float("inf"), 10**1000, None]
)
async def test_water_heater_never_exposes_invalid_temperature(harness, field, raw):
    _controller, _client, manager, water, _fan = harness
    await manager.get_status()
    water._state[field] = raw
    assert water.available
    value = water.current_temperature if field == "t3" else water.target_temperature
    assert value is None


@pytest.mark.asyncio
async def test_water_heater_preserves_valid_zero_readings(harness):
    _controller, _client, manager, water, _fan = harness
    await manager.get_status()
    water._state.update({"t3": 0, "sp": 0})
    assert water.available
    assert water.current_temperature == 0.0
    assert water.target_temperature == 0.0


@pytest.mark.asyncio
async def test_unavailable_water_heater_and_fan_do_not_present_old_state(harness):
    _controller, _client, manager, water, fan = harness
    await manager.get_status()
    assert water.available
    assert fan.available
    # Stop invalidates read freshness in the same way as integration unload.
    await manager.stop()
    assert not water.available
    assert water.current_temperature is None
    assert water.target_temperature is None
    assert water.current_operation is None
    assert not fan.available
    assert fan.current_option is None


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["temperature", "mode", "fan"])
async def test_confirmed_control_does_not_revert_on_entity_update(harness, command):
    controller, _client, manager, water, fan = harness
    await manager.get_status()
    read_at = manager._read_at
    if command == "temperature":
        await water.async_set_temperature(temperature=34.0)
        entity, attribute, expected = water, "target_temperature", 34.0
    elif command == "mode":
        await water.async_set_operation_mode("automatic")
        entity, attribute, expected = water, "current_operation", "automatic"
    else:
        await fan.async_select_option("Silent")
        entity, attribute, expected = fan, "current_option", "Silent"
    assert getattr(entity, attribute) == expected
    reads = controller.reads
    await entity.async_update()
    assert getattr(entity, attribute) == expected
    assert controller.reads == reads
    assert manager._read_at == read_at
    assert manager._read_data["heatings"]["heat0"] == BASELINE["heatings"]["heat0"]


@pytest.mark.asyncio
async def test_new_read_supersedes_the_confirmed_control(harness):
    controller, _client, manager, water, _fan = harness
    await manager.get_status()
    await water.async_set_temperature(temperature=34.0)
    await water.async_update()
    assert water.target_temperature == 34.0
    controller.state["heatings"]["heat0"]["sp"] = 350
    await manager._fetch_data()
    await water.async_update()
    assert water.target_temperature == 35.0
    assert not manager._confirmed_controls


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["temperature", "mode", "fan"])
async def test_commands_after_unload_raise_home_assistant_error(harness, command):
    controller, _client, manager, water, fan = harness
    await manager.stop()
    if command == "temperature":
        action = water.async_set_temperature(temperature=34.0)
    elif command == "mode":
        action = water.async_set_operation_mode("automatic")
    else:
        action = fan.async_select_option("Silent")
    with pytest.raises(HomeAssistantError, match="manager is closed"):
        await action
    assert controller.published == []
    assert controller.reads == 0
