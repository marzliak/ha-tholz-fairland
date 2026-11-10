"""Shared, conservative setpoint policy for the supported Fairland family."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

FAIRLAND_TYPE = 7
DEFAULT_STEP = 10
MIN_SETPOINT = 120
MAX_SETPOINT = 400
_MODE_LIMITS = {0: (120, 400), 2: (120, 400), 4: (180, 400), 5: (120, 300)}

ERR_CAPABILITY = "Invalid or unsupported Fairland temperature capability"
ERR_TEMPERATURE = "Temperature must be a finite number in exact tenths of a degree"
ERR_STEP = "Temperature must align with the reported step; it is not rounded"


class FairlandTemperatureError(ValueError):
    """The requested setpoint cannot be safely represented or validated."""


def _positive_integer(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise FairlandTemperatureError(ERR_CAPABILITY)
    return value


@dataclass(frozen=True, slots=True)
class TemperaturePolicy:
    """Effective limits and minimum-anchored step, all in tenths of a degree."""

    minimum: int
    maximum: int
    step: int

    def validate_raw(self, value: Any) -> int:
        """Reject invalid values before any transport can publish them."""
        if isinstance(value, bool) or not isinstance(value, int):
            raise FairlandTemperatureError(ERR_TEMPERATURE)
        if not self.minimum <= value <= self.maximum:
            message = (
                f"Temperature must be between {self.minimum / 10:g} "
                f"and {self.maximum / 10:g} °C for the selected mode"
            )
            raise FairlandTemperatureError(message)
        if (value - self.minimum) % self.step:
            raise FairlandTemperatureError(ERR_STEP)
        return value

    def to_raw(self, temperature: Any) -> int:
        """Convert degrees without silently rounding a caller's request."""
        if isinstance(temperature, bool) or not isinstance(
            temperature, (int, float, Decimal)
        ):
            raise FairlandTemperatureError(ERR_TEMPERATURE)
        value = Decimal(str(temperature))
        if not value.is_finite():
            raise FairlandTemperatureError(ERR_TEMPERATURE)
        if value < Decimal(self.minimum) / 10 or value > Decimal(self.maximum) / 10:
            message = (
                f"Temperature must be between {self.minimum / 10:g} "
                f"and {self.maximum / 10:g} °C for the selected mode"
            )
            raise FairlandTemperatureError(message)
        numerator, denominator = value.as_integer_ratio()
        raw, remainder = divmod(numerator * 10, denominator)
        if remainder:
            raise FairlandTemperatureError(ERR_TEMPERATURE)
        return self.validate_raw(raw)


def temperature_policy(
    state: dict[str, Any] | None, operation_mode: int | None = None
) -> TemperaturePolicy:
    """
    Intersect verified mode limits with valid, reported restrictions.

    Off retains the legacy range without turning the device on. Unknown modes
    and malformed capabilities fail closed. Missing step fields retain the
    existing one-degree default; an invalid present field never falls through.
    """
    if not isinstance(state, dict):
        raise FairlandTemperatureError(ERR_CAPABILITY)
    heating_type = state.get("type")
    mode = state.get("opMode") if operation_mode is None else operation_mode
    if (
        isinstance(heating_type, bool)
        or not isinstance(heating_type, int)
        or heating_type != FAIRLAND_TYPE
        or isinstance(mode, bool)
        or not isinstance(mode, int)
        or mode not in _MODE_LIMITS
    ):
        raise FairlandTemperatureError(ERR_CAPABILITY)
    minimum, maximum = _MODE_LIMITS[mode]
    reported_min = _positive_integer(state.get("minSp", MIN_SETPOINT))
    reported_max = _positive_integer(state.get("maxSp", MAX_SETPOINT))
    if reported_min > reported_max:
        raise FairlandTemperatureError(ERR_CAPABILITY)
    minimum = max(minimum, reported_min)
    maximum = min(maximum, reported_max)
    for field in ("stepSp", "step"):
        if field in state:
            reported_step = _positive_integer(state[field])
            if reported_step > MAX_SETPOINT - MIN_SETPOINT:
                raise FairlandTemperatureError(ERR_CAPABILITY)
    step = state.get("stepSp", state.get("step", DEFAULT_STEP))
    if minimum > maximum or step > MAX_SETPOINT - MIN_SETPOINT:
        raise FairlandTemperatureError(ERR_CAPABILITY)
    return TemperaturePolicy(minimum=minimum, maximum=maximum, step=step)
