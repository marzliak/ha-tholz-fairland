"""Manifest compatibility regression tests."""

from __future__ import annotations

import json
from pathlib import Path

MANIFEST_PATH = (
    Path(__file__).parents[1] / "custom_components" / "tholz_fairland" / "manifest.json"
)


def _manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def test_boto3_version_is_managed_by_home_assistant() -> None:
    """Avoid an exact boto3 pin conflicting with Home Assistant's runtime pin."""
    requirements = _manifest()["requirements"]

    assert "boto3" in requirements
    assert not any(requirement.startswith("boto3==") for requirement in requirements)


def test_pycognito_is_declared_but_never_pinned() -> None:
    """Pycognito ships with core via hass-nabucasa; an exact pin would clash."""
    requirements = _manifest()["requirements"]

    assert "pycognito" in requirements
    assert not any(
        requirement.startswith("pycognito==") for requirement in requirements
    )


def test_no_third_party_debug_loggers_are_enabled() -> None:
    """botocore/boto3/urllib3 at DEBUG would log tokens; never opt in via manifest."""
    manifest = _manifest()
    loggers = manifest.get("loggers", [])
    assert not ({"botocore", "boto3", "urllib3"} & set(loggers))
