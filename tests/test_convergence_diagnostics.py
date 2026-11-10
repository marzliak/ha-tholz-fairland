"""Synthetic-only privacy and boundedness checks; no physical-device evidence."""

import gc
import inspect
import json
import time
import weakref

import pytest

from custom_components.tholz_fairland.cloud import (
    convergence_diagnostics as diagnostics,
)
from custom_components.tholz_fairland.cloud.convergence_diagnostics import (
    ConvergenceTrace,
)

NEEDLE = "SYNTHETIC_PRIVATE_NEEDLE_DO_NOT_RETAIN"
ABSENT = object()


class Sensitive:
    """Fail if diagnostics invokes arbitrary object behavior."""

    def __repr__(self):
        """Reject representation of an untrusted value."""
        raise AssertionError(NEEDLE)

    def __str__(self):
        """Reject string conversion of an untrusted value."""
        raise AssertionError(NEEDLE)

    def __bool__(self):
        """Reject truthiness of an untrusted value."""
        raise AssertionError(NEEDLE)

    def __len__(self):
        """Reject length inspection of an untrusted value."""
        raise AssertionError(NEEDLE)

    def __eq__(self, other):
        """Reject equality callbacks from unsafe dictionary lookups."""
        del other
        raise AssertionError(NEEDLE)

    def __hash__(self):
        """Collide with a known path without needing foreign comparisons."""
        return hash("state")

    def __deepcopy__(self, memo):
        """Reject copying an untrusted value."""
        del memo
        raise AssertionError(NEEDLE)

    def __float__(self):
        """Reject numeric conversion of an untrusted value."""
        raise AssertionError(NEEDLE)


class SensitiveDict(dict):
    """Builtin subclasses are not trusted mappings."""

    def items(self):
        raise AssertionError(NEEDLE)

    def get(self, *args):
        del args
        raise AssertionError(NEEDLE)

    def __bool__(self):
        """Reject truthiness of an untrusted mapping subclass."""
        raise AssertionError(NEEDLE)


class SensitiveInt(int):
    """An integer subclass is not an exact protocol integer."""


class Clock:
    def __init__(self, *values):
        self.values = iter(values)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        value = next(self.values)
        if isinstance(value, Exception):
            raise value
        return value


def document(reported=ABSENT, delta=ABSENT):
    state = {}
    if reported is not ABSENT:
        state["reported"] = {"heatings": {"heat0": reported}}
    if delta is not ABSENT:
        state["delta"] = delta
    return {"state": state}


def serialize(summary):
    encoded = json.dumps(summary, allow_nan=False, sort_keys=True)
    assert len(encoded.encode()) < 6000
    assert NEEDLE not in encoded
    assert json.loads(encoded) == summary
    return encoded


def invalid_value(kind):
    return {
        "bool": lambda: True,
        "false": lambda: False,
        "float": lambda: 330.0,
        "int_subclass": lambda: SensitiveInt(330),
        "nan": lambda: float("nan"),
        "infinity": lambda: float("inf"),
        "negative_infinity": lambda: float("-inf"),
        "huge_int": lambda: 10**10000,
        "text": lambda: NEEDLE,
        "object": Sensitive,
        "mapping": lambda: {NEEDLE: NEEDLE},
        "list": lambda: [NEEDLE],
        "null": lambda: None,
    }[kind]()


def test_empty_trace_and_frozen_independent_finish():
    clock = Clock(10, 11)
    trace = ConvergenceTrace({"sp": 330}, phase="apply", clock=clock)
    result = trace.finish("timeout")
    assert result == {
        "schema_version": 1,
        "phase": "apply",
        "outcome": "timeout",
        "requested": {"sp": 330},
        "requested_shape": "mapping",
        "requested_missing": ["opMode", "fanMode"],
        "requested_invalid": [],
        "sample_count": 0,
        "elapsed_ms": 1000,
        "ever_reported_match": False,
        "ever_delta_empty": False,
        "first": None,
        "first_reported_match": None,
        "last": None,
    }
    result["requested"]["sp"] = 400
    result["requested_missing"].clear()
    trace.observe(document({"sp": 400}), reported_matches=True, delta_empty=True)
    repeated = trace.finish("confirmed")
    assert repeated["requested"] == {"sp": 330}
    assert repeated["requested_missing"] == ["opMode", "fanMode"]
    assert repeated["sample_count"] == 0
    assert repeated["outcome"] == "timeout"
    assert clock.calls == 2
    serialize(repeated)


@pytest.mark.parametrize(
    ("matches", "empty", "reason"),
    [
        (True, True, "confirmed"),
        (True, False, "pending_delta"),
        (False, True, "reported_mismatch"),
        (False, False, "reported_mismatch_and_pending_delta"),
        (True, None, "unclassified"),
        (False, None, "unclassified"),
        (None, True, "unclassified"),
        (None, False, "unclassified"),
        (None, None, "unclassified"),
    ],
)
def test_reasons_use_only_supplied_canonical_booleans(matches, empty, reason):
    trace = ConvergenceTrace({"sp": 330}, phase="apply", clock=lambda: 0)
    # Deliberately inconsistent values: these are observations, not decisions.
    trace.observe(
        document({"sp": 100}, {"heatings": {"heat0": {"sp": 50}}}),
        reported_matches=matches,
        delta_empty=empty,
    )
    result = trace.finish("timeout")
    assert result["first"]["reason"] == reason
    assert result["first"]["reported_matches"] is matches
    assert result["first"]["delta_empty"] is empty
    assert result["ever_reported_match"] is (matches is True)
    assert result["ever_delta_empty"] is (empty is True)
    assert result["outcome"] == "timeout"
    serialize(result)


def test_transient_match_survives_hundreds_of_samples_with_three_slots():
    trace = ConvergenceTrace({"sp": 330}, phase="restore", clock=lambda: 0)
    trace.observe(document({"sp": 300}), reported_matches=False, delta_empty=False)
    trace.observe(document({"sp": 330}), reported_matches=True, delta_empty=False)
    trace.observe(document({"sp": 330}), reported_matches=True, delta_empty=True)
    for _ in range(500):
        trace.observe(document({"sp": 320}), reported_matches=False, delta_empty=False)
    retained = {id(trace._first), id(trace._first_reported_match), id(trace._last)}
    assert len(retained) == 3
    assert {
        name
        for name, value in vars(trace).items()
        if type(value) is dict and "reason" in value
    } == {"_first", "_first_reported_match", "_last"}
    assert not any(
        type(value) is list and any(type(item) is dict for item in value)
        for value in vars(trace).values()
    )
    result = trace.finish("timeout")
    assert result["sample_count"] == 503
    assert result["first"]["reported"] == {"sp": 300}
    assert result["first_reported_match"]["reported"] == {"sp": 330}
    assert result["first_reported_match"]["reason"] == "pending_delta"
    assert result["last"]["reported"] == {"sp": 320}
    assert result["ever_reported_match"] is True
    assert result["ever_delta_empty"] is True
    serialize(result)


@pytest.mark.parametrize("field", ["sp", "opMode", "fanMode"])
@pytest.mark.parametrize(
    "kind",
    [
        "bool",
        "false",
        "float",
        "int_subclass",
        "nan",
        "infinity",
        "negative_infinity",
        "huge_int",
        "text",
        "object",
        "mapping",
        "list",
        "null",
    ],
)
def test_invalid_values_are_markers_not_values(field, kind):
    value = invalid_value(kind)
    controls = {field: value, NEEDLE: value}
    trace = ConvergenceTrace(controls, phase=NEEDLE, clock=lambda: 0)
    trace.observe(
        document(controls, {"heatings": {"heat0": controls}}),
        reported_matches=None,
        delta_empty=None,
    )
    result = trace.finish(NEEDLE)
    assert result["requested"] == {}
    assert result["requested_invalid"] == [field]
    assert result["phase"] == "unspecified"
    assert result["outcome"] == "error"
    sample = result["first"]
    assert sample["reported"] == {}
    assert sample["reported_invalid"] == [field]
    assert sample["delta"]["controls"] == {}
    assert sample["delta"]["invalid"] == [field]
    assert sample["delta"]["other_field_count"] == 1
    serialize(result)


@pytest.mark.parametrize(
    ("field", "value", "valid"),
    [
        ("sp", 0, True),
        ("sp", 1000, True),
        ("sp", -1, False),
        ("sp", 1001, False),
        ("opMode", 0, True),
        ("opMode", 2, True),
        ("opMode", 4, True),
        ("opMode", 5, True),
        ("opMode", 1, False),
        ("opMode", 3, False),
        ("opMode", 6, False),
        ("fanMode", 0, True),
        ("fanMode", 1, True),
        ("fanMode", 2, False),
        ("fanMode", -1, False),
    ],
)
def test_exact_control_domains(field, value, valid):
    controls = {field: value}
    trace = ConvergenceTrace(controls, phase="noop", clock=lambda: 0)
    trace.observe(
        document(controls, {"heatings": {"heat0": controls}}),
        reported_matches=True,
        delta_empty=False,
    )
    result = trace.finish("confirmed")
    expected = controls if valid else {}
    assert result["requested"] == expected
    assert result["last"]["reported"] == expected
    assert result["last"]["delta"]["controls"] == expected
    assert result["requested_invalid"] == ([] if valid else [field])
    serialize(result)


def test_delta_requested_values_and_unknown_paths_have_only_safe_counts():
    delta = {
        "heatings": {
            "heat0": {"sp": 320, "opMode": 4, "fanMode": 1, NEEDLE: NEEDLE},
            NEEDLE: {"sp": 900, NEEDLE: NEEDLE},
        },
        NEEDLE: {"sp": 800, NEEDLE: NEEDLE},
        "sp": 700,
    }
    trace = ConvergenceTrace({"sp": 330}, phase="apply", clock=lambda: 0)
    trace.observe(
        document({"sp": 330}, delta), reported_matches=True, delta_empty=False
    )
    result = trace.finish("timeout")
    assert result["last"]["delta"] == {
        "shape": "mapping",
        "truthy": True,
        "heatings_shape": "mapping",
        "heat0_shape": "mapping",
        "controls": {"sp": 320, "opMode": 4, "fanMode": 1},
        "missing": [],
        "invalid": [],
        "requested_control_count": 1,
        "other_control_count": 2,
        "other_field_count": 1,
        "other_heating_count": 1,
        "other_branch_count": 2,
    }
    encoded = serialize(result)
    assert "900" not in encoded
    assert "800" not in encoded
    assert "700" not in encoded


@pytest.mark.parametrize(
    ("kind", "shape", "truthy"),
    [
        ("absent", "absent", False),
        ("empty", "empty", False),
        ("null", "null", False),
        ("text", "malformed", True),
        ("list", "malformed", True),
        ("bool", "malformed", True),
        ("false", "malformed", False),
        ("nan", "malformed", True),
        ("object", "malformed", None),
        ("subclass", "malformed", None),
    ],
)
def test_absent_empty_and_malformed_delta_are_distinct(kind, shape, truthy):
    if kind == "absent":
        delta = ABSENT
    elif kind == "empty":
        delta = {}
    elif kind == "subclass":
        delta = SensitiveDict({NEEDLE: NEEDLE})
    else:
        delta = invalid_value(kind)
    trace = ConvergenceTrace({}, phase="apply", clock=lambda: 0)
    trace.observe(document({}, delta), reported_matches=True, delta_empty=False)
    result = trace.finish("error")
    assert result["first"]["delta"]["shape"] == shape
    assert result["first"]["delta"]["truthy"] is truthy
    assert result["first"]["reason"] == "pending_delta"
    serialize(result)


@pytest.mark.parametrize(
    "path",
    [
        (),
        ("state",),
        ("state", "reported"),
        ("state", "reported", "heatings"),
        ("state", "reported", "heatings", "heat0"),
        ("state", "delta"),
        ("state", "delta", "heatings"),
        ("state", "delta", "heatings", "heat0"),
    ],
)
@pytest.mark.parametrize("kind", ["null", "text", "object", "list"])
def test_missing_and_malformed_branches_never_escape(path, kind):
    raw = invalid_value(kind)
    for key in reversed(path):
        raw = {key: raw}
    trace = ConvergenceTrace({}, phase="apply", clock=lambda: 0)
    trace.observe(raw, reported_matches=None, delta_empty=None)
    result = trace.finish("error")
    assert result["first"]["reported"] == {}
    assert result["first"]["reported_missing"] == ["sp", "opMode", "fanMode"]
    assert result["first"]["reason"] == "unclassified"
    serialize(result)


def test_no_raw_aliases_or_sensitive_references_retained():
    sensitive = Sensitive()
    reference = weakref.ref(sensitive)
    requested = {"sp": 330, "opMode": sensitive}
    raw = document({"sp": 320, "fanMode": sensitive}, {NEEDLE: sensitive})
    trace = ConvergenceTrace(requested, phase="apply", clock=lambda: 0)
    trace.observe(raw, reported_matches=True, delta_empty=True)
    requested["sp"] = 500
    raw["state"]["reported"]["heatings"]["heat0"]["sp"] = 700
    del requested, raw, sensitive
    gc.collect()
    assert reference() is None
    result = trace.finish("confirmed")
    assert result["requested"] == {"sp": 330}
    assert result["first"]["reported"] == {"sp": 320}
    result["first"]["reported"]["sp"] = 900
    result["first"]["delta"]["invalid"].append(NEEDLE)
    assert result["last"]["reported"] == {"sp": 320}
    assert result["first_reported_match"]["reported"] == {"sp": 320}
    repeated = trace.finish("error")
    assert repeated["first"]["reported"] == {"sp": 320}
    assert repeated["outcome"] == "confirmed"
    serialize(repeated)


def test_circular_unknown_input_is_not_copied_or_traversed():
    unknown = {NEEDLE: NEEDLE}
    unknown["cycle"] = unknown
    controls = {"sp": 330, "opMode": 0, "fanMode": 1, NEEDLE: unknown}
    trace = ConvergenceTrace(controls, phase="apply", clock=lambda: 0)
    trace.observe(
        document(controls, {"heatings": {"heat0": controls}, NEEDLE: unknown}),
        reported_matches=True,
        delta_empty=False,
    )
    result = trace.finish("timeout")
    assert result["last"]["reported"] == {"sp": 330, "opMode": 0, "fanMode": 1}
    assert result["last"]["delta"]["other_branch_count"] == 1
    assert result["last"]["delta"]["other_field_count"] == 1
    serialize(result)


@pytest.mark.parametrize("phase", ["apply", "restore", "noop", "unspecified"])
@pytest.mark.parametrize("outcome", ["confirmed", "timeout", "error"])
def test_allowlisted_phase_and_outcome_labels_are_preserved(phase, outcome):
    trace = ConvergenceTrace({}, phase=phase, clock=lambda: 0)
    result = trace.finish(outcome)
    assert result["phase"] == phase
    assert result["outcome"] == outcome
    serialize(result)


@pytest.mark.parametrize("value", [0, 1, 0.0, 1.0, "true", "false"])
def test_nonboolean_predicates_are_not_coerced(value):
    trace = ConvergenceTrace({}, phase="apply", clock=lambda: 0)
    trace.observe({}, reported_matches=value, delta_empty=value)
    result = trace.finish("error")
    assert result["first"]["reason"] == "unclassified"
    assert result["ever_reported_match"] is False
    assert result["ever_delta_empty"] is False
    serialize(result)


def test_hostile_keys_subclasses_labels_and_predicates_are_not_called():
    foreign = Sensitive()
    trace = ConvergenceTrace(SensitiveDict({"sp": 330}), phase=foreign, clock=lambda: 0)
    trace.observe({foreign: foreign}, reported_matches=foreign, delta_empty=foreign)
    result = trace.finish(foreign)
    assert result["requested"] == {}
    assert result["requested_shape"] == "malformed"
    assert result["phase"] == "unspecified"
    assert result["outcome"] == "error"
    assert result["first"]["reported_matches"] is None
    assert result["first"]["delta_empty"] is None
    serialize(result)


def test_counts_and_maximal_summary_are_bounded(monkeypatch):
    monkeypatch.setattr(diagnostics, "_MAX_COUNT", 5)
    unknown = {f"{NEEDLE}_{index}": NEEDLE for index in range(5000)}
    heat0 = {**unknown, "sp": 1000, "opMode": 5, "fanMode": 1}
    delta = {**unknown, "heatings": {**unknown, "heat0": heat0}}
    trace = ConvergenceTrace(
        heat0, phase="unspecified", clock=Clock(0, *([1e308] * 10))
    )
    for index in range(8):
        trace.observe(
            document(heat0, delta), reported_matches=index == 1, delta_empty=False
        )
    result = trace.finish("error")
    assert result["sample_count"] == 5
    assert result["elapsed_ms"] == diagnostics._MAX_ELAPSED_MS
    for slot in ("first", "first_reported_match", "last"):
        assert result[slot]["delta"]["other_field_count"] == 5
        assert result[slot]["delta"]["other_heating_count"] == 5
        assert result[slot]["delta"]["other_branch_count"] == 5
    serialize(result)


@pytest.mark.parametrize(
    "kind",
    ["nan", "infinity", "negative_infinity", "huge_int", "text", "object", "bool"],
)
def test_nonfinite_or_invalid_clocks_produce_finite_bounded_timing(kind):
    clock = Clock(invalid_value(kind), 10, invalid_value(kind), 12)
    trace = ConvergenceTrace({}, phase="apply", clock=clock)
    trace.observe({}, reported_matches=None, delta_empty=None)
    trace.observe({}, reported_matches=None, delta_empty=None)
    result = trace.finish("timeout")
    assert result["first"]["elapsed_ms"] == 0
    assert result["last"]["elapsed_ms"] == 0
    assert result["elapsed_ms"] == 2000
    serialize(result)


def test_clock_regression_overflow_and_exception_do_not_escape():
    clock = Clock(-1e308, 1e308, -1e308, RuntimeError(NEEDLE), 0)
    trace = ConvergenceTrace({}, phase="restore", clock=clock)
    for _ in range(3):
        trace.observe({}, reported_matches=False, delta_empty=False)
    result = trace.finish("error")
    assert result["first"]["elapsed_ms"] == diagnostics._MAX_ELAPSED_MS
    assert result["last"]["elapsed_ms"] == diagnostics._MAX_ELAPSED_MS
    assert result["elapsed_ms"] == diagnostics._MAX_ELAPSED_MS
    serialize(result)


def test_clock_regression_does_not_reduce_elapsed():
    trace = ConvergenceTrace({}, phase="apply", clock=Clock(10, 12, 11, 13))
    trace.observe({}, reported_matches=False, delta_empty=False)
    trace.observe({}, reported_matches=False, delta_empty=False)
    result = trace.finish("timeout")
    assert result["first"]["elapsed_ms"] == 2000
    assert result["last"]["elapsed_ms"] == 2000
    assert result["elapsed_ms"] == 3000
    serialize(result)


def test_default_clock_is_perf_counter_and_never_uses_monotonic(monkeypatch):
    assert (
        inspect.signature(ConvergenceTrace).parameters["clock"].default
        is time.perf_counter
    )

    def forbidden():
        raise AssertionError(NEEDLE)

    with monkeypatch.context() as patch:
        patch.setattr(time, "monotonic", forbidden)
        trace = ConvergenceTrace({}, phase="noop")
        trace.observe({}, reported_matches=False, delta_empty=True)
        result = trace.finish("error")
    assert 0 <= result["elapsed_ms"] <= diagnostics._MAX_ELAPSED_MS
    serialize(result)
