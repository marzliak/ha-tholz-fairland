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


def _format_time(value):
    if not isinstance(value, dict):
        return None
    hour = value.get("hr")
    minute = value.get("min")
    if not isinstance(hour, int) or not isinstance(minute, int):
        return None
    return f"{hour:02d}:{minute:02d}"


def _repeat_description(mask):
    if mask == _ALL_DAYS_REPEAT_MASK:
        return "Todos os dias"
    if mask == 0:
        return "Nenhum dia"
    return f"Máscara semanal {mask} (dias ainda não mapeados)"


def get_cloud_schedule_sensors(hass, entry, manager, data):
    """Create schedule entities after a successful read."""
    schedule = data.get("schedule")
    if not isinstance(schedule, dict):
        return []

    device_info = get_device_info(entry, data)
    entities: list[SensorEntity] = [
        CloudScheduleSlotSensor(
            hass,
            entry,
            manager,
            device_info,
            index,
            data,
        )
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
    """Shared polling support for schedule entities."""

    def __init__(self, hass, entry, manager, device_info, data):
        self._hass = hass
        self._entry = entry
        self._manager = manager
        self._attr_device_info = device_info
        self._data = data
        self._attr_should_poll = True
        self._attr_scan_interval = ENTITIES_SCAN_INTERVAL

    async def async_update(self):
        data = await self._manager.get_status()
        if data and isinstance(data.get("schedule"), dict):
            self._data = data


class CloudScheduleSlotSensor(_CloudScheduleSensor):
    """One of the five schedule records exposed by the device."""

    def __init__(self, hass, entry, manager, device_info, index, data):
        super().__init__(hass, entry, manager, device_info, data)
        self._index = index
        self._key = f"event{index}"
        self._attr_name = f"{entry.data.get(CONF_NAME_KEY)} Agenda {index + 1}"

    @property
    def _event(self):
        return self._data.get("schedule", {}).get(self._key, {})

    @property
    def native_value(self):
        event = self._event
        start = _format_time(event.get("turnOn")) or "--:--"
        end = _format_time(event.get("turnOff")) or "--:--"
        state = "Ativa" if event.get("active") is True else "Desativada"
        return f"{state} · {start}-{end}"

    @property
    def icon(self):
        return (
            "mdi:calendar-check" if self._event.get("active") else "mdi:calendar-remove"
        )

    @property
    def extra_state_attributes(self):
        event = self._event
        repeat = event.get("rept")
        return {
            "slot": self._index,
            "active": event.get("active"),
            "turn_on": _format_time(event.get("turnOn")),
            "turn_off": _format_time(event.get("turnOff")),
            "repeat_mask": repeat,
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
        schedule = self._data.get("schedule", {})
        return sum(event.get("active") is True for event in schedule.values())

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

    @property
    def native_value(self):
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

    @property
    def native_value(self):
        timestamp = self._data.get("scheduleTimestamp")
        if not isinstance(timestamp, int) or isinstance(timestamp, bool):
            return None
        return datetime.fromtimestamp(timestamp, tz=UTC)

    @property
    def unique_id(self):
        return f"{DOMAIN}_{self._entry.entry_id}_schedule_timestamp"
