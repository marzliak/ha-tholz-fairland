"""Manifest compatibility regression tests."""

from __future__ import annotations

import json
from pathlib import Path


def test_boto3_version_is_managed_by_home_assistant() -> None:
    """Avoid an exact boto3 pin conflicting with Home Assistant's runtime pin."""
    manifest_path = (
        Path(__file__).parents[1]
        / "custom_components"
        / "tholz_fairland"
        / "manifest.json"
    )
    requirements = json.loads(manifest_path.read_text(encoding="utf-8"))["requirements"]

    assert "boto3" in requirements
    assert not any(requirement.startswith("boto3==") for requirement in requirements)
