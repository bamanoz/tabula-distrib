---
name: mcp
description: "MCP bridge — connects to external MCP servers (filesystem, APIs, databases). Call: `EXEC python3 skills/mcp/run.py call <server> <tool> '<json_args>'`. List: `EXEC python3 skills/mcp/run.py list <server>`. Config: ~/.tabula/config/skills/mcp/servers.json"
requires-kernel-tools: ["shell_exec"]
---
# MCP Bridge

Connects Tabula to [Model Context Protocol](https://modelcontextprotocol.io) servers.

## Run

```bash
python3 skills/mcp/run.py discover
python3 skills/mcp/run.py pool
```

## Config File

Path:

    ~/.tabula/config/global.toml

Example:

```toml
[mcp.pool]
url = ""
host = "0.0.0.0"
port = 0
```

MCP server definitions live in:

    ~/.tabula/config/skills/mcp/servers.json

Example:

```json
{
  "servers": {
    "filesystem": {
      "transport": "stdio",
      "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    },
    "fetch": {
      "transport": "stdio",
      "command": ["uvx", "mcp-server-fetch"]
    },
    "remote-api": {
      "transport": "http",
      "url": "https://api.example.com/mcp"
    }
  }
}
```

## Secrets

This skill has no schema-defined secrets.

## Configuration

| Key | Type | Default | Secret | Canonical env | Aliases | Notes |
|---|---|---|---|---|---|---|
| `pool.url` | `string` | `""` | no | `TABULA_SKILL_MCP_POOL_URL` | `TABULA_MCP_POOL_URL` | Overrides local pool discovery and routes clients to a remote pool |
| `pool.host` | `string` | `0.0.0.0` | no | `TABULA_SKILL_MCP_POOL_HOST` | `TABULA_MCP_POOL_HOST` | Bind address for `python3 skills/mcp/run.py pool` |
| `pool.port` | `int` | `0` | no | `TABULA_SKILL_MCP_POOL_PORT` | `TABULA_MCP_POOL_PORT` | Bind port for pool daemon, `0` means auto-select |

## Runtime Environment

| Variable | Required | Description |
|---|---|---|
| `TABULA_HOME` | yes | Tabula home used for `config/skills/mcp/servers.json` and `run/mcp/pool.url` |

## Precedence

1. env (`TABULA_SKILL_*`, then legacy alias)
2. `~/.tabula/config/global.toml`
3. schema defaults

## Server Definitions

Create `~/.tabula/config/skills/mcp/servers.json`:

```json
{
  "servers": {
    "filesystem": {
      "transport": "stdio",
      "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    },
    "fetch": {
      "transport": "stdio",
      "command": ["uvx", "mcp-server-fetch"]
    },
    "remote-api": {
      "transport": "http",
      "url": "https://api.example.com/mcp"
    }
  }
}
```

## Usage

## Storage Layout

- Server definitions: `~/.tabula/config/skills/mcp/servers.json`
- Pool endpoint file: `~/.tabula/run/mcp/pool.url`

### Discover all tools

```bash
python3 skills/mcp/run.py discover
```

### List tools for a server

```bash
python3 skills/mcp/run.py list filesystem
```

### Call a tool

```bash
python3 skills/mcp/run.py call filesystem read '{"path": "/etc/hosts"}'
```
