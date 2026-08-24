# MCP tool server image.
#
# Build context is the REPOSITORY ROOT, not this directory, because the server
# depends on the local packages/ tree:
#
#     docker build -f mcp_server/Dockerfile -t interest-mcp-server .
#
# Targets linux/arm64 to match the Graviton EC2 instance that ToolHive runs on.
FROM python:3.12-slim AS base

# uv resolves and installs an order of magnitude faster than pip, and the
# lockfile-free install here is deterministic enough because every first-party
# dependency is a local path.
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_SYSTEM_PYTHON=1 \
    UV_COMPILE_BYTECODE=1

WORKDIR /app

# Dependency metadata first so the layer cache survives source-only changes.
COPY packages/finance_core/pyproject.toml   packages/finance_core/
COPY packages/rate_feed/pyproject.toml      packages/rate_feed/
COPY packages/interest_tools/pyproject.toml packages/interest_tools/
COPY mcp_server/pyproject.toml              mcp_server/

COPY packages/ packages/
COPY mcp_server/ mcp_server/

# Installed non-editable: editable installs write .pth files pointing at build
# paths, which is exactly the failure that broke the old tool Lambda.
RUN uv pip install --no-cache \
        ./packages/finance_core \
        ./packages/rate_feed \
        ./packages/interest_tools \
        "mcp~=1.24.0" "pydantic>=2.7" "httpx>=0.27"

# Drop privileges: this process is reachable from the public internet.
RUN useradd --create-home --uid 10001 mcp && chown -R mcp:mcp /app
USER mcp

ENV MCP_BIND_HOST=0.0.0.0 \
    MCP_BIND_PORT=8000 \
    ALLOW_SYNTHETIC_RATES=true

EXPOSE 8000

# A bare TCP check: /mcp requires MCP session headers, so an HTTP probe against
# it would report unhealthy on a perfectly healthy server.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import socket,os,sys; s=socket.create_connection(('127.0.0.1',int(os.environ.get('MCP_BIND_PORT', os.environ.get('MCP_PORT','8000')))),3); s.close()" || exit 1

CMD ["python", "mcp_server/server.py"]
