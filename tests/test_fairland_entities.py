import copy
from types import SimpleNamespace

import pytest

from custom_components.tholz_fairland.entities.cloud.cloud_metrics_sensor import (
    get_cloud_metric_sensors,
)
from custom_components.tholz_fairland.entities.cloud.cloud_schedule_sensor import (
    get_cloud_schedule_sensors,
)
from custom_components.tholz_fairland.entities.header.header_binary_sensor import (
    get_header_binary_sensors,
)
from custom_components.tholz_fairland.entities.heating.heating_binary_sensor import (
    get_heating_binary_sensors,
)
from custom_components.tholz_fairland.entities.heating.heating_fan_mode_select import (
    HeatingFanModeSelect,
    get_heating_fan_mode_selects,
)
from custom_components.tholz_fairland.entities.heating.heating_water_heater import (
    FAIRLAND_STATE_AUTOMATIC,
    FAIRLAND_STATE_COLD,
    FAIRLAND_STATE_HEAT,
    HeatingWaterHeater,
    get_heating_water_heaters,
)
from custom_components.tholz_fairland.entities.heating.heating_temperature_sensor import (
    get_heating_temperature_sensors,
)


@pytest.fixture
def reported():
    return {
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


class FakeManager:
    def __init__(self, data):
        self.data = data
        self.payloads = []

    async def get_status(self):
        return self.data

    async def set_status(self, payload):
        self.payloads.append(payload)
        result = copy.deepcopy(self.data)
        result["heatings"]["heat0"].update(payload["heatings"]["heat0"])
        self.data = result
        return result


def _entry():
    return SimpleNamespace(
        entry_id="entry-1",
        data={"name": "Piscina"},
    )


def _water_heater(manager, reported):
    return HeatingWaterHeater(
        None,
        _entry(),
        manager,
        {},
        ["heatings", "heat0"],
        reported["heatings"]["heat0"],
    )


def test_fairland_water_heater_exposes_verified_modes_and_ranges(reported):
    entity = _water_heater(FakeManager(reported), reported)

    assert entity.operation_list == [
        "off",
        FAIRLAND_STATE_AUTOMATIC,
        FAIRLAND_STATE_HEAT,
        FAIRLAND_STATE_COLD,
    ]
    assert entity.current_operation == "off"
    assert entity.current_temperature == 23.0
    assert entity.target_temperature == 33.0
    assert entity.target_temperature_step == 1.0
    assert entity.min_temp == 12.0
    assert entity.max_temp == 40.0


@pytest.mark.asyncio
async def test_mode_command_does_not_mutate_cached_reported_state(reported):
    manager = FakeManager(reported)
    entity = _water_heater(manager, reported)

    await entity.async_set_operation_mode(FAIRLAND_STATE_HEAT)

    assert reported["heatings"]["heat0"]["opMode"] == 0
    assert manager.payloads[0]["heatings"]["heat0"]["opMode"] == 4
    assert entity.current_operation == FAIRLAND_STATE_HEAT


@pytest.mark.asyncio
async def test_setpoint_command_uses_tenths_of_degree(reported):
    manager = FakeManager(reported)
    entity = _water_heater(manager, reported)

    await entity.async_set_temperature(temperature=34.0)

    assert reported["heatings"]["heat0"]["sp"] == 330
    assert manager.payloads[0]["heatings"]["heat0"]["sp"] == 340
    assert entity.target_temperature == 34.0


@pytest.mark.asyncio
async def test_fan_command_supports_only_silent_and_intelligent(reported):
    manager = FakeManager(reported)
    entity = HeatingFanModeSelect(
        None,
        _entry(),
        manager,
        {},
        ["heatings", "heat0"],
        reported["heatings"]["heat0"],
    )

    assert entity.options == ["Silent", "Intelligent"]
    await entity.async_select_option("Silent")

    assert reported["heatings"]["heat0"]["fanMode"] == 1
    assert manager.payloads[0]["heatings"]["heat0"]["fanMode"] == 0
    assert entity.current_option == "Silent"


def test_cloud_metrics_create_two_cached_sensors(reported):
    reported["metricsSummary"] = {
        "heatingActiveSeconds24h": 3600.0,
        "heatingDutyCycle24h": 4.17,
    }

    entities = get_cloud_metric_sensors(None, _entry(), FakeManager(reported), reported)

    assert len(entities) == 2
    assert [entity.native_value for entity in entities] == [3600.0, 4.17]


def test_cloud_schedule_creates_five_slots_and_diagnostics(reported):
    reported["schedule"] = {
        f"event{index}": {
            "active": index == 0,
            "turnOn": {"hr": 12, "min": index},
            "turnOff": {"hr": 23, "min": 59},
            "rept": 127,
        }
        for index in range(5)
    }
    reported["scheduleVersion"] = 42
    reported["scheduleTimestamp"] = 1_777_777_777

    entities = get_cloud_schedule_sensors(
        None, _entry(), FakeManager(reported), reported
    )

    assert len(entities) == 8
    assert entities[0].native_value == "Ativa · 12:00-23:59"
    assert entities[0].extra_state_attributes["repeat_description"] == "Todos os dias"
    assert entities[5].native_value == 1
    assert entities[6].native_value == 42
    assert entities[7].native_value.isoformat() == "2026-05-03T03:09:37+00:00"


def test_fairland_entity_inventory_includes_live_telemetry(reported):
    entry = _entry()
    manager = FakeManager(reported)

    temperatures = get_heating_temperature_sensors(None, entry, manager, reported)
    operations = get_heating_binary_sensors(None, entry, manager, reported)
    connectivity = get_header_binary_sensors(None, entry, manager, reported)
    water_heaters = get_heating_water_heaters(None, entry, manager, reported)
    fan_modes = get_heating_fan_mode_selects(None, entry, manager, reported)

    assert len(temperatures) == 3
    assert [entity.state for entity in temperatures] == [18.0, 24.0, 23.0]
    assert len(operations) == 1
    assert operations[0].is_on is False
    assert any(entity.name.endswith("Online") for entity in connectivity)
    assert len(water_heaters) == 1
    assert len(fan_modes) == 1
