"""
Read-validity and numeric-validation regressions for the Fairland cloud layer.

The real FairlandClientManager and entity classes run here. Only the SDK-backed
client is replaced by a scripted fake, so these tests perform no network or
device I/O.
"""

import asyncio
import copy
import threading
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from custom_components.tholz_fairland.cloud import client_manager as manager_module
from custom_components.tholz_fairland.cloud.client import FairlandCloudAuthError
from custom_components.tholz_fairland.cloud.client_manager import (
    FairlandClientManager,
    FairlandManagerClosedError,
    FairlandReadUnavailableError,
)
from custom_components.tholz_fairland.entities.cloud.cloud_metrics_sensor import (
    get_cloud_metric_sensors,
)
from custom_components.tholz_fairland.entities.cloud.cloud_schedule_sensor import (
    CloudScheduleTimestampSensor,
    get_cloud_schedule_sensors,
)
from custom_components.tholz_fairland.entities.header.header_binary_sensor import (
    get_header_binary_sensors,
)
from custom_components.tholz_fairland.entities.header.header_sensor import (
    get_header_sensors,
)
from custom_components.tholz_fairland.entities.heating.heating_binary_sensor import (
    get_heating_binary_sensors,
)
from custom_components.tholz_fairland.entities.heating.heating_temperature_sensor import (
    get_heating_temperature_sensors,
)
from custom_components.tholz_fairland.entities.heating.utils import (
    get_heating_type,
    get_native_temperature,
    get_valid_heatings,
)

POLLING = 30
WINDOW = 3 * POLLING + 5

SNAPSHOT = {
    "id": 1496,
    "connected": True,
    "error": 0,
    "metricsSummary": {"heatingActiveSeconds24h": 3600.0, "heatingDutyCycle24h": 4.17},
    "schedule": {
        f"event{index}": {
            "active": index == 0,
            "turnOn": {"hr": 12, "min": index},
            "turnOff": {"hr": 23, "min": 59},
            "rept": 127,
        }
        for index in range(5)
    },
    "scheduleVersion": 42,
    "scheduleTimestamp": 1_777_777_777,
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

# Documents the manager must refuse as a read; none has the heat0/type-7 shape.
MALFORMED_READS = [
    {"id": 1496},
    {"heatings": []},
    {"heatings": {"heat0": []}},
    {"heatings": {"heat0": {"opMode": 0}}},
    {"heatings": {"heat0": {"type": True}}},
    {"heatings": {"heat0": {"type": "7"}}},
    {"heatings": {"heat0": {"type": 7.0}}},
    {"heatings": {"heat0": {"type": 8}}},
]


class FakeClock:
    """Monotonic clock the test controls; patched into the manager module only."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class ScriptedClient:
    """SDK boundary stand-in: each read consumes the next scripted outcome."""

    def __init__(self):
        self.script = []
        self.reads = 0
        self.closed = 0
        self.callback = None
        self.payloads = []
        self.command_result = None

    def queue(self, *outcomes):
        self.script.extend(outcomes)

    def get_status(self):
        self.reads += 1
        outcome = self.script.pop(0) if self.script else SNAPSHOT
        if isinstance(outcome, BaseException):
            raise outcome
        return copy.deepcopy(outcome)

    def set_status(self, payload):
        self.payloads.append(payload)
        return copy.deepcopy(self.command_result)

    def close(self):
        self.closed += 1

    def set_state_change_callback(self, callback):
        self.callback = callback


class FakeHass:
    def async_create_background_task(self, coroutine, name):
        del name
        return asyncio.create_task(coroutine)


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr(manager_module, "time", fake)
    return fake


@pytest.fixture
def client():
    return ScriptedClient()


@pytest.fixture
def manager(client):
    return FairlandClientManager(client, polling_interval=POLLING)


def _entry():
    return SimpleNamespace(entry_id="entry-1", data={"name": "Piscina"})


def _with(**changes):
    document = copy.deepcopy(SNAPSHOT)
    document.update(changes)
    return document


def _heat0(**changes):
    document = copy.deepcopy(SNAPSHOT)
    document["heatings"]["heat0"].update(changes)
    return document


def _by_suffix(entities, suffix):
    matches = [entity for entity in entities if entity.unique_id.endswith(suffix)]
    assert len(matches) == 1, suffix
    return matches[0]


async def _wait_until(predicate):
    for _ in range(400):
        if predicate():
            return
        await asyncio.sleep(0.005)
    message = "condition was not reached within two seconds"
    raise AssertionError(message)


# --- manager: freshness, invalidation and read/command separation ---------


def test_freshness_window_is_three_polls_plus_five_seconds(client):
    assert FairlandClientManager(client, POLLING).freshness_window == WINDOW
    assert FairlandClientManager(client, 10).freshness_window == 35


def test_no_read_means_not_fresh(manager):
    assert not manager.is_fresh


@pytest.mark.asyncio
async def test_fresh_read_expires_exactly_after_the_window(manager, clock):
    await manager.get_status()
    clock.advance(WINDOW)
    assert manager.is_fresh
    clock.advance(0.5)
    assert not manager.is_fresh


@pytest.mark.asyncio
async def test_read_failure_invalidates_immediately(manager, client):
    await manager.get_status()
    client.queue(RuntimeError("cloud unreachable"))
    with pytest.raises(RuntimeError, match="cloud unreachable"):
        await manager._fetch_data()
    assert not manager.is_fresh
    assert await manager.get_sensor_status() is None


@pytest.mark.asyncio
async def test_invalid_read_is_not_cached(manager, client):
    await manager.get_status()
    client.queue(None)
    assert await manager._fetch_data() is None
    assert not manager.is_fresh


@pytest.mark.asyncio
@pytest.mark.parametrize("document", MALFORMED_READS)
async def test_structurally_invalid_read_drops_the_previous_read(
    manager, client, document
):
    await manager.get_status()
    client.queue(document)
    assert await manager._fetch_data() is None
    assert not manager.is_fresh
    assert await manager.get_sensor_status() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("document", MALFORMED_READS)
async def test_setup_refuses_a_malformed_snapshot(manager, client, document):
    client.queue(document)
    with pytest.raises(FairlandReadUnavailableError):
        await manager.get_status()
    assert not manager.is_fresh


@pytest.mark.asyncio
async def test_sensor_status_performs_no_io_and_returns_a_copy(manager, client):
    await manager.get_status()
    reads = client.reads
    first = await manager.get_sensor_status()
    first["heatings"]["heat0"]["sp"] = 999
    second = await manager.get_sensor_status()
    assert client.reads == reads
    assert second["heatings"]["heat0"]["sp"] == 330


@pytest.mark.asyncio
async def test_sensor_status_is_none_after_expiry_without_io(manager, client, clock):
    await manager.get_status()
    clock.advance(WINDOW + 1)
    reads = client.reads
    assert await manager.get_sensor_status() is None
    assert client.reads == reads


@pytest.mark.asyncio
async def test_setup_read_fails_closed_when_the_client_returns_no_snapshot(
    manager, client
):
    client.queue(None)
    with pytest.raises(FairlandReadUnavailableError):
        await manager.get_status()
    assert client.reads == 1
    assert not manager.is_fresh


@pytest.mark.asyncio
async def test_get_status_propagates_auth_failure_without_caching(manager, client):
    client.queue(FairlandCloudAuthError("token rejected"))
    with pytest.raises(FairlandCloudAuthError, match="token rejected"):
        await manager.get_status()
    assert client.reads == 1
    assert not manager.is_fresh


@pytest.mark.asyncio
async def test_get_status_never_returns_stale_data_after_expiry(manager, client, clock):
    await manager.get_status()
    clock.advance(WINDOW + 1)
    client.queue(RuntimeError("cloud unreachable"))
    with pytest.raises(RuntimeError, match="cloud unreachable"):
        await manager.get_status()
    assert await manager.get_sensor_status() is None


@pytest.mark.asyncio
async def test_get_status_refreshes_an_expired_snapshot(manager, client, clock):
    await manager.get_status()
    clock.advance(WINDOW + 1)
    data = await manager.get_status()
    assert client.reads == 2
    assert data["heatings"]["heat0"]["t3"] == 230
    assert manager.is_fresh


@pytest.mark.asyncio
async def test_command_result_does_not_advance_the_read_clock(manager, client, clock):
    await manager.get_status()
    clock.advance(60)
    client.command_result = _heat0(sp=340)
    await manager.set_status({"heatings": {"heat0": {"sp": 340}}})
    assert manager._data["heatings"]["heat0"]["sp"] == 340

    # Age the original read to WINDOW + 1 seconds; the ACK at +60 s must not renew it.
    clock.advance(WINDOW - 60 + 1)
    assert not manager.is_fresh
    await manager.set_status({"heatings": {"heat0": {"sp": 340}}})
    assert not manager.is_fresh
    assert manager._read_data["heatings"]["heat0"]["sp"] == 330


@pytest.mark.asyncio
async def test_stop_invalidates_freshness_and_closes_the_client(manager, client):
    manager.start(FakeHass())
    await manager.get_status()
    assert manager.is_fresh

    await manager.stop()

    assert not manager.is_fresh
    assert await manager.get_sensor_status() is None
    assert client.closed == 1
    assert client.callback is None


@pytest.mark.asyncio
async def test_confirmed_controls_do_not_replace_or_rejuvenate_temperatures(
    manager, client, clock
):
    await manager.get_status()
    clock.advance(60)
    client.command_result = _heat0(sp=340, t3=990)
    await manager.set_status({"heatings": {"heat0": {"sp": 340}}})
    data = await manager.get_sensor_status()
    assert data["heatings"]["heat0"]["sp"] == 340
    assert data["heatings"]["heat0"]["t3"] == 230
    assert manager._read_data["heatings"]["heat0"]["sp"] == 330
    clock.advance(WINDOW - 60 + 1)
    assert not manager.is_fresh
    assert await manager.get_sensor_status() is None
    assert client.reads == 1


@pytest.mark.asyncio
async def test_invalid_read_logs_a_safe_message_and_drops_confirmed_controls(
    manager, client, caplog
):
    await manager.get_status()
    client.command_result = _heat0(sp=340)
    await manager.set_status({"heatings": {"heat0": {"sp": 340}}})
    client.queue({"unexpected": "synthetic-private-value"})
    await manager._fetch_data()
    assert not manager.is_fresh
    assert not manager._confirmed_controls
    assert "invalid Fairland state" in caplog.text
    assert "synthetic-private-value" not in caplog.text


@pytest.mark.asyncio
async def test_command_waiting_for_the_lock_is_refused_after_stop(manager, client):
    await manager._lock.acquire()
    pending = asyncio.create_task(
        manager.set_status({"heatings": {"heat0": {"sp": 340}}})
    )
    await asyncio.sleep(0)
    assert not pending.done()
    try:
        await manager.stop()
    finally:
        manager._lock.release()
    with pytest.raises(FairlandManagerClosedError):
        await pending
    assert client.payloads == []


@pytest.mark.asyncio
async def test_command_failure_invalidates_the_old_read(manager, client, monkeypatch):
    await manager.get_status()

    def fail(_payload):
        message = "synthetic command failure"
        raise OSError(message)

    monkeypatch.setattr(client, "set_status", fail)
    with pytest.raises(OSError, match="synthetic command failure"):
        await manager.set_status({"heatings": {"heat0": {"sp": 340}}})
    assert not manager.is_fresh
    assert await manager.get_sensor_status() is None


@pytest.mark.asyncio
async def test_closed_manager_performs_no_client_io(manager, client):
    await manager.get_status()
    await manager.stop()
    reads = client.reads

    with pytest.raises(FairlandReadUnavailableError):
        await manager.get_status()

    assert client.reads == reads
    assert not manager.is_fresh


@pytest.mark.asyncio
async def test_command_after_stop_is_refused_without_client_io(manager, client):
    await manager.get_status()
    await manager.stop()

    with pytest.raises(FairlandManagerClosedError):
        await manager.set_status({"heatings": {"heat0": {"sp": 340}}})

    assert client.payloads == []


@pytest.mark.asyncio
async def test_read_completing_after_stop_cannot_repopulate_the_cache(manager, client):
    entered = threading.Event()
    gate = threading.Event()

    def held_read():
        entered.set()
        gate.wait(timeout=5)
        return copy.deepcopy(SNAPSHOT)

    client.get_status = held_read
    pending = asyncio.create_task(manager.get_status())
    assert await asyncio.to_thread(entered.wait, 5)
    await manager.stop()
    gate.set()

    with pytest.raises(FairlandReadUnavailableError):
        await pending
    assert not manager.is_fresh
    assert await manager.get_sensor_status() is None


@pytest.mark.asyncio
async def test_polling_auth_failure_keeps_the_updater_running(manager, client):
    manager.start(FakeHass())
    try:
        await manager.get_status()
        client.queue(FairlandCloudAuthError("token rejected"))
        client.callback()
        await _wait_until(lambda: client.reads >= 2 and not manager.is_fresh)
        assert not manager._task.done()
    finally:
        await manager.stop()


# --- discovery and numeric validation -------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(0, 0.0), (230, 23.0), (-15, -1.5), (1, 0.1), (230.5, 23.05)],
)
def test_valid_tenths_convert_to_celsius(raw, expected):
    assert get_native_temperature(raw) == pytest.approx(expected)


def test_valid_zero_is_a_reading_not_a_missing_value():
    value = get_native_temperature(0)
    assert value == 0.0
    assert isinstance(value, float)


@pytest.mark.parametrize(
    "raw",
    [
        True,
        False,
        "230",
        "",
        None,
        [],
        {},
        float("nan"),
        float("inf"),
        float("-inf"),
        10**400,
    ],
)
def test_invalid_numeric_inputs_are_rejected(raw):
    assert get_native_temperature(raw) is None


def test_heating_type_is_an_integer_never_a_boolean():
    assert get_heating_type({"type": 7}) == 7
    assert get_heating_type({"type": True}) is None
    assert get_heating_type({"type": 7.0}) is None
    assert get_heating_type({}) is None


@pytest.mark.parametrize(
    "data",
    [
        None,
        [],
        {"heatings": None},
        {"heatings": []},
        {"heatings": {"heat0": []}},
        {"heatings": {"heat0": {"type": 7}}},
    ],
)
def test_discovery_tolerates_malformed_containers(data):
    assert get_valid_heatings(data) == []


def test_boolean_heating_type_never_creates_fairland_entities(manager):
    data = _heat0(type=True)
    assert get_heating_temperature_sensors(None, _entry(), manager, data) == []
    assert get_heating_binary_sensors(None, _entry(), manager, data) == []


# --- entities over the real manager ---------------------------------------


@pytest.mark.asyncio
async def test_temperature_channels_reject_invalid_numbers_and_keep_valid_zero(
    client, manager
):
    client.queue(_heat0(t1=0, t2=True, t3=float("nan")))
    data = await manager.get_status()
    sensors = get_heating_temperature_sensors(None, _entry(), manager, data)

    ambient = _by_suffix(sensors, "_t1_temperature")
    outlet = _by_suffix(sensors, "_t2_temperature")
    inlet = _by_suffix(sensors, "_t3_temperature")
    assert (ambient.available, ambient.native_value) == (True, 0.0)
    assert (outlet.available, outlet.native_value) == (False, None)
    assert (inlet.available, inlet.native_value) == (False, None)


@pytest.mark.asyncio
async def test_temperature_expires_with_the_read_without_entity_retries(
    client, manager, clock
):
    data = await manager.get_status()
    sensor = _by_suffix(
        get_heating_temperature_sensors(None, _entry(), manager, data),
        "_t3_temperature",
    )
    assert sensor.native_value == 23.0

    reads = client.reads
    await sensor.async_update()
    assert client.reads == reads
    assert sensor.native_value == 23.0

    clock.advance(WINDOW + 1)
    await sensor.async_update()
    assert client.reads == reads
    assert not sensor.available
    assert sensor.native_value is None
    assert sensor._state == {}


@pytest.mark.asyncio
async def test_closed_manager_makes_entities_unavailable(manager):
    manager.start(FakeHass())
    try:
        data = await manager.get_status()
        sensor = _by_suffix(
            get_heating_temperature_sensors(None, _entry(), manager, data),
            "_t3_temperature",
        )
        assert sensor.available is True
    finally:
        await manager.stop()
    assert sensor.available is False
    assert sensor.native_value is None


@pytest.mark.asyncio
async def test_online_sensor_reports_false_when_the_cloud_says_disconnected(
    client, manager
):
    client.queue(_with(connected=False))
    data = await manager.get_status()
    online = _by_suffix(
        get_header_binary_sensors(None, _entry(), manager, data), "_connected"
    )
    assert online.available is True
    assert online.is_on is False


@pytest.mark.asyncio
async def test_connected_must_be_boolean_to_be_available(client, manager):
    client.queue(_with(connected="yes"))
    data = await manager.get_status()
    online = _by_suffix(
        get_header_binary_sensors(None, _entry(), manager, data), "_connected"
    )
    assert online.available is False
    assert online.is_on is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("value", "available", "is_on"),
    [(0, True, False), (3, True, True), (True, False, None), ("1", False, None)],
)
async def test_problem_flag_requires_an_integer_code(
    client, manager, value, available, is_on
):
    client.queue(_with(error=value))
    data = await manager.get_status()
    problem = _by_suffix(
        get_header_binary_sensors(None, _entry(), manager, data), "_error"
    )
    assert problem.available is available
    assert problem.is_on == is_on


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("value", "expected"),
    [(0, 0), (5, 5), (True, None), ("5", None), (float("nan"), None)],
)
async def test_error_code_sensor_exposes_only_integer_codes(
    client, manager, value, expected
):
    client.queue(_with(error=value))
    data = await manager.get_status()
    (sensor,) = get_header_sensors(None, _entry(), manager, data)
    assert sensor.native_value == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("value", "available", "is_on"),
    [(False, True, False), (True, True, True), ("true", False, None), (1, False, None)],
)
async def test_running_flag_requires_a_real_boolean(
    client, manager, value, available, is_on
):
    client.queue(_heat0(on=value))
    data = await manager.get_status()
    (running,) = get_heating_binary_sensors(None, _entry(), manager, data)
    assert running.available is available
    assert running.is_on == is_on


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (3600.0, 3600.0),
        (0, 0),
        (True, None),
        (float("inf"), None),
        ("4.2", None),
        (float("nan"), None),
    ],
)
async def test_metric_values_must_be_finite_real_numbers(
    client, manager, value, expected
):
    summary = {"heatingActiveSeconds24h": value, "heatingDutyCycle24h": 4.17}
    client.queue(_with(metricsSummary=summary))
    data = await manager.get_status()
    active = _by_suffix(
        get_cloud_metric_sensors(None, _entry(), manager, data),
        "_heatingActiveSeconds24h",
    )
    assert active.native_value == expected
    assert active.available is (expected is not None)


@pytest.mark.asyncio
async def test_schedule_expires_with_the_read_and_clears_local_slot_state(
    client, manager, clock
):
    data = await manager.get_status()
    slot = _by_suffix(
        get_cloud_schedule_sensors(None, _entry(), manager, data), "_schedule_event_0"
    )
    assert slot.native_value == "Ativa · 12:00-23:59"

    reads = client.reads
    clock.advance(WINDOW + 1)
    await slot.async_update()

    assert client.reads == reads
    assert slot.native_value is None
    assert not slot.available
    assert slot._data == {}


@pytest.mark.asyncio
async def test_version_and_timestamp_require_their_own_valid_field(client, manager):
    client.queue(_with(scheduleVersion=True, scheduleTimestamp=True))
    data = await manager.get_status()
    entities = get_cloud_schedule_sensors(None, _entry(), manager, data)
    version = _by_suffix(entities, "_schedule_version")
    stamp = _by_suffix(entities, "_schedule_timestamp")
    assert (version.available, version.native_value) == (False, None)
    assert (stamp.available, stamp.native_value) == (False, None)


@pytest.mark.asyncio
async def test_valid_version_and_timestamp_are_exposed(manager):
    data = await manager.get_status()
    entities = get_cloud_schedule_sensors(None, _entry(), manager, data)
    version = _by_suffix(entities, "_schedule_version")
    stamp = _by_suffix(entities, "_schedule_timestamp")
    assert version.native_value == 42
    assert stamp.native_value == datetime(2026, 5, 3, 3, 9, 37, tzinfo=UTC)


@pytest.mark.asyncio
async def test_schedule_timestamp_rejects_values_outside_datetime_range(
    client, manager
):
    client.queue(_with(scheduleTimestamp=10**30))
    data = await manager.get_status()
    stamp = _by_suffix(
        get_cloud_schedule_sensors(None, _entry(), manager, data), "_schedule_timestamp"
    )
    assert isinstance(stamp, CloudScheduleTimestampSensor)
    assert stamp.native_value is None


@pytest.mark.asyncio
async def test_start_after_stop_does_not_resume_polling(manager, client):
    manager.start(FakeHass())
    await manager.stop()

    manager.start(FakeHass())

    assert manager._task is None
    assert client.callback is None
    assert not manager.is_fresh
