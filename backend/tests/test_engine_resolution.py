"""Engine provisioning rules.

These exist because the failure they guard against is silent. If resolution ever
falls back to an arbitrary Stockfish on ``PATH``, every cached evaluation, every
sensitivity ranking and every golden-set assertion becomes a function of whatever
happened to be installed on that machine -- and nothing visibly breaks.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from fiftymoves import layout
from fiftymoves.config import EngineNotProvisioned, Settings


def _fake_binary(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    binary = directory / layout.engine_binary_name()
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    return binary


class TestEngineResolution:
    def test_explicit_override_wins(self, tmp_path: Path) -> None:
        binary = _fake_binary(tmp_path / "custom")
        assert Settings(engine_path=binary).resolve_engine_path() == binary

    def test_missing_override_fails_loudly(self, tmp_path: Path) -> None:
        with pytest.raises(EngineNotProvisioned, match="does not exist"):
            Settings(engine_path=tmp_path / "nope" / "stockfish").resolve_engine_path()

    def test_container_path_is_preferred_over_local(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        container = _fake_binary(tmp_path / "opt")
        _fake_binary(tmp_path / "local")
        monkeypatch.setattr(layout, "CONTAINER_ENGINE_DIR", tmp_path / "opt")
        monkeypatch.setattr("fiftymoves.config.CONTAINER_ENGINE_DIR", tmp_path / "opt")
        monkeypatch.setattr("fiftymoves.config.local_engine_dir", lambda: tmp_path / "local")

        assert Settings().resolve_engine_path() == container

    def test_never_falls_back_to_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The load-bearing assertion: an unprovisioned environment must raise,
        even when a perfectly good stockfish sits on PATH."""
        monkeypatch.setattr("fiftymoves.config.CONTAINER_ENGINE_DIR", Path("/nonexistent/opt"))
        monkeypatch.setattr(
            "fiftymoves.config.local_engine_dir", lambda: Path("/nonexistent/vendor")
        )
        monkeypatch.setenv("PATH", "/usr/bin:/bin")

        with pytest.raises(EngineNotProvisioned) as excinfo:
            Settings().resolve_engine_path()
        assert "PATH is intentionally not used" in str(excinfo.value)


class TestPlatformSlug:
    def test_normalises_architecture_aliases(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(layout.platform, "system", lambda: "Linux")
        monkeypatch.setattr(layout.platform, "machine", lambda: "AMD64")
        assert layout.platform_slug() == "linux-x86-64"

        monkeypatch.setattr(layout.platform, "machine", lambda: "aarch64")
        assert layout.platform_slug() == "linux-arm64"

    def test_rejects_unknown_architecture(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(layout.platform, "machine", lambda: "riscv64")
        with pytest.raises(layout.UnsupportedPlatform):
            layout.platform_slug()

    def test_binary_name_matches_host_when_no_target_given(self) -> None:
        expected = "stockfish.exe" if sys.platform == "win32" else "stockfish"
        assert layout.engine_binary_name() == expected

    def test_binary_name_follows_the_target_not_the_host(self) -> None:
        """Cross-provisioning is normal: a Windows dev machine fetches the linux
        asset for the Docker build. Naming that ``stockfish.exe`` would produce
        an image whose engine path does not exist."""
        assert layout.engine_binary_name("linux-x86-64") == "stockfish"
        assert layout.engine_binary_name("darwin-arm64") == "stockfish"
        assert layout.engine_binary_name("windows-x86-64") == "stockfish.exe"
