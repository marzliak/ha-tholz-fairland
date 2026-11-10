"""Fairland water-heater entity."""

from homeassistant.components.water_heater import (
    STATE_OFF,
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.exceptions import HomeAssistantError

from ...cloud.client_manager import FairlandManagerClosedError
from ...cloud.temperature_policy import (
    FairlandTemperatureError,
    temperature_policy,
)
from ...utils.const import CONF_NAME_KEY, DOMAIN, ENTITIES_SCAN_INTERVAL
from ...utils.device import get_device_info
from ...utils.dict import get_in, set_in
from .const import HEATING_OP_MODE, HEATING_TYPE
from .utils import get_heating_type, get_native_temperature, get_valid_heatings

FAIRLAND_STATE_AUTOMATIC = "automatic"
FAIRLAND_STATE_HEAT = "heat"
FAIRLAND_STATE_COLD = "cold"

_MODE_TO_HA: dict[int, str] = {
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
    """Control operation mode and target temperature without replaying old state."""

    def __init__(self, hass, entry, manager, device_info, heating_key, state):
        self._hass = hass
        self._entry = entry
        self._manager = manager
        self._attr_device_info = device_info
        self._heating_key = heating_key
        self._state = state if isinstance(state, dict) else {}
        self._attr_should_poll = True
        self._attr_scan_interval = ENTITIES_SCAN_INTERVAL

    async def async_update(self):
        data = await self._manager.get_sensor_status()
        state = get_in(data, self._heating_key)
        self._state = state if isinstance(state, dict) else {}

    async def async_set_temperature(self, **kwargs):
        temperature = kwargs.get("temperature")
        if temperature is None:
            return
        try:
            setpoint = temperature_policy(self._state).to_raw(temperature)
        except FairlandTemperatureError as exc:
            raise HomeAssistantError(str(exc)) from exc
        try:
            result = await self._manager.set_status(
                set_in({}, self._heating_key, {"sp": setpoint})
            )
        except FairlandManagerClosedError as exc:
            raise HomeAssistantError(str(exc)) from exc
        if result:
            state = get_in(result, self._heating_key)
            self._state = state if isinstance(state, dict) else {}

    async def async_set_operation_mode(self, operation_mode):
        if operation_mode not in _HA_TO_MODE:
            return

        try:
            result = await self._manager.set_status(
                set_in(
                    {}, self._heating_key, {"opMode": int(_HA_TO_MODE[operation_mode])}
                )
            )
        except FairlandManagerClosedError as exc:
            raise HomeAssistantError(str(exc)) from exc
        if result:
            state = get_in(result, self._heating_key)
            self._state = state if isinstance(state, dict) else {}

    @property
    def available(self):
        return (
            self._manager.is_fresh
            and get_heating_type(self._state) == HEATING_TYPE.TROCADOR_CALOR_FAIRLAND
        )

    @property
    def temperature_unit(self):
        return UnitOfTemperature.CELSIUS

    @property
    def current_temperature(self):
        if not self.available:
            return None
        return get_native_temperature(self._state.get("t3"))

    @property
    def target_temperature(self):
        if not self.available:
            return None
        return get_native_temperature(self._state.get("sp"))

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
        if not self.available:
            return None
        value = self._state.get("opMode")
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return _MODE_TO_HA.get(value)

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
