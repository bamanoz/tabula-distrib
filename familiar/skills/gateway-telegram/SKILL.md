---
name: gateway-telegram
description: Telegram Bot gateway. Bridges Telegram chats to Tabula sessions. Each chat_id gets its own session + driver. Access control via pairing tokens. Before running: verify `TELEGRAM_BOT_TOKENS` exists without printing secret values; if missing ask user to add their bot token. Run: `python3 skills/gateway-telegram/run.py`. Install as service: `bash skills/gateway-telegram/install-service.sh`
---

# gateway-telegram

Telegram bot gateway for Tabula. Users go through a pairing flow before they can chat.
Supports multiple bot tokens and streaming responses via sendMessageDraft.

## Run

```bash
python3 skills/gateway-telegram/run.py
```

## Config File

Path:

    ~/.tabula/config/global.toml

Example:

```toml
[gateway.telegram]
# provider_override = "openai"
api_timeout = 30
bot_tokens = { source = "store", id = "gateway-telegram.bot_tokens" }

[gateway.telegram.session]
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
  "gateway-telegram.bot_tokens": ["123:ABC", "456:DEF"]
}
```

## Configuration

| Key | Type | Default | Secret | Canonical env | Aliases | Notes |
|---|---|---|---|---|---|---|
| `provider_override` | `string` | `""` | no | `TABULA_SKILL_GATEWAY_TELEGRAM_PROVIDER_OVERRIDE` | -- | Optional per-gateway provider override |
| `api_timeout` | `float` | `10` | no | `TABULA_SKILL_GATEWAY_TELEGRAM_API_TIMEOUT` | `TABULA_TELEGRAM_API_TIMEOUT` | Telegram API request timeout in seconds |
| `session.idle_ttl` | `float` | `900` | no | `TABULA_SKILL_GATEWAY_TELEGRAM_SESSION_IDLE_TTL` | `TABULA_TELEGRAM_SESSION_IDLE_TTL` | Idle session eviction threshold in seconds |
| `session.max_age` | `float` | `21600` | no | `TABULA_SKILL_GATEWAY_TELEGRAM_SESSION_MAX_AGE` | `TABULA_TELEGRAM_SESSION_MAX_AGE` | Max session lifetime in seconds |
| `session.cleanup_interval` | `float` | `30` | no | `TABULA_SKILL_GATEWAY_TELEGRAM_SESSION_CLEANUP_INTERVAL` | `TABULA_TELEGRAM_SESSION_CLEANUP_INTERVAL` | Cleanup loop interval in seconds |
| `bot_tokens` | `string_list` | -- | yes | `TABULA_SKILL_GATEWAY_TELEGRAM_BOT_TOKENS` | `TELEGRAM_BOT_TOKENS` | Store id: `gateway-telegram.bot_tokens` |

## Runtime Environment

| Variable | Required | Description |
|---|---|---|
| `TABULA_URL` | yes | Kernel WebSocket URL |
| `TABULA_HOME` | yes | Tabula home directory used for drivers, auth, and pid file |

## Precedence

1. env (`TABULA_SKILL_*`, then legacy alias)
2. `~/.tabula/config/global.toml`
3. `~/.tabula/secrets.json` for `bot_tokens`
4. schema defaults

## Setup

1. Create a bot via `@BotFather`
2. Configure tokens via env or `global.toml` + secret store
3. Install as a service if you want it to run persistently

## Service Setup

### macOS (launchd)

```bash
bash skills/gateway-telegram/install-service.sh
```

### Linux (systemd)

```bash
bash skills/gateway-telegram/install-service.sh
```

### Windows (Task Scheduler)

```powershell
powershell -ExecutionPolicy Bypass -File skills/gateway-telegram/install-service.ps1
```

## Pairing flow

1. User writes `/start` to the bot
2. Bot generates a token (`PRX-XXXXXX-YYYYYY`) and sends it to the user
3. Admin approves: `python3 skills/pair/run.py telegram approve PRX-XXXXXX-YYYYYY`
4. User can now chat with the bot

Auth state is stored in `{TABULA_HOME}/data/pair/telegram.json`.

## pair.py -- access management

```bash
# List authorized users and pending requests
python3 skills/pair/run.py telegram list

# Approve a pairing request
python3 skills/pair/run.py telegram approve PRX-XXXXXX-YYYYYY

# Revoke access
python3 skills/pair/run.py telegram revoke <chat_id>
```

## Sessions

Each `chat_id` gets a dedicated Tabula session (`tg-<chat_id>`) with its own driver.
Conversation history is preserved across messages within the same session.
Sessions are not persisted across gateway restarts (in-memory).

## Streaming

Responses are streamed to Telegram via `sendMessageDraft`:
- While the LLM generates text, the user sees incremental updates with typing indicators
- Draft messages are not saved to chat history
- Once the full response is ready, it's sent as a final `sendMessage`
- Draft updates are throttled at ~100ms to avoid rate limiting

## Limitations

- Messages > 4096 chars are split automatically
- Sessions reset on gateway restart

## Storage Layout

- PID file: `~/.tabula/run/gateway-telegram/gateway-telegram.pid`
- Pairing state: `~/.tabula/data/pair/telegram.json`
