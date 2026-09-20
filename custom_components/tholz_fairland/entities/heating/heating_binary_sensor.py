"""Read-only Fairland activity sensor."""

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)

from ...utils.const import CONF_NAME_KEY, DOMAIN, ENTITIES_SCAN_INTERVAL
from ...utils.device import get_device_info
from ...utils.dict import get_in
from .const import HEATING_TYPE
from .utils import get_heating_type, get_valid_heatings


def get_heating_binary_sensors(hass, entry, manager, data):
    """Create the reported running-state sensor."""
    device_info = get_device_info(entry, data)
    return [
        HeatingBinarySensor(hass, entry, manager, device_info, heating_key, state)
        for heating_key, state in get_valid_heatings(data)
        if get_heating_type(state) == HEATING_TYPE.TROCADOR_CALOR_FAIRLAND
        and state.get("on") is not None
    ]


class HeatingBinarySensor(BinarySensorEntity):
    """Expose reported heat-pump activity without a write control."""

    def __init__(self, hass, entry, manager, device_info, heating_key, state):
        self._hass = hass
        self._entry = entry
        self._manager = manager
        self._attr_device_info = device_info
        self._heating_key = heating_key
        self._state = state
        self._attr_name = f"{entry.data.get(CONF_NAME_KEY)} Heat pump running"
        self._attr_icon = "mdi:heat-pump-outline"
        self._attr_device_class = BinarySensorDeviceClass.RUNNING
        self._attr_should_poll = True
        self._attr_scan_interval = ENTITIES_SCAN_INTERVAL

    async def async_update(self):
        data = await self._manager.get_status()
        if data:
            self._state = get_in(data, self._heating_key)

    @property
    def is_on(self):
        return self._state.get("on") is True

    @property
    def unique_id(self):
        return f"{DOMAIN}_{self._entry.entry_id}_heat0_running"
