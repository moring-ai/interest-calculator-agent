# AgentCore Runtime container.
#
#     docker build --platform linux/arm64 -f agent/Dockerfile -t interest-agent agent/
#
# AgentCore Runtime requires linux/arm64 (Graviton) and an HTTP server on
# 0.0.0.0:8080 exposing POST /invocations and GET /ping. `BedrockAgentCoreApp`
# from the bedrock-agentcore SDK implements that contract, so main.py only has
# to provide the entrypoint generator.
FROM python:3.12-slim AS base

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_SYSTEM_PYTHON=1 \
    UV_COMPILE_BYTECODE=1

WORKDIR /app

# Dependencies first so source edits do not invalidate the install layer.
COPY pyproject.toml ./
RUN uv pip install --no-cache \
        "aws-opentelemetry-distro" \
        "bedrock-agentcore>=1.9.1" \
        "boto3>=1.35.0" "botocore[crt]>=1.35.0" \
        "mcp~=1.24.0" \
        "strands-agents>=1.15.0"

COPY . .

RUN useradd --create-home --uid 10001 agent && chown -R agent:agent /app
USER agent

ENV AGENT_PORT=8080
EXPOSE 8080

# The runtime polls /ping; failing this marks the container unhealthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/ping',timeout=3).status==200 else 1)" || exit 1

CMD ["python", "main.py"]
