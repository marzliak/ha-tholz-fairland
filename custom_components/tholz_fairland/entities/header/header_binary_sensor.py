"""Read-only connection and problem sensors."""

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory

from ...utils.const import CONF_NAME_KEY, DOMAIN, ENTITIES_SCAN_INTERVAL
from ...utils.device import get_device_info

_CONFIG = {
    "connected": (
        "Online",
        "mdi:cloud-check-outline",
        BinarySensorDeviceClass.CONNECTIVITY,
    ),
    "error": ("Problem", "mdi:alert-outline", BinarySensorDeviceClass.PROBLEM),
}


def get_header_binary_sensors(hass, entry, manager, data):
    """Create binary sensors present in the reported state."""
    device_info = get_device_info(entry, data)
    return [
        HeaderBinarySensor(hass, entry, manager, device_info, key, data[key])
        for key in _CONFIG
        if data.get(key) is not None
    ]


class HeaderBinarySensor(BinarySensorEntity):
    """A reported cloud/device status flag."""

    def __init__(self, hass, entry, manager, device_info, sensor_key, value):
        self._hass = hass
        self._entry = entry
        self._manager = manager
        self._sensor_key = sensor_key
        self._state = value
        name, icon, device_class = _CONFIG[sensor_key]
        self._attr_name = f"{entry.data.get(CONF_NAME_KEY)} {name}"
        self._attr_icon = icon
        self._attr_device_class = device_class
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_device_info = device_info
        self._attr_should_poll = True
        self._attr_scan_interval = ENTITIES_SCAN_INTERVAL

    async def async_update(self):
        data = await self._manager.get_status()
        if data:
            self._state = data.get(self._sensor_key)

    @property
    def is_on(self):
        if self._sensor_key == "error":
            return self._state not in (0, None, False)
        return self._state is True

    @property
    def unique_id(self):
        return f"{DOMAIN}_{self._entry.entry_id}_{self._sensor_key}"
