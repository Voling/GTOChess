"""Filesystem and platform layout.

Deliberately **stdlib only**. The Docker engine stage imports this module and
``tools.fetch_stockfish`` with no third-party packages installed, so the engine
can be provisioned before any dependency resolution happens. Adding an import of
pydantic (or anything else) here breaks that stage.
"""

from __future__ import annotations

import platform
import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_ROOT.parent.parent

#: Where the Docker image places the engine fetched during the build. Nothing
#: engine-shaped is ever committed; this path is populated by the build stage.
CONTAINER_ENGINE_DIR = Path("/opt/stockfish")

#: Local (non-container) development fetch target. Git-ignored.
LOCAL_ENGINE_ROOT = REPO_ROOT / "vendor" / "stockfish"


class UnsupportedPlatform(RuntimeError):
    pass


def engine_binary_name(slug: str | None = None) -> str:
    """Executable name for a *target* platform.

    Derived from ``slug`` when given, because provisioning is cross-platform:
    a Windows host fetching the linux asset for a Docker build must not name the
    result ``stockfish.exe``. Falls back to the host when no target is stated.
    """
    if slug is not None:
        return "stockfish.exe" if slug.startswith("windows") else "stockfish"
    return "stockfish.exe" if sys.platform == "win32" else "stockfish"


def platform_slug() -> str:
    """Identifier used to select the right release asset from engine.lock.json."""
    system = platform.system().lower()
    machine = platform.machine().lower()
    if machine in {"amd64", "x86_64"}:
        arch = "x86-64"
    elif machine in {"arm64", "aarch64"}:
        arch = "arm64"
    else:
        raise UnsupportedPlatform(f"unsupported architecture: {machine}")
    return f"{system}-{arch}"


def local_engine_dir(slug: str | None = None) -> Path:
    return LOCAL_ENGINE_ROOT / (slug or platform_slug())
