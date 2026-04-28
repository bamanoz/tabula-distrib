# gateway-telegram-plugin

Plugin-owned supervisor for the Telegram gateway daemon.

This component keeps the daemon lifecycle under plugin supervision while leaving
the existing `gateway-telegram/run.py` Telegram gateway implementation unchanged.

Interactive gateways such as `gateway-cli` and `gateway-tui` remain user-owned
launchers because they need terminal/UI ownership.
