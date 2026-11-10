"""Build local, deterministic release archives. Never tags, uploads or publishes."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

DOMAIN = "tholz_fairland"
INTEGRATION_PREFIX = f"custom_components/{DOMAIN}"
MANIFEST_PATH = f"{INTEGRATION_PREFIX}/manifest.json"
INTEGRATION_EXTRA_FILES = ("LICENSE",)
SOURCE_TOP_FILES = (
    ".gitattributes",
    ".gitignore",
    ".ruff.toml",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "README.md",
    "README.pt-BR.md",
    "SECURITY.md",
    "hacs.json",
    "requirements.txt",
    "requirements-ha2026.txt",
    "requirements-ha202611.txt",
    "pyproject.toml",
)
SOURCE_TREES = (".github", "custom_components", "docs", "examples", "scripts", "tests")
REQUIRED_FILES = (
    MANIFEST_PATH,
    "CHANGELOG.md",
    "LICENSE",
    "README.md",
    "hacs.json",
    "requirements.txt",
)
SKIPPED_DIR_NAMES = frozenset({"__pycache__", ".pytest_cache", ".ruff_cache"})
SKIPPED_SUFFIXES = frozenset({".pyc", ".pyo"})
SKIPPED_NAMES = frozenset({".DS_Store"})
FORBIDDEN_SUFFIXES = frozenset(
    {
        ".apk",
        ".xapk",
        ".pcap",
        ".pcapng",
        ".jsonl",
        ".pem",
        ".key",
        ".p12",
        ".sqlite",
        ".db",
        ".log",
    }
)
FORBIDDEN_NAME_PARTS = ("client_secret", "secrets.json")
GIT_DIR = ".git"
FIXED_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
FILE_ATTRIBUTES = 0o644 << 16
UNIX_CREATE_SYSTEM = 3
CHECKSUMS_NAME = "SHA256SUMS.txt"
VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
DEFAULT_REPO = Path(__file__).resolve().parents[1]


class ReleaseBuildError(RuntimeError):
    """The release cannot be built safely from the current tree."""


@dataclass(frozen=True)
class Member:
    """One archive entry: its archive name and the repository file it comes from."""

    arcname: str
    path: Path


def integration_zip_name(version: str) -> str:
    """Return the integration archive file name for a version."""
    return f"tholz-fairland-{version}-integration.zip"


def source_zip_name(version: str) -> str:
    """Return the source archive file name for a version."""
    return f"tholz-fairland-{version}-source.zip"


def source_root(version: str) -> str:
    """Return the single top-level directory inside the source archive."""
    return f"ha-tholz-fairland-{version}"


def _is_skipped(relative: PurePosixPath) -> bool:
    if any(part in SKIPPED_DIR_NAMES for part in relative.parts[:-1]):
        return True
    return relative.name in SKIPPED_NAMES or relative.suffix in SKIPPED_SUFFIXES


def _reject_forbidden(relative: PurePosixPath) -> None:
    name = relative.name.casefold().replace("-", "_")
    if (
        relative.suffix.casefold() in FORBIDDEN_SUFFIXES
        or name.startswith(".env")
        or any(part in name for part in FORBIDDEN_NAME_PARTS)
        or GIT_DIR in relative.parts
    ):
        msg = f"forbidden file in release allowlist: {relative.as_posix()}"
        raise ReleaseBuildError(msg)


def _collect_tree(repo: Path, rel_dir: str) -> list[Member]:
    root = repo / rel_dir
    if not root.is_dir():
        msg = f"missing allowlisted directory: {rel_dir}"
        raise ReleaseBuildError(msg)
    members: list[Member] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            msg = f"symlinks are not released: {path.relative_to(repo).as_posix()}"
            raise ReleaseBuildError(msg)
        if path.is_dir():
            continue
        relative = PurePosixPath(path.relative_to(repo).as_posix())
        if _is_skipped(relative):
            continue
        _reject_forbidden(relative)
        members.append(Member(relative.as_posix(), path))
    return members


def _collect_file(repo: Path, rel_path: str) -> Member:
    path = repo / rel_path
    if not path.is_file() or path.is_symlink():
        msg = f"missing or non-regular allowlisted file: {rel_path}"
        raise ReleaseBuildError(msg)
    relative = PurePosixPath(rel_path)
    _reject_forbidden(relative)
    return Member(rel_path, path)


def read_manifest_version(repo: Path) -> str:
    """Return the integration version declared in its manifest."""
    manifest = json.loads((repo / MANIFEST_PATH).read_text(encoding="utf-8"))
    version = manifest.get("version")
    if not isinstance(version, str):
        msg = "manifest.json has no string version"
        raise ReleaseBuildError(msg)
    return version


def integration_members(repo: Path) -> list[Member]:
    """Return the integration ZIP allowlist: the component tree plus LICENSE."""
    _require(repo)
    members = _collect_tree(repo, INTEGRATION_PREFIX)
    members.extend(_collect_file(repo, name) for name in INTEGRATION_EXTRA_FILES)
    return sorted(members, key=lambda member: member.arcname)


def source_members(repo: Path, version: str) -> list[Member]:
    """Return the source ZIP allowlist, rooted at ``ha-tholz-fairland-<version>``."""
    _require(repo)
    root = source_root(version)
    members = [_collect_file(repo, name) for name in SOURCE_TOP_FILES]
    for tree in SOURCE_TREES:
        members.extend(_collect_tree(repo, tree))
    prefixed = [Member(f"{root}/{member.arcname}", member.path) for member in members]
    return sorted(prefixed, key=lambda member: member.arcname)


def _require(repo: Path) -> None:
    for rel in REQUIRED_FILES:
        if not (repo / rel).is_file():
            msg = f"required file missing: {rel}"
            raise ReleaseBuildError(msg)


def _write_zip(target: Path, members: list[Member]) -> None:
    with zipfile.ZipFile(target, "w") as archive:
        for member in members:
            info = zipfile.ZipInfo(member.arcname, date_time=FIXED_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = FILE_ATTRIBUTES
            info.create_system = UNIX_CREATE_SYSTEM
            archive.writestr(info, member.path.read_bytes())


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _check_output_dir(repo: Path, out_dir: Path) -> None:
    resolved = out_dir.resolve()
    if resolved == repo.resolve() or repo.resolve() in resolved.parents:
        msg = "output directory must be outside the repository"
        raise ReleaseBuildError(msg)
    if out_dir.exists() and (not out_dir.is_dir() or any(out_dir.iterdir())):
        msg = "output directory must be empty or absent"
        raise ReleaseBuildError(msg)
    out_dir.mkdir(parents=True, exist_ok=True)


def build_release(
    repo: Path, out_dir: Path, version: str | None = None
) -> dict[str, str]:
    """Write both archives and SHA256SUMS.txt; return ``{file name: sha256}``."""
    repo = repo.resolve()
    declared = read_manifest_version(repo)
    chosen = version or declared
    if not VERSION_PATTERN.fullmatch(chosen):
        msg = f"version must be MAJOR.MINOR.PATCH, got {chosen!r}"
        raise ReleaseBuildError(msg)
    if chosen != declared:
        msg = f"requested version {chosen} differs from manifest version {declared}"
        raise ReleaseBuildError(msg)

    integration = integration_members(repo)
    source = source_members(repo, chosen)
    _check_output_dir(repo, out_dir)

    integration_path = out_dir / integration_zip_name(chosen)
    source_path = out_dir / source_zip_name(chosen)
    _write_zip(integration_path, integration)
    _write_zip(source_path, source)

    digests = {
        integration_path.name: _sha256(integration_path),
        source_path.name: _sha256(source_path),
    }
    lines = [f"{digests[name]}  {name}\n" for name in sorted(digests)]
    (out_dir / CHECKSUMS_NAME).write_text("".join(lines), encoding="utf-8")
    return digests


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point; returns a process exit status."""
    parser = argparse.ArgumentParser(
        description="Build local release archives without publishing them."
    )
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    parser.add_argument("--version", default=None)
    args = parser.parse_args(argv)
    try:
        digests = build_release(args.repo, args.out_dir, args.version)
    except ReleaseBuildError as exc:
        sys.stderr.write(f"build_release: {exc}\n")
        return 1
    for name in sorted(digests):
        sys.stdout.write(f"{digests[name]}  {name}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
