#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import sys

ROOT = os.environ.get("TABULA_HOME", os.path.expanduser("~/.tabula"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
GUARDIAN_LIB = os.path.join(ROOT, "distrib", "guardian", "skills", "guardian-lib")
if GUARDIAN_LIB not in sys.path:
    sys.path.insert(0, GUARDIAN_LIB)

from runtime import execute_guardian_code
from runtime import workspace_root_for_session


def main() -> int:
    if len(sys.argv) < 3 or sys.argv[1] != "tool" or sys.argv[2] != "execute_code":
        print("usage: run.py tool execute_code", file=sys.stderr)
        return 2

    params = json.load(sys.stdin)
    code = params.get("code", "")
    if not isinstance(code, str) or not code.strip():
        print("ERROR: missing or invalid code")
        return 1

    session = os.environ.get("TABULA_SESSION", "main")
    workspace_root = workspace_root_for_session(session) or os.environ.get("GUARDIAN_WORKSPACE_ROOT") or os.getcwd()
    output, is_error = execute_guardian_code(code, session=session, workspace_root=workspace_root)
    print(output)
    return 1 if is_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
