"""Every rendered form and error must resolve to a translated string."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from custom_components.tholz_fairland.config_flow import (
    ERR_CANNOT_CONNECT,
    ERR_INVALID_TOKEN,
    _cloud_schema,
    _reauth_schema,
)

COMPONENT = Path(__file__).parents[1] / "custom_components" / "tholz_fairland"
STRINGS = COMPONENT / "strings.json"
TRANSLATIONS = sorted((COMPONENT / "translations").glob("*.json"))
# async_step_reauth only delegates, so it renders no form of its own.
STEPS_WITHOUT_A_FORM = {"reauth"}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _rendered_step_ids() -> set[str]:
    """Collect every step_id passed to async_show_form in the config flow."""
    tree = ast.parse((COMPONENT / "config_flow.py").read_text(encoding="utf-8"))
    return {
        keyword.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "attr", None) == "async_show_form"
        for keyword in node.keywords
        if keyword.arg == "step_id" and isinstance(keyword.value, ast.Constant)
    }


def _declared_step_ids() -> set[str]:
    """Collect every async_step_* method the flow defines."""
    tree = ast.parse((COMPONENT / "config_flow.py").read_text(encoding="utf-8"))
    return {
        node.name[len("async_step_") :]
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name.startswith("async_step_")
    }


def _keys(schema) -> set[str]:
    return {marker.schema for marker in schema.schema}


def test_every_rendered_step_has_config_or_options_strings():
    strings = _load(STRINGS)
    known = set(strings["config"]["step"]) | set(strings["options"]["step"])
    assert _rendered_step_ids() <= known


def test_every_declared_step_renders_or_delegates():
    """Guards against a step whose form was renamed but whose strings were not."""
    missing = _declared_step_ids() - _rendered_step_ids() - STEPS_WITHOUT_A_FORM
    assert missing == set()


def test_reauth_step_id_matches_its_strings_key():
    """The flow shows reauth_confirm; a 'reauth' key would render an empty title."""
    step = _load(STRINGS)["config"]["step"]
    assert "reauth_confirm" in step
    assert "reauth" not in step


def test_flow_error_keys_are_translated():
    errors = _load(STRINGS)["config"]["error"]
    assert ERR_CANNOT_CONNECT in errors
    assert ERR_INVALID_TOKEN in errors


@pytest.mark.parametrize(
    ("step", "schema"),
    [
        ("user", _cloud_schema()),
        ("reauth_confirm", _reauth_schema()),
    ],
)
def test_every_form_field_has_a_label(step, schema):
    data = _load(STRINGS)["config"]["step"][step]["data"]
    assert _keys(schema) <= set(data)


@pytest.mark.parametrize("path", TRANSLATIONS, ids=lambda p: p.stem)
def test_translations_match_the_strings_structure(path):
    """A missing key renders the raw identifier in the Home Assistant UI."""

    def shape(node):
        if isinstance(node, dict):
            return {key: shape(value) for key, value in sorted(node.items())}
        return None

    assert shape(_load(path)) == shape(_load(STRINGS))


@pytest.mark.parametrize("path", TRANSLATIONS, ids=lambda p: p.stem)
def test_translations_are_not_left_empty(path):
    def walk(node, trail=()):
        if isinstance(node, dict):
            for key, value in node.items():
                yield from walk(value, (*trail, key))
        else:
            yield trail, node

    blank = [trail for trail, value in walk(_load(path)) if not str(value).strip()]
    assert blank == []
