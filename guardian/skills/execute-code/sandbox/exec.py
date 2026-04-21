#!/usr/bin/env python3
"""In-container entrypoint for guardian execute_code calls.

Invoked via `docker exec -i <container> python /sandbox/exec.py`.
Reads JSON {"code": "..."} from stdin, runs prelude+code, prints output.

The prelude wires up `ws`, `scratchpad`, persistent locals — same contract as
the original local runtime. State files live under /state (host-mounted), the
workspace lives under /workspace (host-mounted).
"""

from __future__ import annotations

import builtins
import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, "/sandbox")
from workspace import GuardianWorkspace  # noqa: E402

SCRATCHPAD = Path("/state/scratchpad.json")
LOCALS = Path("/state/locals.json")
ANSWER = Path("/state/answer.json")
TRACKING = Path("/state/tracking.json")


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _save_json(path: Path, data) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def main() -> int:
    payload = json.load(sys.stdin)
    code = payload.get("code", "")
    if not isinstance(code, str) or not code.strip():
        print("ERROR: missing or invalid code")
        return 1

    # Touch activity marker so the container's idle-guard keeps it alive.
    try:
        import time as _time
        Path("/state/last_activity").write_text(str(int(_time.time())), encoding="utf-8")
    except OSError:
        pass

    scratchpad = _load_json(SCRATCHPAD, {})
    state = _load_json(LOCALS, {})

    ws = GuardianWorkspace(
        workspace_root="/workspace",
        answer_file=str(ANSWER),
        tracking_file=str(TRACKING),
    )

    # Inject preloaded names. Pre-imported modules to mirror bitgn behaviour.
    import os, re, csv, math, hashlib, base64  # noqa
    from datetime import datetime, timedelta, date  # noqa
    from collections import defaultdict, Counter  # noqa
    from pathlib import PurePosixPath  # noqa
    try:
        import yaml  # noqa
    except ImportError:
        yaml = None  # type: ignore
    try:
        from dateutil import parser as dateutil_parser  # noqa
        from dateutil.relativedelta import relativedelta  # noqa
    except ImportError:
        dateutil_parser = None  # type: ignore
        relativedelta = None  # type: ignore

    namespace: dict = {
        "__name__": "__guardian__",
        "ws": ws,
        "workspace": ws,
        "scratchpad": scratchpad,
        "json": json,
        "sys": sys,
        "os": os,
        "re": re,
        "csv": csv,
        "math": math,
        "hashlib": hashlib,
        "base64": base64,
        "datetime": datetime,
        "timedelta": timedelta,
        "date": date,
        "defaultdict": defaultdict,
        "Counter": Counter,
        "PurePosixPath": PurePosixPath,
        "yaml": yaml,
        "dateutil_parser": dateutil_parser,
        "relativedelta": relativedelta,
    }
    builtins.scratchpad = scratchpad

    # Restore user-defined persistent variables.
    prelude_keys = set(namespace.keys())
    for k, v in state.items():
        if k not in prelude_keys:
            namespace[k] = v

    exit_code = 0
    try:
        exec(compile(code, "<execute_code>", "exec"), namespace)
    except SystemExit as exc:
        exit_code = int(exc.code or 0)
    except BaseException:
        traceback.print_exc()
        exit_code = 1

    # Persist scratchpad + JSON-serializable locals.
    _save_json(SCRATCHPAD, scratchpad)
    new_state: dict = {}
    for k, v in namespace.items():
        if k.startswith("_") or k in prelude_keys:
            continue
        try:
            json.dumps(v)
            new_state[k] = v
        except (TypeError, ValueError):
            pass
    _save_json(LOCALS, new_state)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
