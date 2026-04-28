# gateway-cli

Interactive CLI gateway for terminal use.

This is a user-owned launcher, not a skill or plugin. It connects to the kernel
WebSocket, reads input from `/dev/tty`, and displays streamed responses on
stdout.

## Usage

```bash
tabula-claw
```

or directly from an installed distro:

```bash
python3 "$TABULA_HOME/clients/gateway-cli/run.py"
```
