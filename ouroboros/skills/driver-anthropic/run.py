#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import signal
import sys
from pathlib import Path

ROOT = os.environ.get("TABULA_HOME", os.path.expanduser("~/.tabula"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from skills._ouroboros.lib import session_model_override
from skills._lib import SkillConfigError, load_skill_config
from skills._drivers.driver_runtime import DriverConfig, DriverRuntime
from skills._drivers.providers import AnthropicSession


TABULA_URL = os.environ.get("TABULA_URL", "ws://localhost:8089/ws")
VERBOSE = os.environ.get("TABULA_VERBOSE", "") == "1"


def log(msg: str) -> None:
    if VERBOSE:
        sys.stderr.write(f"[ouroboros:driver-anthropic] {msg}\n")
        sys.stderr.flush()


def load_settings() -> dict:
    settings = load_skill_config(Path(__file__).resolve().parent)
    return {
        "api_key": settings["api_key"],
        "base_url": settings["base_url"],
        "model": settings["model"],
    }


def effective_model(default_model: str, session: str) -> str:
    override = session_model_override(session)
    if override.get("provider") == "anthropic" and override.get("model"):
        return str(override["model"])
    return default_model


def main() -> None:
    parser = argparse.ArgumentParser(description="Ouroboros LLM driver (Anthropic)")
    parser.add_argument("--session", default="main", help="Session to join")
    args = parser.parse_args()

    try:
        settings = load_settings()
    except SkillConfigError as exc:
        log(f"ERROR: {exc}")
        raise SystemExit(1)

    model = effective_model(settings["model"], args.session)
    runtime = DriverRuntime(
        DriverConfig(name="anthropic", url=TABULA_URL, session=args.session),
        provider_factory=lambda prompt, tools: AnthropicSession(
            system_prompt=prompt,
            model=model,
            api_key=settings["api_key"],
            base_url=settings["base_url"],
            tools=tools,
        ),
        logger=log,
    )

    def handle_sigint(sig, frame):
        runtime.abort()
        runtime.conn.close()

    signal.signal(signal.SIGINT, handle_sigint)
    runtime.connect()
    try:
        runtime.run()
    finally:
        runtime.conn.close()


if __name__ == "__main__":
    main()
