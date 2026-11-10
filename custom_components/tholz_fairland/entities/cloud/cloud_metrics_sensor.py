"""Rolling cloud-metric sensors for Fairland heat pumps."""

import math

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfTime

from ...utils.const import CONF_NAME_KEY, DOMAIN, ENTITIES_SCAN_INTERVAL
from ...utils.device import get_device_info

_METRIC_CONFIG = {
    "heatingActiveSeconds24h": {
        "name": "Tempo Aquecendo 24h",
        "icon": "mdi:timer-outline",
        "device_class": SensorDeviceClass.DURATION,
        "unit": UnitOfTime.SECONDS,
    },
    "heatingDutyCycle24h": {
        "name": "Ciclo de Aquecimento 24h",
        "icon": "mdi:percent-outline",
        "device_class": None,
        "unit": PERCENTAGE,
    },
}


def get_cloud_metric_sensors(hass, entry, manager, data):
    """Create the cached 24-hour metric sensors."""
    device_info = get_device_info(entry, data)
    return [
        CloudMetricSensor(hass, entry, manager, device_info, key, data)
        for key in _METRIC_CONFIG
    ]


def _finite_number(value):
    """Return a real, finite number; reject bool, text, NaN, infinity and overflow."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        finite = math.isfinite(float(value))
    except OverflowError:
        return None
    return value if finite else None


class CloudMetricSensor(SensorEntity):
    """A cached 24-hour statistic returned by the Tholz metrics API."""

    def __init__(self, hass, entry, manager, device_info, metric_key, data):
        self._hass = hass
        self._entry = entry
        self._manager = manager
        self._device_info = device_info
        self._metric_key = metric_key
        summary = data.get("metricsSummary")
        self._summary = summary if isinstance(summary, dict) else {}

        config = _METRIC_CONFIG[metric_key]
        self._attr_name = f"{entry.data.get(CONF_NAME_KEY)} {config['name']}"
        self._attr_icon = config["icon"]
        self._attr_device_class = config["device_class"]
        self._attr_native_unit_of_measurement = config["unit"]
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_should_poll = True
        self._attr_scan_interval = ENTITIES_SCAN_INTERVAL

    async def async_update(self):
        data = await self._manager.get_sensor_status()
        summary = data.get("metricsSummary") if data else None
        self._summary = summary if isinstance(summary, dict) else {}

    @property
    def native_value(self):
        if not self._manager.is_fresh:
            return None
        return _finite_number(self._summary.get(self._metric_key))

    @property
    def available(self):
        return self.native_value is not None

    @property
    def extra_state_attributes(self):
        return {"window_hours": 24}

    @property
    def unique_id(self):
        return f"{DOMAIN}_{self._entry.entry_id}_{self._metric_key}"

    @property
    def device_info(self):
        return self._device_info
