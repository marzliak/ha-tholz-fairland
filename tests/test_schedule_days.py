"""Exhaustive, read-only coverage for heat0Schedule repeat days."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.tholz_fairland.entities.cloud.cloud_schedule_sensor import (
    decode_repeat_days,
    get_cloud_schedule_sensors,
)

# This oracle reads the seven wire positions as a binary string, independently
# of the production bit masks and bitwise decoder.
_WIRE_ORDER = (
    "Domingo",
    "Segunda-feira",
    "Terça-feira",
    "Quarta-feira",
    "Quinta-feira",
    "Sexta-feira",
    "Sábado",
)
_CALENDAR_ORDER = (*_WIRE_ORDER[1:], _WIRE_ORDER[0])
_INVALID_MASKS = [
    pytest.param(None, id="none"),
    pytest.param(False, id="false-is-not-zero"),
    pytest.param(True, id="true-is-not-saturday"),
    pytest.param(-1, id="negative"),
    pytest.param(128, id="eighth-bit"),
    pytest.param(255, id="all-eight-bits"),
    pytest.param(10**100, id="oversized-integer"),
    pytest.param("", id="empty-string"),
    pytest.param("0", id="zero-string"),
    pytest.param("32", id="weekday-string"),
    pytest.param("127", id="all-days-string"),
    pytest.param(0.0, id="zero-float"),
    pytest.param(32.0, id="weekday-float"),
    pytest.param(127.0, id="all-days-float"),
    pytest.param(1.5, id="fraction"),
    pytest.param(float("nan"), id="nan"),
    pytest.param(float("inf"), id="positive-infinity"),
    pytest.param(float("-inf"), id="negative-infinity"),
    pytest.param(1 + 0j, id="complex"),
    pytest.param(b"32", id="bytes"),
    pytest.param([], id="list"),
    pytest.param({}, id="dictionary"),
    pytest.param((32,), id="tuple"),
]


def _expected_days(mask):
    selected = {
        day
        for digit, day in zip(f"{mask:07b}", _WIRE_ORDER, strict=True)
        if digit == "1"
    }
    return [day for day in _CALENDAR_ORDER if day in selected]


def _expected_description(mask):
    if mask == 0:
        return "Nenhum dia"
    if mask == 127:
        return "Todos os dias"
    return ", ".join(_expected_days(mask))


def _manager(data):
    return SimpleNamespace(
        is_fresh=True,
        get_sensor_status=AsyncMock(return_value=deepcopy(data)),
        get_status=AsyncMock(
            side_effect=AssertionError("Entities must use the cached sensor read")
        ),
        set_status=AsyncMock(
            side_effect=AssertionError("Schedule writes are forbidden")
        ),
    )


def _payload(mask):
    return {
        "schedule": {
            f"event{index}": {
                "active": index == 0,
                "turnOn": {"hr": 12, "min": index},
                "turnOff": {"hr": 23, "min": 59},
                "rept": mask,
            }
            for index in range(5)
        },
        "scheduleVersion": 42,
        "scheduleTimestamp": 1_777_777_777,
    }


def _entities(data, manager):
    entry = SimpleNamespace(entry_id="schedule-entry", data={"name": "Aquecedor"})
    return get_cloud_schedule_sensors(None, entry, manager, data)


@pytest.mark.parametrize("mask", range(128))
def test_decode_all_masks_with_an_independent_round_trip(mask):
    days = decode_repeat_days(mask)

    assert isinstance(days, list)
    assert days == _expected_days(mask)
    assert len(days) == mask.bit_count()
    assert len(days) == len(set(days))
    reconstructed = int("".join("1" if day in days else "0" for day in _WIRE_ORDER), 2)
    assert reconstructed == mask


@pytest.mark.parametrize(
    ("mask", "days", "description"),
    [
        (0, [], "Nenhum dia"),
        (127, list(_CALENDAR_ORDER), "Todos os dias"),
        (32, ["Segunda-feira"], "Segunda-feira"),
        (16, ["Terça-feira"], "Terça-feira"),
        (8, ["Quarta-feira"], "Quarta-feira"),
        (4, ["Quinta-feira"], "Quinta-feira"),
        (2, ["Sexta-feira"], "Sexta-feira"),
        (1, ["Sábado"], "Sábado"),
        (64, ["Domingo"], "Domingo"),
        (
            62,
            [
                "Segunda-feira",
                "Terça-feira",
                "Quarta-feira",
                "Quinta-feira",
                "Sexta-feira",
            ],
            "Segunda-feira, Terça-feira, Quarta-feira, Quinta-feira, Sexta-feira",
        ),
        (65, ["Sábado", "Domingo"], "Sábado, Domingo"),
        (33, ["Segunda-feira", "Sábado"], "Segunda-feira, Sábado"),
        (
            106,
            ["Segunda-feira", "Quarta-feira", "Sexta-feira", "Domingo"],
            "Segunda-feira, Quarta-feira, Sexta-feira, Domingo",
        ),
    ],
)
def test_single_days_weekdays_weekend_and_mixed_days(mask, days, description):
    data = _payload(mask)
    manager = _manager(data)
    entity = _entities(data, manager)[0]

    assert decode_repeat_days(mask) == days
    assert entity.extra_state_attributes["repeat_days"] == days
    assert entity.extra_state_attributes["repeat_description"] == description
    manager.get_status.assert_not_called()
    manager.set_status.assert_not_called()


@pytest.mark.parametrize("mask", _INVALID_MASKS)
def test_decode_rejects_invalid_masks_without_coercion(mask):
    with pytest.raises(ValueError, match="integer from 0 to 127"):
        decode_repeat_days(mask)


@pytest.mark.parametrize("mask", range(128))
def test_all_masks_preserve_slot_state_identity_and_read_only_fields(mask):
    data = _payload(mask)
    before = deepcopy(data)
    manager = _manager(data)
    entities = _entities(data, manager)

    assert len(entities) == 8
    for index, entity in enumerate(entities[:5]):
        state = "Ativa" if index == 0 else "Desativada"
        assert entity.native_value == f"{state} · 12:{index:02d}-23:59"
        assert entity.name == f"Aquecedor Agenda {index + 1}"
        assert (
            entity.unique_id == f"tholz_fairland_schedule-entry_schedule_event_{index}"
        )
        assert entity.extra_state_attributes == {
            "slot": index,
            "active": index == 0,
            "turn_on": f"12:{index:02d}",
            "turn_off": "23:59",
            "repeat_mask": mask,
            "repeat_days": _expected_days(mask),
            "repeat_description": _expected_description(mask),
            "read_only": True,
        }
        assert entity.extra_state_attributes["read_only"] is True

    assert (
        entities[5].unique_id == "tholz_fairland_schedule-entry_schedule_active_count"
    )
    assert entities[5].native_value == 1
    assert entities[6].unique_id == "tholz_fairland_schedule-entry_schedule_version"
    assert entities[6].native_value == 42
    assert entities[7].unique_id == "tholz_fairland_schedule-entry_schedule_timestamp"
    assert entities[7].native_value.isoformat() == "2026-05-03T03:09:37+00:00"
    assert data == before
    manager.get_status.assert_not_called()
    manager.set_status.assert_not_called()


@pytest.mark.parametrize("mask", _INVALID_MASKS)
def test_invalid_mask_is_explicit_without_changing_raw_mask_or_slot_state(mask):
    data = _payload(mask)
    manager = _manager(data)
    entity = _entities(data, manager)[0]
    attributes = entity.extra_state_attributes

    assert attributes["repeat_mask"] is mask
    assert attributes["repeat_days"] is None
    assert attributes["repeat_description"] == "Máscara semanal inválida"
    assert attributes["read_only"] is True
    assert entity.native_value == "Ativa · 12:00-23:59"
    assert entity.unique_id == "tholz_fairland_schedule-entry_schedule_event_0"
    manager.get_status.assert_not_called()
    manager.set_status.assert_not_called()


def test_missing_repeat_mask_is_unknown_not_no_days():
    data = _payload(0)
    del data["schedule"]["event0"]["rept"]
    manager = _manager(data)
    entity = _entities(data, manager)[0]

    assert entity.extra_state_attributes["repeat_mask"] is None
    assert entity.extra_state_attributes["repeat_days"] is None
    assert (
        entity.extra_state_attributes["repeat_description"]
        == "Máscara semanal inválida"
    )
    assert "rept" not in data["schedule"]["event0"]
    manager.set_status.assert_not_called()


def test_repeat_days_lists_are_fresh_and_do_not_mutate_the_source():
    data = _payload(127)
    before = deepcopy(data)
    manager = _manager(data)
    entity = _entities(data, manager)[0]

    decoded = decode_repeat_days(127)
    decoded.clear()
    attributes = entity.extra_state_attributes
    attributes["repeat_days"].clear()

    assert decode_repeat_days(127) == list(_CALENDAR_ORDER)
    assert entity.extra_state_attributes["repeat_days"] == list(_CALENDAR_ORDER)
    assert data == before
    manager.set_status.assert_not_called()


@pytest.mark.asyncio
async def test_polling_only_reads_and_preserves_unrelated_schedule_fields():
    initial = _payload(0)
    updated = _payload(65)
    updated["schedule"]["event0"]["rept"] = 62
    updated["schedule"]["event0"]["turnOn"] = {"hr": 6, "min": 30}
    updated["scheduleVersion"] = 43
    updated["weeklySchedule"] = {"event0": {"rept": 64}}
    before_initial = deepcopy(initial)
    before_updated = deepcopy(updated)
    manager = _manager(updated)
    entities = _entities(initial, manager)
    ids = [entity.unique_id for entity in entities]

    for entity in entities:
        await entity.async_update()
        _ = entity.native_value, entity.extra_state_attributes

    assert [entity.unique_id for entity in entities] == ids
    assert entities[0].native_value == "Ativa · 06:30-23:59"
    assert entities[0].extra_state_attributes["repeat_days"] == list(
        _CALENDAR_ORDER[:5]
    )
    assert entities[1].extra_state_attributes["repeat_days"] == ["Sábado", "Domingo"]
    assert entities[6].native_value == 43
    assert initial == before_initial
    assert updated == before_updated
    assert manager.get_sensor_status.await_count == len(entities)
    manager.get_status.assert_not_called()
    manager.set_status.assert_not_called()


def test_weekly_schedule_is_not_used_as_heat_schedule():
    data = {"weeklySchedule": _payload(64)["schedule"]}
    manager = _manager(data)

    assert _entities(data, manager) == []
    manager.get_status.assert_not_called()
    manager.set_status.assert_not_called()


def test_heat_schedule_days_do_not_come_from_weekly_schedule():
    data = _payload(32)
    data["weeklySchedule"] = _payload(64)["schedule"]
    before = deepcopy(data)
    manager = _manager(data)
    entity = _entities(data, manager)[0]

    assert entity.extra_state_attributes["repeat_mask"] == 32
    assert entity.extra_state_attributes["repeat_days"] == ["Segunda-feira"]
    assert data == before
    manager.get_status.assert_not_called()
    manager.set_status.assert_not_called()


def test_expired_schedule_hides_old_days_and_recovers_without_recreation():
    data = _payload(32)
    before = deepcopy(data)
    manager = _manager(data)
    entity = _entities(data, manager)[0]
    unique_id = entity.unique_id
    assert entity.available
    assert entity.extra_state_attributes["repeat_days"] == ["Segunda-feira"]
    manager.is_fresh = False
    assert not entity.available
    assert entity.native_value is None
    assert entity.extra_state_attributes == {}
    manager.is_fresh = True
    assert entity.available
    assert entity.unique_id == unique_id
    assert entity.extra_state_attributes["repeat_days"] == ["Segunda-feira"]
    assert data == before
    manager.get_status.assert_not_called()
    manager.get_sensor_status.assert_not_called()
    manager.set_status.assert_not_called()
