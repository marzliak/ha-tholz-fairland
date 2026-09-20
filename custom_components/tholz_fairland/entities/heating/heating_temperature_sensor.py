"""Fairland temperature sensors."""

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfTemperature

from ...utils.const import CONF_NAME_KEY, DOMAIN, ENTITIES_SCAN_INTERVAL
from ...utils.device import get_device_info
from ...utils.dict import get_in
from .const import HEATING_TYPE
from .utils import get_heating_type, get_valid_heatings

_TEMPERATURES = {
    "t1": "Ambient temperature",
    "t2": "Outlet temperature",
    "t3": "Inlet temperature",
}


def get_heating_temperature_sensors(hass, entry, manager, data):
    """Create available Fairland temperature sensors."""
    device_info = get_device_info(entry, data)
    entities = []
    for heating_key, state in get_valid_heatings(data):
        if get_heating_type(state) != HEATING_TYPE.TROCADOR_CALOR_FAIRLAND:
            continue
        entities.extend(
            HeatingTemperatureSensor(
                hass, entry, manager, device_info, heating_key, key, state
            )
            for key in _TEMPERATURES
            if state.get(key) is not None
        )
    return entities


class HeatingTemperatureSensor(SensorEntity):
    """One reported temperature channel."""

    def __init__(
        self, hass, entry, manager, device_info, heating_key, sensor_key, state
    ):
        self._hass = hass
        self._entry = entry
        self._manager = manager
        self._attr_device_info = device_info
        self._heating_key = heating_key
        self._sensor_key = sensor_key
        self._state = state
        self._attr_name = f"{entry.data.get(CONF_NAME_KEY)} {_TEMPERATURES[sensor_key]}"
        self._attr_icon = "mdi:thermometer"
        self._attr_device_class = SensorDeviceClass.TEMPERATURE
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        self._attr_should_poll = True
        self._attr_scan_interval = ENTITIES_SCAN_INTERVAL

    async def async_update(self):
        data = await self._manager.get_status()
        if data:
            self._state = get_in(data, self._heating_key)

    @property
    def native_value(self):
        raw = self._state.get(self._sensor_key)
        return raw / 10 if raw is not None else None

    @property
    def state(self):
        """Retain compatibility with existing entity tests."""
        return self.native_value

    @property
    def unique_id(self):
        return f"{DOMAIN}_{self._entry.entry_id}_heat0_{self._sensor_key}_temperature"
