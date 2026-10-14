"""
Render the Tholz Fairland visual assets from their SVG sources.

Sources: docs/images/ and docs/images/brand/.
A local Chrome/Chromium renders PNGs without fetching remote assets.
Run with --check to validate the local SVG sources without rendering.
"""

from __future__ import annotations

import argparse
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
IMAGES = REPO / "docs" / "images"
BRAND_SRC = IMAGES / "brand"
BRAND_OUT = REPO / "custom_components" / "tholz_fairland" / "brand"
JOBS = [
    (IMAGES / "hero.svg", IMAGES / "hero.png", 1600, 560, 1),
    (IMAGES / "architecture.svg", IMAGES / "architecture.png", 1600, 860, 1),
    (IMAGES / "onboarding.svg", IMAGES / "onboarding.png", 1600, 760, 1),
    (IMAGES / "command-lifecycle.svg", IMAGES / "command-lifecycle.png", 1600, 800, 1),
    (IMAGES / "recovery.svg", IMAGES / "recovery.png", 1600, 760, 1),
    (IMAGES / "entities.svg", IMAGES / "entities.png", 1600, 900, 1),
    (BRAND_SRC / "icon.svg", BRAND_OUT / "icon.png", 256, 256, 1),
    (BRAND_SRC / "icon.svg", BRAND_OUT / "icon@2x.png", 256, 256, 2),
    (BRAND_SRC / "logo.svg", BRAND_OUT / "logo.png", 560, 160, 1),
    (BRAND_SRC / "logo.svg", BRAND_OUT / "logo@2x.png", 560, 160, 2),
    (BRAND_SRC / "logo-dark.svg", BRAND_OUT / "dark_logo.png", 560, 160, 1),
    (BRAND_SRC / "logo-dark.svg", BRAND_OUT / "dark_logo@2x.png", 560, 160, 2),
]
SVG_NS = "{http://www.w3.org/2000/svg}"


def check_svg(path: Path) -> None:
    """Validate a bounded, local SVG source and reject entity declarations."""
    source = path.read_bytes()
    # Only project-owned files are accepted. Reject DTD/entity expansion before
    # using the stdlib parser; no network-dependent SVG content is permitted.
    if b"<!DOCTYPE" in source.upper() or b"<!ENTITY" in source.upper():
        message = f"{path}: XML entity declarations are forbidden"
        raise SystemExit(message)
    root = ET.fromstring(source)  # noqa: S314 - DTD/entities rejected above
    if root.find(f"{SVG_NS}title") is None or root.find(f"{SVG_NS}desc") is None:
        message = f"{path}: missing accessible title or description"
        raise SystemExit(message)
    if root.get("role") != "img":
        message = f'{path}: root element needs role="img"'
        raise SystemExit(message)
    for item in root.iter():
        if item.tag.rsplit("}", 1)[-1] in {"script", "foreignObject"}:
            message = f"{path}: active SVG content is forbidden"
            raise SystemExit(message)
        for key, value in item.attrib.items():
            if key.rsplit("}", 1)[-1] == "href" and value.startswith(
                ("http:", "https:")
            ):
                message = f"{path}: remote assets are forbidden"
                raise SystemExit(message)


def find_chrome() -> str:
    """Locate the operator-selected local renderer."""
    for candidate in (
        os.environ.get("CHROME"),
        "google-chrome",
        "chromium",
        "chromium-browser",
    ):
        if candidate and (resolved := shutil.which(candidate)):
            return resolved
    message = "No Chrome/Chromium found; set CHROME to its local path"
    raise SystemExit(message)


def png_size(path: Path) -> tuple[int, int]:
    """Read PNG dimensions without an external imaging dependency."""
    with path.open("rb") as handle:
        header = handle.read(24)
    if header[:8] != b"\x89PNG\r\n\x1a\n":
        message = f"{path}: not a PNG"
        raise SystemExit(message)
    return struct.unpack(">II", header[16:24])


def render(
    chrome: str, svg: Path, out: Path, width: int, height: int, scale: int
) -> None:
    """Render one allowlisted source at its declared output dimensions."""
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tholz-render-") as profile:
        cmd = [
            chrome,
            "--headless",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-background-networking",
            "--hide-scrollbars",
            "--default-background-color=00000000",
            f"--user-data-dir={profile}",
            f"--force-device-scale-factor={scale}",
            f"--window-size={width},{height}",
            f"--screenshot={out.resolve()}",
            svg.resolve().as_uri(),
        ]
        # No shell or user-controlled SVG paths: JOBS is an explicit allowlist.
        subprocess.run(cmd, check=True, timeout=180, capture_output=True)  # noqa: S603
    expected = (width * scale, height * scale)
    actual = png_size(out)
    if actual != expected:
        message = f"{out}: expected {expected}, got {actual}"
        raise SystemExit(message)


def main() -> int:
    """Validate or render every allowlisted image."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--check", action="store_true", help="validate SVG metadata only"
    )
    args = parser.parse_args()
    svgs = sorted({job[0] for job in JOBS})
    for svg in svgs:
        check_svg(svg)
    sys.stdout.write(f"Checked {len(svgs)} SVG sources\n")
    if args.check:
        return 0
    chrome = find_chrome()
    for svg, out, width, height, scale in JOBS:
        render(chrome, svg, out, width, height, scale)
        sys.stdout.write(f"{out.relative_to(REPO)}  {width * scale}x{height * scale}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
