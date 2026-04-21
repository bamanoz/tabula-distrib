FROM python:3.13-slim

RUN pip install --no-cache-dir pyyaml python-dateutil

# Non-root user. UID/GID matter on Linux because of bind-mount perms; on
# Docker Desktop / OrbStack (macOS) the userland VFS proxy handles uid mapping
# transparently, so any fixed uid works.
RUN useradd -m -s /bin/bash -u 1000 agent

# /sandbox holds the Python entrypoint + workspace.py (mounted read-only at runtime).
# /workspace is the bind-mounted user workspace (rw).
# /state holds scratchpad / locals / tracking JSON files (rw).
RUN mkdir -p /sandbox /workspace /state \
    && chown agent:agent /workspace /state

USER agent
WORKDIR /workspace

# Image exists only to be exec'd into. The entrypoint (`sleep infinity`) keeps a
# warm container per session; `docker exec` runs the actual Python code.
ENTRYPOINT ["sleep", "infinity"]
