"""
MCP (Model Context Protocol) client library.

Supports two transports:
- stdio: spawns MCP server as subprocess, communicates via stdin/stdout
- http: sends JSON-RPC over HTTP POST, optional SSE for streaming
"""

import json
import subprocess
import sys
import threading
from typing import Any

import requests


class MCPError(Exception):
    """Error from MCP server."""

    def __init__(self, code: int, message: str, data: Any = None):
        self.code = code
        self.message = message
        self.data = data
        super().__init__(f"MCP error {code}: {message}")


class StdioTransport:
    """Communicate with an MCP server over stdin/stdout."""

    def __init__(self, command: list[str], env: dict[str, str] | None = None):
        self._command = command
        self._env = env
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def start(self):
        self._proc = subprocess.Popen(
            self._command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self._env,
            text=True,
            bufsize=1,
        )

    def send(self, msg: dict) -> dict:
        if not self._proc or self._proc.poll() is not None:
            raise MCPError(-1, "MCP server process not running")
        line = json.dumps(msg, ensure_ascii=False) + "\n"
        with self._lock:
            self._proc.stdin.write(line)
            self._proc.stdin.flush()
            resp_line = self._proc.stdout.readline()
        if not resp_line:
            stderr = self._proc.stderr.read() if self._proc.stderr else ""
            raise MCPError(-1, f"MCP server closed stdout. stderr: {stderr.strip()}")
        return json.loads(resp_line)

    def send_notification(self, msg: dict):
        """Send a JSON-RPC notification (no response expected)."""
        if not self._proc or self._proc.poll() is not None:
            raise MCPError(-1, "MCP server process not running")
        line = json.dumps(msg, ensure_ascii=False) + "\n"
        with self._lock:
            self._proc.stdin.write(line)
            self._proc.stdin.flush()

    def close(self):
        if self._proc and self._proc.poll() is None:
            self._proc.stdin.close()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
        self._proc = None


class HttpTransport:
    """Communicate with an MCP server over HTTP."""

    def __init__(self, url: str, headers: dict[str, str] | None = None):
        self._url = url
        self._headers = headers or {}
        self._session_id: str | None = None

    def start(self):
        pass

    def send(self, msg: dict) -> dict:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **self._headers,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        resp = requests.post(self._url, json=msg, headers=headers, timeout=30)
        resp.raise_for_status()
        if "Mcp-Session-Id" in resp.headers:
            self._session_id = resp.headers["Mcp-Session-Id"]
        return resp.json()

    def send_notification(self, msg: dict):
        """Send a JSON-RPC notification (no response expected)."""
        headers = {
            "Content-Type": "application/json",
            **self._headers,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        requests.post(self._url, json=msg, headers=headers, timeout=10)

    def close(self):
        self._session_id = None


class MCPClient:
    """MCP client that wraps a transport and provides high-level methods."""

    PROTOCOL_VERSION = "2025-06-18"

    def __init__(self, name: str, transport: StdioTransport | HttpTransport):
        self.name = name
        self._transport = transport
        self._next_id = 1
        self._server_info: dict = {}
        self._server_capabilities: dict = {}

    def _request(self, method: str, params: dict | None = None) -> Any:
        msg = {
            "jsonrpc": "2.0",
            "id": self._next_id,
            "method": method,
        }
        if params:
            msg["params"] = params
        self._next_id += 1

        resp = self._transport.send(msg)
        if "error" in resp:
            err = resp["error"]
            raise MCPError(err.get("code", -1), err.get("message", "unknown"), err.get("data"))
        return resp.get("result")

    def _notify(self, method: str, params: dict | None = None):
        msg: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params:
            msg["params"] = params
        self._transport.send_notification(msg)

    def connect(self):
        """Start transport and perform MCP initialization handshake."""
        self._transport.start()
        result = self._request("initialize", {
            "protocolVersion": self.PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "tabula-mcp", "version": "1.0.0"},
        })
        self._server_info = result.get("serverInfo", {})
        self._server_capabilities = result.get("capabilities", {})
        self._notify("notifications/initialized")

    def list_tools(self) -> list[dict]:
        """Discover available tools from the server."""
        result = self._request("tools/list")
        return result.get("tools", [])

    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        """Execute a tool and return the result."""
        params: dict[str, Any] = {"name": name}
        if arguments:
            params["arguments"] = arguments
        return self._request("tools/call", params)

    def close(self):
        """Close the transport."""
        self._transport.close()
