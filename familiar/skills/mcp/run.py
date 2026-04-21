#!/usr/bin/env python3
"""
MCP bridge skill for Tabula.

Subcommands:
  discover             Connect to all servers, output tools as JSON
  list <server>        List tools for one server
  call <server> <tool> [args_json]  Call a tool, print result
  pool                 Start persistent MCP server pool daemon

When the pool daemon is running, call/list/discover route through it
(servers stay alive between calls). Otherwise, falls back to direct mode.
"""

import argparse
import json
import os
import sys

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
TABULA_HOME = os.environ.get("TABULA_HOME", os.path.expanduser("~/.tabula"))
SKILLS_ROOT = os.path.join(TABULA_HOME, "skills")
DISTRIB_SKILLS_ROOT = os.path.join(TABULA_HOME, "distrib", "main", "skills")
# Add deployed shared libs and the installed distrib skill root first, then the
# local parent directory so `import mcp.daemon` resolves in both repo and home layouts.
for p in (SKILLS_ROOT, DISTRIB_SKILLS_ROOT, os.path.dirname(SKILL_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from mcp.daemon import pool_is_running, pool_request
from mcp.pool import ClientPool


def _via_pool(req: dict) -> dict | None:
    """Try sending request to pool daemon. Returns None if pool is not running."""
    if not pool_is_running():
        return None
    try:
        return pool_request(req)
    except Exception:
        return None


def cmd_discover(_args):
    """Connect to all servers and output discovered tools as JSON."""
    resp = _via_pool({"method": "discover"})
    if resp is not None:
        if resp["ok"]:
            json.dump(resp["result"], sys.stdout, ensure_ascii=False)
            sys.stdout.write("\n")
        else:
            print(f"error: {resp['error']}", file=sys.stderr)
            sys.exit(1)
        return

    pool = ClientPool()
    try:
        tools = pool.discover_all()
        json.dump(tools, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
    finally:
        pool.close_all()


def cmd_list(args):
    """List tools for a specific server."""
    resp = _via_pool({"method": "list_tools", "server": args.server})
    if resp is not None:
        if resp["ok"]:
            tools = resp["result"]
        else:
            print(f"error: {resp['error']}", file=sys.stderr)
            sys.exit(1)
            return
    else:
        pool = ClientPool()
        try:
            client = pool.get(args.server)
            tools = client.list_tools()
        finally:
            pool.close_all()

    for tool in tools:
        schema = tool.get("inputSchema", {})
        params = schema.get("properties", {})
        param_str = ", ".join(
            f"{k}: {v.get('type', 'any')}" for k, v in params.items()
        )
        print(f"  {tool['name']}({param_str}) — {tool.get('description', '')}")


def cmd_call(args):
    """Call an MCP tool and print the result."""
    arguments = json.loads(args.args) if args.args else {}

    resp = _via_pool({"method": "call", "server": args.server, "tool": args.tool, "args": arguments})
    if resp is not None:
        if resp["ok"]:
            result = resp["result"]
        else:
            print(f"error: {resp['error']}", file=sys.stderr)
            sys.exit(1)
            return
    else:
        pool = ClientPool()
        try:
            client = pool.get(args.server)
            result = client.call_tool(args.tool, arguments)
        finally:
            pool.close_all()

    for content in result.get("content", []):
        ctype = content.get("type", "text")
        if ctype == "text":
            print(content.get("text", ""))
        elif ctype == "image":
            mime = content.get("mimeType", "unknown")
            data_len = len(content.get("data", ""))
            print(f"[image: {mime}, {data_len} bytes base64]")
        elif ctype == "resource":
            res = content.get("resource", {})
            print(f"[resource: {res.get('uri', 'unknown')}]")
            if "text" in res:
                print(res["text"])
        else:
            print(json.dumps(content, ensure_ascii=False))


def cmd_pool(_args):
    """Start the persistent MCP server pool daemon."""
    from mcp.daemon import run_daemon
    run_daemon()


def main():
    parser = argparse.ArgumentParser(description="MCP bridge for Tabula")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("discover", help="Discover all MCP tools")

    p_list = sub.add_parser("list", help="List tools for a server")
    p_list.add_argument("server", help="Server name from config")

    p_call = sub.add_parser("call", help="Call an MCP tool")
    p_call.add_argument("server", help="Server name")
    p_call.add_argument("tool", help="Tool name")
    p_call.add_argument("args", nargs="?", default="{}", help="JSON arguments")

    sub.add_parser("pool", help="Start persistent MCP server pool")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        "discover": cmd_discover,
        "list": cmd_list,
        "call": cmd_call,
        "pool": cmd_pool,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
