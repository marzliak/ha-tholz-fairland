"""Fairland water-heater entity."""

from homeassistant.components.water_heater import (
    STATE_OFF,
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.exceptions import HomeAssistantError

from ...cloud.temperature_policy import (
    FairlandTemperatureError,
    temperature_policy,
)
from ...utils.const import CONF_NAME_KEY, DOMAIN, ENTITIES_SCAN_INTERVAL
from ...utils.device import get_device_info
from ...utils.dict import get_in, set_in
from .const import HEATING_OP_MODE, HEATING_TYPE
from .utils import get_heating_type, get_valid_heatings

FAIRLAND_STATE_AUTOMATIC = "automatic"
FAIRLAND_STATE_HEAT = "heat"
FAIRLAND_STATE_COLD = "cold"

_MODE_TO_HA = {
    HEATING_OP_MODE.DESLIGADO: STATE_OFF,
    HEATING_OP_MODE.AUTOMATICO: FAIRLAND_STATE_AUTOMATIC,
    HEATING_OP_MODE.AQUECER: FAIRLAND_STATE_HEAT,
    HEATING_OP_MODE.RESFRIAR: FAIRLAND_STATE_COLD,
}
_HA_TO_MODE = {value: key for key, value in _MODE_TO_HA.items()}


def get_heating_water_heaters(hass, entry, manager, data):
    """Create one entity for the supported Fairland heat0 channel."""
    device_info = get_device_info(entry, data)
    return [
        HeatingWaterHeater(hass, entry, manager, device_info, heating_key, state)
        for heating_key, state in get_valid_heatings(data)
        if get_heating_type(state) == HEATING_TYPE.TROCADOR_CALOR_FAIRLAND
    ]


class HeatingWaterHeater(WaterHeaterEntity):
    """Control operation mode and target temperature."""

    def __init__(self, hass, entry, manager, device_info, heating_key, state):
        self._hass = hass
        self._entry = entry
        self._manager = manager
        self._attr_device_info = device_info
        self._heating_key = heating_key
        self._state = state
        self._attr_should_poll = True
        self._attr_scan_interval = ENTITIES_SCAN_INTERVAL

    async def async_update(self):
        data = await self._manager.get_status()
        if data:
            self._state = get_in(data, self._heating_key)

    async def async_set_temperature(self, **kwargs):
        temperature = kwargs.get("temperature")
        if temperature is None:
            return
        try:
            setpoint = temperature_policy(self._state).to_raw(temperature)
        except FairlandTemperatureError as exc:
            raise HomeAssistantError(str(exc)) from exc
        next_state = {"sp": setpoint}
        result = await self._manager.set_status(
            set_in({}, self._heating_key, next_state)
        )
        if result:
            self._state = get_in(result, self._heating_key)

    async def async_set_operation_mode(self, operation_mode):
        if operation_mode not in _HA_TO_MODE:
            return
        next_state = {"opMode": _HA_TO_MODE[operation_mode]}
        result = await self._manager.set_status(
            set_in({}, self._heating_key, next_state)
        )
        if result:
            self._state = get_in(result, self._heating_key)

    @property
    def temperature_unit(self):
        return UnitOfTemperature.CELSIUS

    @property
    def current_temperature(self):
        value = self._state.get("t3")
        return value / 10 if value is not None else None

    @property
    def target_temperature(self):
        value = self._state.get("sp")
        return value / 10 if value is not None else None

    @property
    def target_temperature_step(self):
        try:
            return temperature_policy(self._state).step / 10
        except FairlandTemperatureError:
            return None

    @property
    def min_temp(self):
        try:
            return temperature_policy(self._state).minimum / 10
        except FairlandTemperatureError:
            return None

    @property
    def max_temp(self):
        try:
            return temperature_policy(self._state).maximum / 10
        except FairlandTemperatureError:
            return None

    @property
    def current_operation(self):
        return _MODE_TO_HA.get(self._state.get("opMode"), STATE_OFF)

    @property
    def operation_list(self):
        return list(_MODE_TO_HA.values())

    @property
    def supported_features(self):
        return (
            WaterHeaterEntityFeature.TARGET_TEMPERATURE
            | WaterHeaterEntityFeature.OPERATION_MODE
        )

    @property
    def name(self):
        return f"{self._entry.data.get(CONF_NAME_KEY)} Pool"

    @property
    def icon(self):
        return "mdi:pool-thermometer"

    @property
    def unique_id(self):
        return f"{DOMAIN}_{self._entry.entry_id}_heat0_water_heater"
