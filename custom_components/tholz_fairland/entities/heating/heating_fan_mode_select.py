"""Fairland fan-mode selector."""

from homeassistant.components.select import SelectEntity
from homeassistant.exceptions import HomeAssistantError

from ...cloud.client_manager import FairlandManagerClosedError
from ...utils.const import CONF_NAME_KEY, DOMAIN, ENTITIES_SCAN_INTERVAL
from ...utils.device import get_device_info
from ...utils.dict import get_in, set_in
from .const import HEATING_FAN_MODE, HEATING_TYPE
from .utils import get_heating_type, get_valid_heatings

FAN_MODE_TO_OPTION: dict[int, str] = {
    HEATING_FAN_MODE.SILENCIOSO: "Silent",
    HEATING_FAN_MODE.INTELIGENTE: "Intelligent",
}
_OPTION_TO_FAN_MODE = {option: mode for mode, option in FAN_MODE_TO_OPTION.items()}


def get_heating_fan_mode_selects(hass, entry, manager, data):
    """Create one fan-mode selector for heat0."""
    device_info = get_device_info(entry, data)
    return [
        HeatingFanModeSelect(hass, entry, manager, device_info, heating_key, state)
        for heating_key, state in get_valid_heatings(data)
        if get_heating_type(state) == HEATING_TYPE.TROCADOR_CALOR_FAIRLAND
    ]


class HeatingFanModeSelect(SelectEntity):
    """Select an allowlisted Fairland fan mode."""

    def __init__(self, hass, entry, manager, device_info, heating_key, state):
        self._hass = hass
        self._entry = entry
        self._manager = manager
        self._attr_device_info = device_info
        self._heating_key = heating_key
        self._state = state if isinstance(state, dict) else {}
        self._attr_name = f"{entry.data.get(CONF_NAME_KEY)} Fan mode"
        self._attr_icon = "mdi:fan"
        self._attr_should_poll = True
        self._attr_scan_interval = ENTITIES_SCAN_INTERVAL

    async def async_update(self):
        data = await self._manager.get_sensor_status()
        state = get_in(data, self._heating_key)
        self._state = state if isinstance(state, dict) else {}

    async def async_select_option(self, option):
        if option not in _OPTION_TO_FAN_MODE:
            return

        try:
            result = await self._manager.set_status(
                set_in(
                    {}, self._heating_key, {"fanMode": int(_OPTION_TO_FAN_MODE[option])}
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
    def options(self):
        return list(FAN_MODE_TO_OPTION.values())

    @property
    def current_option(self):
        if not self.available:
            return None
        value = self._state.get("fanMode")
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return FAN_MODE_TO_OPTION.get(value)

    @property
    def unique_id(self):
        return f"{DOMAIN}_{self._entry.entry_id}_heat0_fan_mode"
