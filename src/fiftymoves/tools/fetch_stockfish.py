"""Provision a pinned Stockfish build.

The binary is a **build input, never a repository artifact**. In Docker this runs
in a dedicated build stage that writes to ``/opt/stockfish``; locally it writes
to the git-ignored ``vendor/stockfish/<platform>/``. Either way the version and
per-platform asset names come from ``engine.lock.json``, and every download is
checksum-verified.

Stdlib only, on purpose: the Docker engine stage runs this before any
dependencies are installed. Do not import pydantic or anything else here.

Checksums use trust-on-first-use, deliberately and visibly. The first fetch of a
new version records the digest and tells you to commit it; every later fetch, on
every machine and in CI, is verified against the committed value. We do not ship
invented digests -- a wrong hardcoded checksum is worse than none, because it
teaches people to reach for ``--force``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import stat
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from fiftymoves.layout import REPO_ROOT, engine_binary_name, local_engine_dir, platform_slug

DEFAULT_LOCK_PATH = REPO_ROOT / "engine.lock.json"
_CHUNK = 1 << 20


class ProvisionError(RuntimeError):
    pass


def load_lock(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ProvisionError(f"missing lockfile: {path}")
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def save_lock(path: Path, lock: dict[str, Any]) -> None:
    path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url: str, destination: Path) -> None:
    print(f"  downloading {url}")
    with urllib.request.urlopen(url) as response, destination.open("wb") as out:
        shutil.copyfileobj(response, out)


def _extract_binary(archive: Path, into: Path, *, slug: str) -> Path:
    """Pull the single Stockfish executable out of the release archive.

    Archive members are validated rather than trusted -- a ``../`` entry must not
    escape the extraction directory -- and tar extraction uses the ``data``
    filter, which additionally rejects absolute paths, links pointing outside the
    tree, and unsafe metadata.
    """
    into.mkdir(parents=True, exist_ok=True)
    staging = into / "_extract"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()

    def assert_contained(members: list[str]) -> None:
        root = staging.resolve()
        for name in members:
            if not (staging / name).resolve().is_relative_to(root):
                raise ProvisionError(f"archive member escapes extraction dir: {name}")

    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as zf:
            assert_contained(zf.namelist())
            zf.extractall(staging)
    else:
        with tarfile.open(archive) as tf:
            assert_contained(tf.getnames())
            tf.extractall(staging, filter="data")

    candidates = [
        p
        for p in staging.rglob("*")
        if p.is_file()
        and p.name.lower().startswith("stockfish")
        and p.suffix.lower() in {"", ".exe"}
    ]
    if not candidates:
        raise ProvisionError(f"no stockfish executable found inside {archive.name}")
    # Release archives nest the binary alongside docs and source; the shortest
    # path with the plainest name is the executable.
    source = min(candidates, key=lambda p: (len(p.parts), len(p.name)))

    target = into / engine_binary_name(slug)
    if target.exists():
        target.unlink()
    shutil.move(str(source), str(target))
    shutil.rmtree(staging)

    # Only meaningful on POSIX hosts; a Windows host cross-provisioning a linux
    # binary cannot set the mode, so the Docker engine stage (which runs on
    # linux) is what actually establishes the executable bit for images.
    if sys.platform != "win32":
        target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return target


def provision(
    *,
    slug: str | None = None,
    dest: Path | None = None,
    lock_path: Path | None = None,
    force: bool = False,
    record: bool = False,
) -> Path:
    lock_path = lock_path or DEFAULT_LOCK_PATH
    lock = load_lock(lock_path)
    slug = slug or platform_slug()
    target_dir = dest or local_engine_dir(slug)

    platforms = lock.get("platforms", {})
    if slug not in platforms:
        raise ProvisionError(
            f"no entry for platform {slug!r} in {lock_path.name}. Known: {sorted(platforms)}"
        )
    entry = platforms[slug]
    url = f"{lock['base_url']}/{lock['tag']}/{entry['asset']}"

    binary = target_dir / engine_binary_name(slug)
    if binary.exists() and not force:
        print(f"already provisioned: {binary}")
        return binary

    print(f"provisioning Stockfish {lock['tag']} for {slug} -> {target_dir}")
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / entry["asset"]
        _download(url, archive)
        digest = sha256_of(archive)
        expected = entry.get("sha256")

        if expected is None:
            if not record:
                raise ProvisionError(
                    f"{lock_path.name} has no sha256 for {slug}.\n"
                    f"  observed: {digest}\n"
                    "Re-run with --record to pin it (trust-on-first-use), then commit "
                    "the lockfile so every later build -- including CI and Docker -- "
                    "is verified against it."
                )
            entry["sha256"] = digest
            save_lock(lock_path, lock)
            print(f"  recorded sha256 {digest} -- COMMIT {lock_path.name}")
        elif digest != expected:
            raise ProvisionError(
                f"checksum mismatch for {entry['asset']}\n"
                f"  expected: {expected}\n"
                f"  observed: {digest}\n"
                "Refusing to install. Do not bypass this."
            )
        else:
            print(f"  checksum ok ({digest[:16]}...)")

        result = _extract_binary(archive, target_dir, slug=slug)

    print(f"installed: {result}")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch a pinned Stockfish build.")
    parser.add_argument("--platform", dest="slug", default=None, help="override platform slug")
    parser.add_argument(
        "--dest",
        type=Path,
        default=None,
        help="install directory (default: vendor/stockfish/<platform>; Docker uses /opt/stockfish)",
    )
    parser.add_argument("--lockfile", type=Path, default=None, help="path to engine.lock.json")
    parser.add_argument("--force", action="store_true", help="re-download even if present")
    parser.add_argument(
        "--record",
        action="store_true",
        help="pin the observed checksum when the lockfile has none (first fetch only)",
    )
    args = parser.parse_args(argv)
    try:
        provision(
            slug=args.slug,
            dest=args.dest,
            lock_path=args.lockfile,
            force=args.force,
            record=args.record,
        )
    except ProvisionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
