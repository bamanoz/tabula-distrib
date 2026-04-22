"""Shared driver/subagent runtime helpers.

Installed into each distro as ``skills/_drivers/`` by the drivers bundle and
imported by driver/subagent skills as ``skills._drivers.<module>``. Kept out of
the core ``skills/lib`` contract on purpose — everything here concerns LLM
drivers, provider protocols, prompt assembly and conversation compaction, all
of which are distro-level concerns.
"""
