"""MCP client pool — manages connections to multiple MCP servers."""

import json
import os
import sys

from .client import HttpTransport, MCPClient, MCPError, StdioTransport

TABULA_HOME = os.environ.get("TABULA_HOME", os.path.join(os.path.expanduser("~"), ".tabula"))
CONFIG_FILE = os.path.join(TABULA_HOME, "config", "skills", "mcp", "servers.json")


def _expand_env(value: str) -> str:
    """Expand $VAR references in strings."""
    if "$" not in value:
        return value
    return os.path.expandvars(value)


def load_config(path: str | None = None) -> dict:
    """Load MCP servers configuration."""
    path = path or CONFIG_FILE
    if not os.path.isfile(path):
        return {"servers": {}}
    with open(path) as f:
        return json.load(f)


class ClientPool:
    """Manages MCP client connections with lazy initialization."""

    def __init__(self, config: dict | None = None):
        self._config = config or load_config()
        self._clients: dict[str, MCPClient] = {}

    def _make_client(self, name: str) -> MCPClient:
        servers = self._config.get("servers", {})
        if name not in servers:
            raise MCPError(-1, f"Unknown MCP server: {name}")
        spec = servers[name]
        transport_type = spec.get("transport", "stdio")

        if transport_type == "stdio":
            command = [_expand_env(c) for c in spec["command"]]
            env = None
            if "env" in spec:
                env = {**os.environ, **{k: _expand_env(v) for k, v in spec["env"].items()}}
            transport = StdioTransport(command, env=env)
        elif transport_type == "http":
            url = _expand_env(spec["url"])
            headers = {k: _expand_env(v) for k, v in spec.get("headers", {}).items()}
            transport = HttpTransport(url, headers=headers)
        else:
            raise MCPError(-1, f"Unknown transport: {transport_type}")

        return MCPClient(name, transport)

    def get(self, name: str) -> MCPClient:
        """Get or create a connected MCP client."""
        if name not in self._clients:
            client = self._make_client(name)
            client.connect()
            self._clients[name] = client
        return self._clients[name]

    def server_names(self) -> list[str]:
        """List configured server names."""
        return list(self._config.get("servers", {}).keys())

    def discover_all(self) -> dict[str, list[dict]]:
        """Connect to all servers and list their tools."""
        result = {}
        for name in self.server_names():
            try:
                client = self.get(name)
                result[name] = client.list_tools()
            except Exception as e:
                print(f"warning: MCP server {name!r} failed: {e}", file=sys.stderr)
        return result

    def close_all(self):
        """Close all client connections."""
        for client in self._clients.values():
            try:
                client.close()
            except Exception:
                pass
        self._clients.clear()
