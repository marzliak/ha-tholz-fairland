"""Helpers for the Fairland heating state."""


def get_heating_type(state):
    """Return the reported heating type."""
    return state.get("type")


def heating_has_valid_temperatures(state):
    """Return whether at least one Fairland temperature is available."""
    return any(state.get(key) is not None for key in ("t1", "t2", "t3"))


def get_valid_heatings(data):
    """Return the supported heat0 state when it contains telemetry."""
    state = data.get("heatings", {}).get("heat0")
    if not isinstance(state, dict) or not heating_has_valid_temperatures(state):
        return []
    return [(["heatings", "heat0"], state)]
