#!/usr/bin/env python3
"""Shared provider selection for Tabula gateways and bootstrap."""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

from skills.lib.paths import skills_dir
from skills.lib.config import SkillConfigError, load_global_config, load_skill_config


PROVIDER_ALIASES = {
    "anthropic": "anthropic",
    "claude": "anthropic",
    "openai": "openai",
    "gpt": "openai",
    "openclaw": "openai",
    "mock": "mock",
}


class ProviderSelectionError(RuntimeError):
    pass


def _tabula_home(tabula_home: str | Path | None = None) -> Path:
    if tabula_home is not None:
        return Path(tabula_home)
    return Path(os.environ.get("TABULA_HOME", os.path.expanduser("~/.tabula")))


def configured_provider(*, tabula_home: str | Path | None = None) -> str | None:
    env_provider = os.environ.get("TABULA_PROVIDER", "").strip()
    if env_provider:
        return env_provider
    global_cfg = load_global_config(tabula_home_override=_tabula_home(tabula_home))
    provider = str(global_cfg.get("provider", "")).strip()
    return provider or None


def normalize_provider(requested: str | None, *, default_provider: str | None = None) -> str:
    raw = (requested or default_provider or "").strip().lower()
    if not raw:
        raise ProviderSelectionError("no provider configured; set TABULA_PROVIDER or pass --provider")
    provider = PROVIDER_ALIASES.get(raw)
    if not provider:
        supported = ", ".join(sorted(PROVIDER_ALIASES.keys()))
        raise ProviderSelectionError(f"unknown provider {raw!r}; supported values: {supported}")
    return provider


def provider_skill_dir(provider: str, *, tabula_home: str | Path | None = None) -> Path:
    if tabula_home is not None:
        home = _tabula_home(tabula_home)
        return home / "skills" / f"driver-{provider}"
    return skills_dir() / f"driver-{provider}"


def provider_script_path(provider: str, *, tabula_home: str | Path | None = None) -> Path:
    return provider_skill_dir(provider, tabula_home=tabula_home) / "run.py"


def ensure_provider_installed(provider: str, *, tabula_home: str | Path | None = None) -> Path:
    script = provider_script_path(provider, tabula_home=tabula_home)
    if not script.is_file():
        raise ProviderSelectionError(f"provider {provider!r} is not installed; driver script not found: {script}")
    return script


def ensure_provider_ready(provider: str, *, tabula_home: str | Path | None = None) -> dict:
    skill_dir = provider_skill_dir(provider, tabula_home=tabula_home)
    ensure_provider_installed(provider, tabula_home=tabula_home)
    try:
        return load_skill_config(skill_dir, tabula_home_override=_tabula_home(tabula_home))
    except SkillConfigError as exc:
        raise ProviderSelectionError(f"provider {provider!r} is not configured: {exc}") from exc


def resolve_provider(
    requested: str | None = None,
    *,
    tabula_home: str | Path | None = None,
    require_ready: bool = True,
    default_provider: str | None = None,
) -> str:
    if default_provider is None:
        default_provider = configured_provider(tabula_home=tabula_home)
    provider = normalize_provider(requested, default_provider=default_provider)
    ensure_provider_installed(provider, tabula_home=tabula_home)
    if require_ready:
        ensure_provider_ready(provider, tabula_home=tabula_home)
    return provider


def build_driver_command(
    provider: str,
    *,
    tabula_home: str | Path | None = None,
    python_executable: str | None = None,
) -> str:
    script = ensure_provider_installed(provider, tabula_home=tabula_home)
    python_executable = python_executable or sys.executable
    return shlex.join([python_executable, str(script)])


def resolve_driver_command(
    requested: str | None = None,
    *,
    tabula_home: str | Path | None = None,
    python_executable: str | None = None,
    default_provider: str | None = None,
) -> tuple[str, str]:
    provider = resolve_provider(
        requested,
        tabula_home=tabula_home,
        require_ready=True,
        default_provider=default_provider,
    )
    return provider, build_driver_command(provider, tabula_home=tabula_home, python_executable=python_executable)
