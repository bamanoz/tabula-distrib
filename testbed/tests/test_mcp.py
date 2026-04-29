#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import unittest

from tabula_testbed import TestbedClient


FAKE_MCP_SERVER = r'''#!/usr/bin/env python3
import json
import sys

for line in sys.stdin:
    req = json.loads(line)
    method = req.get("method")
    rid = req.get("id")
    if method == "initialize":
        result = {"serverInfo": {"name": "fake"}, "capabilities": {"tools": {}}}
    elif method == "notifications/initialized":
        continue
    elif method == "tools/list":
        result = {"tools": [{"name": "echo", "description": "Echo a message", "inputSchema": {"type": "object", "properties": {"message": {"type": "string", "description": "Message to echo"}}, "required": ["message"]}}]}
    elif method == "tools/call":
        params = req.get("params") or {}
        msg = (params.get("arguments") or {}).get("message", "")
        result = {"content": [{"type": "text", "text": msg}]}
    else:
        print(json.dumps({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "unknown"}}), flush=True)
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}), flush=True)
'''


class MCPPluginSmoke(unittest.TestCase):
    url = "ws://localhost:8089/ws"
    tabula_home = ""

    def make_client(self, name: str) -> TestbedClient:
        client = TestbedClient(self.url, name=name)
        client.connect_join("testbed-mcp")
        return client

    def write_fake_server(self) -> None:
        home = Path(self.tabula_home)
        fake = home / "data" / "testbed" / "fake_mcp_server.py"
        fake.parent.mkdir(parents=True, exist_ok=True)
        fake.write_text(FAKE_MCP_SERVER, encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        config = home / "config" / "plugins" / "mcp" / "servers.json"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(json.dumps({"servers": {"fake": {"transport": "stdio", "command": ["python3", str(fake)]}}}), encoding="utf-8")

    def test_mcp_plugin_registers_and_updates_tools(self):
        home = Path(self.tabula_home)
        self.assertFalse((home / "skills" / "mcp").exists(), "MCP must not be installed as a skill")
        self.assertTrue((home / "plugins" / "mcp" / "plugin.toml").is_file(), "MCP plugin manifest missing")
        with self.make_client("testbed-mcp") as client:
            client.wait_tools({"mcp_list_servers", "mcp_discover", "mcp_reload"}, session="testbed-mcp")
            self.assertFalse(client.has_tool("mcp__fake__echo"))
            self.write_fake_server()
            reload_result = client.call_tool("mcp_reload", {}, timeout=20).json()
            self.assertTrue(reload_result["ok"], reload_result)
            self.assertIn("mcp__fake__echo", reload_result.get("published", []))
            client.refresh_init("testbed-mcp-refresh")
            client.wait_tools({"mcp__fake__echo"}, session="testbed-mcp-refresh")
            echo = client.call_tool("mcp__fake__echo", {"message": "hello mcp"}, timeout=20).json()
            self.assertEqual(echo.get("text"), "hello mcp")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run MCP testbed smoke tests")
    parser.add_argument("--url", default="ws://localhost:8089/ws")
    parser.add_argument("--observer-url", default="http://127.0.0.1:8091/metrics")
    parser.add_argument("--home", default=os.environ.get("TABULA_HOME", ""))
    args = parser.parse_args()
    MCPPluginSmoke.url = args.url
    MCPPluginSmoke.tabula_home = args.home
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(MCPPluginSmoke))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
