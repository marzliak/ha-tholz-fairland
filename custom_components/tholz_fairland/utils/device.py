"""Home Assistant device metadata."""

from .const import CONF_NAME_KEY, DOMAIN


def get_device_info(entry, _data):
    """Return metadata for one Fairland heat pump."""
    return {
        "identifiers": {(DOMAIN, entry.entry_id)},
        "name": entry.data.get(CONF_NAME_KEY),
        "manufacturer": "Tholz",
        "model": "Fairland / TurboSilence heat pump",
    }
