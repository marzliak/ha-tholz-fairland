"""Read-only entities for the verified Fairland heat0Schedule shadow."""

from datetime import UTC, datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import EntityCategory

from ...utils.const import CONF_NAME_KEY, DOMAIN, ENTITIES_SCAN_INTERVAL
from ...utils.device import get_device_info

_SCHEDULE_EVENT_COUNT = 5
_ALL_DAYS_REPEAT_MASK = 127
_REPEAT_DAY_BITS = (
    (32, "Segunda-feira"),
    (16, "Terça-feira"),
    (8, "Quarta-feira"),
    (4, "Quinta-feira"),
    (2, "Sexta-feira"),
    (1, "Sábado"),
    (64, "Domingo"),
)


def decode_repeat_days(mask: object) -> list[str]:
    """Decode a heat0Schedule repeat mask in Monday-to-Sunday order."""
    if (
        not isinstance(mask, int)
        or isinstance(mask, bool)
        or not 0 <= mask <= _ALL_DAYS_REPEAT_MASK
    ):
        message = "Repeat mask must be an integer from 0 to 127"
        raise ValueError(message)
    return [day for bit, day in _REPEAT_DAY_BITS if mask & bit]


_HOUR_MAX = 23
_MINUTE_MAX = 59


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _as_event(value):
    return value if isinstance(value, dict) else {}


def _format_time(value):
    if not isinstance(value, dict):
        return None
    hour = value.get("hr")
    minute = value.get("min")
    if isinstance(hour, bool) or not isinstance(hour, int):
        return None
    if isinstance(minute, bool) or not isinstance(minute, int):
        return None
    if not (0 <= hour <= _HOUR_MAX and 0 <= minute <= _MINUTE_MAX):
        return None
    return f"{hour:02d}:{minute:02d}"


def _repeat_description(mask):
    try:
        days = decode_repeat_days(mask)
    except ValueError:
        return "Máscara semanal inválida"
    if mask == _ALL_DAYS_REPEAT_MASK:
        return "Todos os dias"
    if not days:
        return "Nenhum dia"
    return ", ".join(days)


def get_cloud_schedule_sensors(hass, entry, manager, data):
    """Create schedule entities after a successful read."""
    schedule = data.get("schedule")
    if not isinstance(schedule, dict):
        return []

    device_info = get_device_info(entry, data)
    entities: list[SensorEntity] = [
        CloudScheduleSlotSensor(hass, entry, manager, device_info, index, data)
        for index in range(_SCHEDULE_EVENT_COUNT)
    ]
    entities.extend(
        [
            CloudScheduleActiveCountSensor(hass, entry, manager, device_info, data),
            CloudScheduleVersionSensor(hass, entry, manager, device_info, data),
            CloudScheduleTimestampSensor(hass, entry, manager, device_info, data),
        ]
    )
    return entities


class _CloudScheduleSensor(SensorEntity):
    """Shared read-only support for schedule entities."""

    def __init__(self, hass, entry, manager, device_info, data):
        self._hass = hass
        self._entry = entry
        self._manager = manager
        self._attr_device_info = device_info
        self._data = data
        self._attr_should_poll = True
        self._attr_scan_interval = ENTITIES_SCAN_INTERVAL

    @staticmethod
    def _has_schedule(document):
        return isinstance(document.get("schedule"), dict)

    def _own_value_valid(self) -> bool:
        """Return whether this entity's own field is structurally valid."""
        return True

    @property
    def available(self):
        return (
            self._manager.is_fresh
            and self._has_schedule(self._data)
            and self._own_value_valid()
        )

    async def async_update(self):
        data = await self._manager.get_sensor_status()
        self._data = data if data and self._has_schedule(data) else {}


class CloudScheduleSlotSensor(_CloudScheduleSensor):
    """One of the five schedule records exposed by the device."""

    def __init__(self, hass, entry, manager, device_info, index, data):
        super().__init__(hass, entry, manager, device_info, data)
        self._index = index
        self._key = f"event{index}"
        self._attr_name = f"{entry.data.get(CONF_NAME_KEY)} Agenda {index + 1}"

    def _own_value_valid(self) -> bool:
        event = self._event
        return (
            isinstance(event.get("active"), bool)
            and _format_time(event.get("turnOn")) is not None
            and _format_time(event.get("turnOff")) is not None
        )

    @property
    def _event(self):
        schedule = self._data.get("schedule")
        if not isinstance(schedule, dict):
            return {}
        return _as_event(schedule.get(self._key))

    @property
    def native_value(self):
        if not self.available:
            return None
        event = self._event
        start = _format_time(event.get("turnOn")) or "--:--"
        end = _format_time(event.get("turnOff")) or "--:--"
        state = "Ativa" if event.get("active") is True else "Desativada"
        return f"{state} · {start}-{end}"

    @property
    def icon(self):
        if not self.available:
            return "mdi:calendar-question"
        if self._event.get("active") is True:
            return "mdi:calendar-check"
        return "mdi:calendar-remove"

    @property
    def extra_state_attributes(self):
        if not self.available:
            return {}
        event = self._event
        repeat = event.get("rept")
        try:
            repeat_days = decode_repeat_days(repeat)
        except ValueError:
            repeat_days = None
        return {
            "slot": self._index,
            "active": event.get("active"),
            "turn_on": _format_time(event.get("turnOn")),
            "turn_off": _format_time(event.get("turnOff")),
            "repeat_mask": repeat,
            "repeat_days": repeat_days,
            "repeat_description": _repeat_description(repeat),
            "read_only": True,
        }

    @property
    def unique_id(self):
        return f"{DOMAIN}_{self._entry.entry_id}_schedule_event_{self._index}"


class CloudScheduleActiveCountSensor(_CloudScheduleSensor):
    """Number of active schedule records."""

    def __init__(self, hass, entry, manager, device_info, data):
        super().__init__(hass, entry, manager, device_info, data)
        self._attr_name = f"{entry.data.get(CONF_NAME_KEY)} Agendas Ativas"
        self._attr_icon = "mdi:calendar-clock"
        self._attr_native_unit_of_measurement = "agendas"
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        if not self.available:
            return None
        schedule = self._data["schedule"]
        return sum(
            _as_event(event).get("active") is True for event in schedule.values()
        )

    @property
    def unique_id(self):
        return f"{DOMAIN}_{self._entry.entry_id}_schedule_active_count"


class CloudScheduleVersionSensor(_CloudScheduleSensor):
    """Named-shadow version for diagnostics and future write guards."""

    def __init__(self, hass, entry, manager, device_info, data):
        super().__init__(hass, entry, manager, device_info, data)
        self._attr_name = f"{entry.data.get(CONF_NAME_KEY)} Agenda Versão"
        self._attr_icon = "mdi:source-branch"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    def _own_value_valid(self):
        return _is_int(self._data.get("scheduleVersion"))

    @property
    def native_value(self):
        if not self.available:
            return None
        return self._data.get("scheduleVersion")

    @property
    def unique_id(self):
        return f"{DOMAIN}_{self._entry.entry_id}_schedule_version"


class CloudScheduleTimestampSensor(_CloudScheduleSensor):
    """Last AWS shadow update timestamp."""

    def __init__(self, hass, entry, manager, device_info, data):
        super().__init__(hass, entry, manager, device_info, data)
        self._attr_name = f"{entry.data.get(CONF_NAME_KEY)} Agenda Atualizada"
        self._attr_icon = "mdi:clock-check-outline"
        self._attr_device_class = SensorDeviceClass.TIMESTAMP
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    def _timestamp(self):
        timestamp = self._data.get("scheduleTimestamp")
        if isinstance(timestamp, bool) or not isinstance(timestamp, int):
            return None
        try:
            value = datetime.fromtimestamp(timestamp, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
        else:
            return value

    def _own_value_valid(self):
        return self._timestamp() is not None

    @property
    def native_value(self):
        if not self.available:
            return None
        return self._timestamp()

    @property
    def unique_id(self):
        return f"{DOMAIN}_{self._entry.entry_id}_schedule_timestamp"
