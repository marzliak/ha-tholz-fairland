"""Behavioral tests for the local release builder (no network, no publication)."""

from __future__ import annotations

import hashlib
import importlib.util
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "build_release", REPO / "scripts" / "build_release.py"
)
assert _SPEC is not None
assert _SPEC.loader is not None
build_release_module = importlib.util.module_from_spec(_SPEC)
# Dataclasses resolve their module through sys.modules during class creation.
sys.modules[_SPEC.name] = build_release_module
_SPEC.loader.exec_module(build_release_module)

ReleaseBuildError = build_release_module.ReleaseBuildError
build_release = build_release_module.build_release
VERSION = build_release_module.read_manifest_version(REPO)


@pytest.fixture
def repo_copy(tmp_path: Path) -> Path:
    """Copy the working tree without VCS metadata or bytecode into a scratch dir."""
    destination = tmp_path / "repo"
    shutil.copytree(
        REPO,
        destination,
        ignore=shutil.ignore_patterns(
            ".git", "__pycache__", ".pytest_cache", ".ruff_cache"
        ),
    )
    return destination


def _names(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        return archive.namelist()


def test_integration_archive_contains_only_component_tree_and_license(
    tmp_path: Path,
) -> None:
    out = tmp_path / "out"
    build_release(REPO, out)
    names = _names(out / f"tholz-fairland-{VERSION}-integration.zip")

    assert "custom_components/tholz_fairland/manifest.json" in names
    assert "LICENSE" in names
    assert all(
        name == "LICENSE" or name.startswith("custom_components/tholz_fairland/")
        for name in names
    )
    assert not any(
        name.startswith(("tests/", "scripts/", ".github/")) for name in names
    )


def test_source_archive_is_rooted_at_one_versioned_directory(tmp_path: Path) -> None:
    out = tmp_path / "out"
    build_release(REPO, out)
    names = _names(out / f"tholz-fairland-{VERSION}-source.zip")
    root = f"ha-tholz-fairland-{VERSION}/"

    assert names
    assert all(name.startswith(root) for name in names)
    assert f"{root}tests/test_release_builder.py" in names
    assert f"{root}scripts/build_release.py" in names
    assert f"{root}requirements-ha202611.txt" in names
    assert not any(".git/" in name or "__pycache__" in name for name in names)


def test_builds_are_byte_identical_across_output_directories(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    digests_a = build_release(REPO, first)
    digests_b = build_release(REPO, second)

    assert digests_a == digests_b
    for name in digests_a:
        assert (first / name).read_bytes() == (second / name).read_bytes()
    assert (first / "SHA256SUMS.txt").read_bytes() == (
        second / "SHA256SUMS.txt"
    ).read_bytes()


def test_checksum_file_matches_the_archive_bytes(tmp_path: Path) -> None:
    out = tmp_path / "out"
    build_release(REPO, out)
    lines = (out / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines()

    assert len(lines) == 2
    for line in lines:
        digest, name = line.split("  ", 1)
        assert digest == hashlib.sha256((out / name).read_bytes()).hexdigest()


def test_zip_entries_have_fixed_timestamps_and_regular_modes(tmp_path: Path) -> None:
    out = tmp_path / "out"
    build_release(REPO, out)
    with zipfile.ZipFile(out / f"tholz-fairland-{VERSION}-integration.zip") as archive:
        infos = archive.infolist()
    assert infos
    for info in infos:
        assert info.date_time == (1980, 1, 1, 0, 0, 0)
        assert (info.external_attr >> 16) & 0o777 == 0o644


def test_bytecode_and_cache_files_are_excluded(repo_copy: Path, tmp_path: Path) -> None:
    cache = repo_copy / "custom_components" / "tholz_fairland" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "config_flow.cpython-313.pyc").write_bytes(b"\x00bytecode")

    out = tmp_path / "out"
    build_release(repo_copy, out)

    assert not any(
        "__pycache__" in name
        for name in _names(out / f"tholz-fairland-{VERSION}-integration.zip")
    )
    assert not any(
        name.endswith(".pyc")
        for name in _names(out / f"tholz-fairland-{VERSION}-source.zip")
    )


@pytest.mark.parametrize(
    "relative",
    [
        "tests/session-capture.jsonl",
        "tests/fixture-client-secret.txt",
        "docs/vendor-app.apk",
        "tests/.env",
    ],
)
def test_sensitive_or_vendor_material_fails_the_build(
    repo_copy: Path, tmp_path: Path, relative: str
) -> None:
    target = repo_copy / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"synthetic-placeholder")

    with pytest.raises(ReleaseBuildError, match="forbidden file"):
        build_release(repo_copy, tmp_path / "out")
    assert not (tmp_path / "out" / f"tholz-fairland-{VERSION}-source.zip").exists()


def test_version_mismatch_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ReleaseBuildError, match="differs from manifest"):
        build_release(REPO, tmp_path / "out", version="9.9.9")


def test_malformed_version_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ReleaseBuildError, match=r"MAJOR\.MINOR\.PATCH"):
        build_release(REPO, tmp_path / "out", version="0.3")


def test_missing_required_file_is_refused(repo_copy: Path, tmp_path: Path) -> None:
    (repo_copy / "LICENSE").unlink()
    with pytest.raises(ReleaseBuildError, match="required file missing: LICENSE"):
        build_release(repo_copy, tmp_path / "out")


def test_non_empty_output_directory_is_refused(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "existing.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(ReleaseBuildError, match="empty or absent"):
        build_release(REPO, out)
    assert (out / "existing.txt").read_text(encoding="utf-8") == "keep"


def test_output_inside_the_repository_is_refused() -> None:
    with pytest.raises(ReleaseBuildError, match="outside the repository"):
        build_release(REPO, REPO / "dist-test-output")
    assert not (REPO / "dist-test-output").exists()


def test_cli_returns_nonzero_and_writes_nothing_on_failure(tmp_path: Path) -> None:
    out = tmp_path / "out"
    status = build_release_module.main(
        ["--repo", str(REPO), "--out-dir", str(out), "--version", "9.9.9"]
    )
    assert status == 1
    assert not out.exists() or not any(out.iterdir())
