"""Helpers for the Fairland heating state."""

import math


def get_heating_type(state):
    """
    Return the reported heating type as an integer, or None when malformed.

    Booleans are never a heating type, even though ``True`` equals ``1`` in Python.
    """
    value = state.get("type")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def get_native_temperature(raw):
    """
    Convert a Fairland tenths-of-a-degree value to °C, or return None.

    Booleans, text, non-finite values and values that overflow float conversion
    are rejected. A valid zero is preserved as 0.0.
    """
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    try:
        value = float(raw)
    except OverflowError:
        return None
    if not math.isfinite(value):
        return None
    return value / 10


def heating_has_valid_temperatures(state):
    """Return whether at least one Fairland temperature is available."""
    return any(state.get(key) is not None for key in ("t1", "t2", "t3"))


def get_valid_heatings(data):
    """Return the supported heat0 state when it contains telemetry."""
    if not isinstance(data, dict):
        return []
    heatings = data.get("heatings")
    state = heatings.get("heat0") if isinstance(heatings, dict) else None
    if not isinstance(state, dict) or not heating_has_valid_temperatures(state):
        return []
    return [(["heatings", "heat0"], state)]
