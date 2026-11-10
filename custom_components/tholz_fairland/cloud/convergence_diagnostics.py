"""Bounded, allowlisted observations; never decide command convergence."""

from collections.abc import Callable
import math
import time

_CONTROLS = ("sp", "opMode", "fanMode")
_PHASES = ("apply", "restore", "noop", "unspecified")
_OUTCOMES = ("confirmed", "timeout", "error")
_MAX_COUNT = 1_000_000
_MAX_ELAPSED_MS = 86_400_000
_MAX_SETPOINT = 1000
_MISSING = object()


def _label(value: object, allowed: tuple[str, ...], fallback: str) -> str:
    """Return only a literal label, never an arbitrary input string."""
    if type(value) is str:
        for label in allowed:
            if value == label:
                return label
    return fallback


def _select(value: object, names: tuple[str, ...]) -> dict:
    """Inspect a builtin mapping shallowly without invoking foreign key methods."""
    if type(value) is not dict:
        return {}
    selected = {}
    for key, item in value.items():
        if type(key) is str:
            for name in names:
                if key == name:
                    selected[name] = item
                    break
        if len(selected) == len(names):
            break
    return selected


def _child(value: object, name: str) -> object:
    return _select(value, (name,)).get(name, _MISSING)


def _shape(value: object) -> str:
    if value is _MISSING:
        return "absent"
    if value is None:
        return "null"
    if type(value) is dict:
        return "mapping" if len(value) else "empty"
    return "malformed"


def _truthy(value: object) -> bool | None:
    """Describe builtin JSON truthiness without calling a foreign __bool__."""
    if value is _MISSING or value is None:
        return False
    if type(value) in (dict, list, str, int, float, bool):
        return bool(value)
    return None


def _controls(value: object) -> tuple[dict, list[str], list[str]]:
    selected = _select(value, _CONTROLS)
    valid = {}
    missing = []
    invalid = []
    for name in _CONTROLS:
        item = selected.get(name, _MISSING)
        if item is _MISSING:
            missing.append(name)
            continue
        if type(item) is int and (
            (name == "sp" and 0 <= item <= _MAX_SETPOINT)
            or (name == "opMode" and item in (0, 2, 4, 5))
            or (name == "fanMode" and item in (0, 1))
        ):
            valid[name] = item
        else:
            invalid.append(name)
    return valid, missing, invalid


def _other_count(value: object, known_count: int) -> int:
    if type(value) is not dict:
        return 0
    return min(_MAX_COUNT, len(value) - known_count)


def _delta_summary(delta: object, requested: dict) -> dict:
    heatings = _child(delta, "heatings")
    heat0 = _child(heatings, "heat0")
    controls, missing, invalid = _controls(heat0)
    present = [name for name in _CONTROLS if name not in missing]
    requested_count = sum(name in requested for name in present)
    return {
        "shape": _shape(delta),
        "truthy": _truthy(delta),
        "heatings_shape": _shape(heatings),
        "heat0_shape": _shape(heat0),
        "controls": controls,
        "missing": missing,
        "invalid": invalid,
        "requested_control_count": requested_count,
        "other_control_count": len(present) - requested_count,
        "other_field_count": _other_count(heat0, len(present)),
        "other_heating_count": _other_count(heatings, int(heat0 is not _MISSING)),
        "other_branch_count": _other_count(delta, int(heatings is not _MISSING)),
    }


def _boolean(value: object) -> bool | None:
    return value if type(value) is bool else None


def _reason(*, reported_matches: bool | None, delta_empty: bool | None) -> str:
    if reported_matches is None or delta_empty is None:
        return "unclassified"
    if reported_matches:
        return "confirmed" if delta_empty else "pending_delta"
    return "reported_mismatch" if delta_empty else "reported_mismatch_and_pending_delta"


def _copy_safe(value):
    """Copy only our fixed-depth, sanitized builtin output, never raw input."""
    if type(value) is dict:
        return {key: _copy_safe(item) for key, item in value.items()}
    if type(value) is list:
        return [_copy_safe(item) for item in value]
    return value


class ConvergenceTrace:
    """Retain first, first reported match and last, independent of acceptance."""

    def __init__(
        self,
        requested: dict,
        *,
        phase: str,
        clock: Callable = time.perf_counter,
    ) -> None:
        self._requested, self._requested_missing, self._requested_invalid = _controls(
            requested
        )
        self._requested_shape = _shape(requested)
        self._phase = _label(phase, _PHASES, "unspecified")
        self._clock = clock
        self._started = self._read_clock()
        self._elapsed_ms = 0
        self._sample_count = 0
        self._ever_reported_match = False
        self._ever_delta_empty = False
        self._first = None
        self._first_reported_match = None
        self._last = None
        self._finished = None

    def _read_clock(self) -> int | float | None:
        try:
            value = self._clock()
            if type(value) in (int, float) and math.isfinite(value):
                return value
        except Exception:
            # Instrumentation must not replace the client's primary exception.
            return None
        return None

    def _elapsed(self) -> int:
        now = self._read_clock()
        if now is not None:
            if self._started is None:
                self._started = now
            seconds = now - self._started
            if seconds >= _MAX_ELAPSED_MS / 1000:
                self._elapsed_ms = _MAX_ELAPSED_MS
            elif seconds > 0:
                self._elapsed_ms = max(self._elapsed_ms, int(seconds * 1000))
        return self._elapsed_ms

    def observe(
        self,
        document,
        *,
        reported_matches: bool | None,
        delta_empty: bool | None,
    ) -> None:
        """Record canonical predicate results without recomputing success."""
        if self._finished is not None:
            return
        state = _child(document, "state")
        reported = _child(state, "reported")
        heatings = _child(reported, "heatings")
        heat0 = _child(heatings, "heat0")
        controls, missing, invalid = _controls(heat0)
        reported_matches = _boolean(reported_matches)
        delta_empty = _boolean(delta_empty)
        sample = {
            "document_shape": _shape(document),
            "state_shape": _shape(state),
            "reported_shape": _shape(reported),
            "reported_heatings_shape": _shape(heatings),
            "reported_heat0_shape": _shape(heat0),
            "reported": controls,
            "reported_missing": missing,
            "reported_invalid": invalid,
            "reported_matches": reported_matches,
            "delta_empty": delta_empty,
            "reason": _reason(
                reported_matches=reported_matches, delta_empty=delta_empty
            ),
            "delta": _delta_summary(_child(state, "delta"), self._requested),
            "elapsed_ms": self._elapsed(),
        }
        self._sample_count = min(_MAX_COUNT, self._sample_count + 1)
        self._ever_reported_match |= reported_matches is True
        self._ever_delta_empty |= delta_empty is True
        if self._first is None:
            self._first = sample
        if reported_matches is True and self._first_reported_match is None:
            self._first_reported_match = sample
        self._last = sample

    def finish(self, outcome: str) -> dict:
        """Freeze once and return a fresh independent sanitized copy each time."""
        if self._finished is None:
            self._finished = {
                "schema_version": 1,
                "phase": self._phase,
                "outcome": _label(outcome, _OUTCOMES, "error"),
                "requested": self._requested,
                "requested_shape": self._requested_shape,
                "requested_missing": self._requested_missing,
                "requested_invalid": self._requested_invalid,
                "sample_count": self._sample_count,
                "elapsed_ms": self._elapsed(),
                "ever_reported_match": self._ever_reported_match,
                "ever_delta_empty": self._ever_delta_empty,
                "first": self._first,
                "first_reported_match": self._first_reported_match,
                "last": self._last,
            }
        return {key: _copy_safe(value) for key, value in self._finished.items()}
