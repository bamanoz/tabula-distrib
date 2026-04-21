---
name: gateway-api
description: "OpenAI-compatible HTTP API gateway"
---

# Gateway API

OpenAI-compatible HTTP API gateway for Tabula. Exposes `POST /v1/chat/completions` and `POST /v1/responses` endpoints
that any OpenAI SDK or compatible client can use to interact with Tabula.

## Run

The launcher controls the HTTP port:

```bash
TABULA_API_PORT=8090 tabula
```

## Config File

Path:

    ~/.tabula/config/global.toml

Example:

```toml
[gateway.api]
# provider_override = "openai"
auth_token = { source = "store", id = "gateway-api.auth_token" }

[gateway.api.session]
idle_ttl = 900
max_age = 21600
cleanup_interval = 30
```

## Secrets

Path:

    ~/.tabula/secrets.json

Example:

```json
{
  "gateway-api.auth_token": "secret-token"
}
```

## Configuration

| Key | Type | Default | Secret | Canonical env | Aliases | Notes |
|---|---|---|---|---|---|---|
| `provider_override` | `string` | `""` | no | `TABULA_SKILL_GATEWAY_API_PROVIDER_OVERRIDE` | -- | Optional per-gateway provider override |
| `auth_token` | `string` | `""` | yes | `TABULA_SKILL_GATEWAY_API_AUTH_TOKEN` | `TABULA_API_AUTH` | Store id: `gateway-api.auth_token` |
| `session.idle_ttl` | `float` | `900` | no | `TABULA_SKILL_GATEWAY_API_SESSION_IDLE_TTL` | `TABULA_API_SESSION_IDLE_TTL` | Idle session eviction threshold in seconds |
| `session.max_age` | `float` | `21600` | no | `TABULA_SKILL_GATEWAY_API_SESSION_MAX_AGE` | `TABULA_API_SESSION_MAX_AGE` | Max session lifetime in seconds |
| `session.cleanup_interval` | `float` | `30` | no | `TABULA_SKILL_GATEWAY_API_SESSION_CLEANUP_INTERVAL` | `TABULA_API_SESSION_CLEANUP_INTERVAL` | Cleanup loop interval in seconds |

## Runtime Environment

| Variable | Required | Description |
|---|---|---|
| `TABULA_URL` | yes | Kernel WebSocket URL |
| `TABULA_HOME` | yes | Tabula home used for driver discovery and state |
| `TABULA_API_PORT` | yes | HTTP port passed by launcher or CLI |

## Precedence

1. env (`TABULA_SKILL_*`, then legacy alias)
2. `~/.tabula/config/global.toml`
3. `~/.tabula/secrets.json` for `auth_token`
4. schema defaults

## Endpoints

### POST /v1/chat/completions

Standard OpenAI Chat Completions format. Supports streaming (SSE) and non-streaming responses.

### POST /v1/responses

OpenAI Responses API format. Supports streaming (named SSE events) and non-streaming responses.
Input can be a string or array of message items.

## Session Management

Sessions are resolved in order:
1. `x-session-id` header
2. Deterministic hash of `user` field
3. Random new session
