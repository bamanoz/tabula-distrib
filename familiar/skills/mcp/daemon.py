"""
Persistent MCP server pool daemon.

Keeps MCP servers alive between tool calls. Listens on HTTP
for requests from run.py. Servers are started lazily on first use.

Configuration:
  TABULA_MCP_POOL_URL — override pool URL (e.g. http://mcp-pool:8099)
  TABULA_MCP_POOL_HOST — bind address (default: 0.0.0.0)
  TABULA_MCP_POOL_PORT — bind port (default: 0 = auto)

Protocol (JSON over HTTP POST):
  Request:  {"method": "call", "server": "name", "tool": "tool_name", "args": {...}}
  Request:  {"method": "list_tools", "server": "name"}
  Request:  {"method": "discover"}
  Response: {"ok": true, "result": ...}
  Response: {"ok": false, "error": "message"}
"""

import json
import os
import signal
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = os.environ.get("TABULA_HOME", os.path.join(os.path.expanduser("~"), ".tabula"))
SKILLS_ROOT = os.path.join(ROOT, "skills")
DISTRIB_SKILLS_ROOT = os.path.join(ROOT, "distrib", "main", "skills")
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
for p in (SKILLS_ROOT, DISTRIB_SKILLS_ROOT, os.path.dirname(os.path.dirname(os.path.abspath(__file__))), REPO_ROOT, ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

os.environ.setdefault("TABULA_HOME", ROOT)

from skills._lib import load_skill_config
from skills._lib.paths import ensure_parent, skill_run_dir

from .client import MCPError
from .pool import ClientPool

TABULA_HOME = os.environ.get("TABULA_HOME", os.path.join(os.path.expanduser("~"), ".tabula"))
POOL_URL_FILE = str(skill_run_dir("mcp") / "pool.url")


def load_mcp_settings() -> dict:
    settings = load_skill_config(Path(__file__).resolve().parent)
    return {
        "pool.url": settings["pool.url"],
        "pool.host": settings["pool.host"],
        "pool.port": settings["pool.port"],
    }


SETTINGS = load_mcp_settings()


def _get_pool_url() -> str | None:
    """Get the pool daemon's URL from env or file."""
    # Explicit override or configured remote pool URL
    url = SETTINGS["pool.url"]
    if url:
        return url
    # Read from file written by the daemon at startup
    if not os.path.isfile(POOL_URL_FILE):
        return None
    try:
        return open(POOL_URL_FILE).read().strip()
    except Exception:
        return None


def _make_handler(pool: ClientPool, lock: threading.Lock):
    """Create an HTTP request handler with access to the pool."""

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            req = json.loads(body)
            method = req.get("method", "")

            try:
                with lock:
                    if method == "call":
                        client = pool.get(req["server"])
                        result = client.call_tool(req["tool"], req.get("args", {}))
                        resp = {"ok": True, "result": result}
                    elif method == "list_tools":
                        client = pool.get(req["server"])
                        tools = client.list_tools()
                        resp = {"ok": True, "result": tools}
                    elif method == "discover":
                        tools = pool.discover_all()
                        resp = {"ok": True, "result": tools}
                    else:
                        resp = {"ok": False, "error": f"unknown method: {method}"}
            except MCPError as e:
                resp = {"ok": False, "error": str(e)}
            except Exception as e:
                resp = {"ok": False, "error": str(e)}

            payload = json.dumps(resp, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass  # Suppress request logging

    return Handler


def run_daemon():
    """Start the pool daemon on an HTTP port."""
    host = SETTINGS["pool.host"]
    port = SETTINGS["pool.port"]

    pool = ClientPool()
    lock = threading.Lock()

    server = HTTPServer((host, port), _make_handler(pool, lock))
    actual_host, actual_port = server.server_address

    # Write URL file so local clients can find us
    pool_url = f"http://{actual_host}:{actual_port}"
    with open(ensure_parent(Path(POOL_URL_FILE)), "w") as f:
        f.write(pool_url)

    print(f"mcp-pool listening on {pool_url}", file=sys.stderr)

    def shutdown(_sig, _frame):
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    try:
        server.serve_forever()
    finally:
        server.server_close()
        pool.close_all()
        if os.path.isfile(POOL_URL_FILE):
            os.unlink(POOL_URL_FILE)
        print("mcp-pool shut down", file=sys.stderr)


def pool_request(req: dict) -> dict:
    """Send a request to the running pool daemon. Returns response dict."""
    import urllib.request

    url = _get_pool_url()
    if not url:
        raise ConnectionError("pool not running")
    data = json.dumps(req, ensure_ascii=False).encode()
    http_req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(http_req, timeout=30) as resp:
        return json.loads(resp.read())


def pool_is_running() -> bool:
    """Check if the pool daemon is accepting connections."""
    import socket

    url = _get_pool_url()
    if not url:
        return False
    try:
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 80
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        sock.connect((host, port))
        sock.close()
        return True
    except Exception:
        return False


if __name__ == "__main__":
    run_daemon()
