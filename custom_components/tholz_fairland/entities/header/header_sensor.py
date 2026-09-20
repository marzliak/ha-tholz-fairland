"""Read-only Fairland diagnostic sensors."""

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory

from ...utils.const import CONF_NAME_KEY, DOMAIN, ENTITIES_SCAN_INTERVAL
from ...utils.device import get_device_info


def get_header_sensors(hass, entry, manager, data):
    """Create the numeric error-code sensor when reported."""
    if data.get("error") is None:
        return []
    return [HeaderErrorSensor(hass, entry, manager, get_device_info(entry, data), data)]


class HeaderErrorSensor(SensorEntity):
    """Expose the vendor's numeric error code without reinterpretation."""

    def __init__(self, hass, entry, manager, device_info, data):
        self._hass = hass
        self._entry = entry
        self._manager = manager
        self._value = data.get("error")
        self._attr_name = f"{entry.data.get(CONF_NAME_KEY)} Error code"
        self._attr_icon = "mdi:message-alert-outline"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_device_info = device_info
        self._attr_should_poll = True
        self._attr_scan_interval = ENTITIES_SCAN_INTERVAL

    async def async_update(self):
        data = await self._manager.get_status()
        if data:
            self._value = data.get("error")

    @property
    def native_value(self):
        return self._value

    @property
    def unique_id(self):
        return f"{DOMAIN}_{self._entry.entry_id}_error_code"
