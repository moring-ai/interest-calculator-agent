"""MCP client pointed at the ToolHive-hosted finance tools.

The tools used to live behind an AgentCore Gateway, which authenticated with
SigV4 because it was an AWS resource. They now run under ToolHive on an EC2
host fronted by Caddy, so authentication is a bearer token that Caddy checks
before it will proxy anything through.

Both the URL and the token arrive by environment variable. When the URL is
absent this returns None and `main.py` runs the agent with no tools, which
keeps the container startable before the tool host exists.
"""

from __future__ import annotations

import logging
import os

from mcp.client.streamable_http import streamablehttp_client
from strands.tools.mcp.mcp_client import MCPClient

logger = logging.getLogger(__name__)

MCP_URL_VAR = "MCP_SERVER_URL"
MCP_TOKEN_VAR = "MCP_SERVER_TOKEN"
#: Name of an SSM parameter holding the token. Preferred over MCP_SERVER_TOKEN:
#: `agentcore.json` is committed to git, so putting the secret in an env var
#: there would publish it. The parameter NAME is not sensitive, and the runtime
#: reads the value at startup with its own execution role.
MCP_TOKEN_SSM_VAR = "MCP_TOKEN_SSM_PARAM"

#: Tool calls do real work -- a rate fetch plus an amortization schedule -- so
#: the default 5s read timeout is too tight, especially on a cold FRED cache.
DEFAULT_TIMEOUT_SECONDS = 60


def _resolve_token() -> str:
    """Get the bearer token, preferring SSM over a literal env var."""
    literal = os.environ.get(MCP_TOKEN_VAR, "").strip()
    if literal:
        return literal

    parameter = os.environ.get(MCP_TOKEN_SSM_VAR, "").strip()
    if not parameter:
        return ""

    try:
        import boto3

        region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
        client = boto3.client("ssm", region_name=region)
        value = client.get_parameter(Name=parameter, WithDecryption=True)
        logger.info("resolved MCP token from SSM parameter %s", parameter)
        return value["Parameter"]["Value"]
    except Exception as exc:                          # noqa: BLE001
        # Do not raise: a missing token should degrade the agent to "no tools"
        # with a loud log, not crash the container into a restart loop.
        logger.error("could not read MCP token from SSM %s: %s", parameter, exc)
        return ""


def get_streamable_http_mcp_client() -> MCPClient | None:
    """Return an MCP client for the finance tools, or None if unconfigured."""
    endpoint = os.environ.get(MCP_URL_VAR, "").strip()
    if not endpoint:
        logger.info("%s is not set; running without tools.", MCP_URL_VAR)
        return None

    token = _resolve_token()
    if not token:
        # Worth shouting about: the endpoint is public, so a missing token means
        # every tool call comes back 401 and the agent silently loses its tools.
        logger.warning(
            "%s is set but no token could be resolved from %s or %s; "
            "the tool host will reject these calls.",
            MCP_URL_VAR, MCP_TOKEN_VAR, MCP_TOKEN_SSM_VAR,
        )

    headers = {"Authorization": f"Bearer {token}"} if token else None
    timeout = float(os.environ.get("MCP_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS))
    logger.info("connecting to MCP server at %s (auth=%s)", endpoint, bool(token))

    return MCPClient(lambda: streamablehttp_client(
        endpoint, headers=headers, timeout=timeout, sse_read_timeout=timeout,
    ))
