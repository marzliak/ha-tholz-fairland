"""Offline HACS distribution contracts; online validation remains mandatory."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
COMPONENT = REPO / "custom_components" / "tholz_fairland"


def test_hacs_structure_and_metadata() -> None:
    """HACS must discover exactly one versioned, configurable integration."""
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    hacs = json.loads((REPO / "hacs.json").read_text())
    integrations = [
        path.name
        for path in COMPONENT.parent.iterdir()
        if path.is_dir() and not path.name.startswith("__")
    ]
    assert integrations == ["tholz_fairland"]
    assert manifest["domain"] == "tholz_fairland"
    assert re.fullmatch(r"\d+\.\d+\.\d+", manifest["version"])
    assert manifest["config_flow"] is True
    assert manifest["codeowners"] == ["@marzliak"]
    assert hacs["name"] == manifest["name"]
    assert hacs.get("content_in_root", False) is False
    assert hacs.get("zip_release", False) is False
    assert (COMPONENT / "brand" / "icon.png").is_file()


def test_hacs_validation_is_not_conditional_or_ignored() -> None:
    """Public pushes, dispatches and schedules must actually execute HACS."""
    workflow = yaml.safe_load(
        (REPO / ".github" / "workflows" / "validate.yml").read_text()
    )
    job = workflow["jobs"]["hacs"]
    assert "if" not in job
    assert not job.get("continue-on-error", False)
    steps = [step for step in job["steps"] if "uses" in step]
    validation = next(step for step in steps if step["uses"].startswith("hacs/action@"))
    assert validation["with"]["category"] == "integration"
    assert "ignore" not in validation["with"]
    assert "if" not in validation
    assert not validation.get("continue-on-error", False)
    assert "hassfest" in workflow["jobs"]


def test_install_guides_use_the_default_hacs_catalogue() -> None:
    """Both languages present catalogue search before manual installation."""
    guides = (
        ("README.md", "### HACS installation", "### Manual installation"),
        ("README.pt-BR.md", "### Pelo HACS", "### Pelo pacote manual"),
        ("docs/en/installation.md", "### Through HACS", "## 3. Download"),
        ("docs/installation.md", "### Pelo HACS", "## 3. Baixe"),
    )
    for name, hacs, manual in guides:
        text = (REPO / name).read_text()
        assert "hacs_repository" not in text
        assert "custom repositor" not in text.casefold()
        assert "repositório personalizado" not in text.casefold()
        assert "not yet been approved" not in text.casefold()
        assert "ainda não foi aprovada" not in text.casefold()
        flow = text[text.index(hacs) : text.index(manual)]
        assert re.search(r"(?:search for|procure por) \*\*Tholz Fairland\*\*", flow)
        assert re.search(
            r"\*\*Download\*\*.*(?:Restart|Reinicie).*"
            r"(?:Add integration|Adicionar integração)",
            flow,
            re.DOTALL,
        )
