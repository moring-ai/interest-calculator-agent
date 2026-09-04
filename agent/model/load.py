"""Model selection for the Interest Calculator agent.

Two ways to reach a model, chosen by configuration rather than by a code change:

1. **Through an LLM gateway** (LiteLLM, or anything OpenAI-compatible). Used
   whenever a gateway base URL is set. Required on platforms that centralise
   model access -- the AICP agent runtime holds no Bedrock permissions of its
   own, so a direct Bedrock call from inside the runtime is refused. Model
   access lives on the gateway host and every call routes through it.

2. **Directly to Bedrock** with the runtime's IAM execution role. The fallback
   when no gateway is configured, and what runs when the agent is deployed with
   the AgentCore CLI into an account whose execution role can call Bedrock.

Both return a Strands model, so `main.py` is unaffected by which one is used.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Sonnet 4.5 is a deliberate default rather than the newest available: this
# agent picks a tool and explains what came back, and every number is computed
# in finance_core. That is not reasoning-heavy work, so a larger model buys
# little here. Override with AGENT_MODEL_ID.
DEFAULT_MODEL_ID = "global.anthropic.claude-sonnet-4-5-20250929-v1:0"

#: Gateway base URL; the first of these that is set wins. Several names are
#: accepted because the variable a hosting platform injects is its own
#: convention, and guessing wrong fails as a silent fallback to direct Bedrock
#: -- which then fails again later, and far less clearly, as an AccessDenied.
GATEWAY_URL_VARS = (
    "LLM_GATEWAY_BASE_URL",
    "LITELLM_BASE_URL",
    "OPENAI_BASE_URL",
    "OPENAI_API_BASE",
)

GATEWAY_KEY_VARS = (
    "LLM_GATEWAY_API_KEY",
    "LITELLM_API_KEY",
    "LITELLM_MASTER_KEY",
    "OPENAI_API_KEY",
)

#: Which model to ask the gateway for. AICP injects `AGENT_MODEL`; the longer
#: `AGENT_MODEL_ID` is what this repo used before and is kept so a CLI deploy
#: kept working. Reading only one of them means the other is silently ignored
#: and the request goes out with a model name the gateway has never heard of.
MODEL_ID_VARS = ("AGENT_MODEL_ID", "AGENT_MODEL")


def _first_env(names: tuple[str, ...]) -> tuple[str | None, str | None]:
    """Return the first (name, value) pair that is set and non-empty."""
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return name, value
    return None, None


#: Resolved once per container; the catalogue does not change under us and
#: load_model() runs per session.
_resolved_model: str | None = None


def _resolve_model_against_gateway(base_url: str, api_key: str, wanted: str) -> str:
    """Reconcile the configured model name with what the gateway actually serves.

    A hosting platform may inject a display name ("claude-sonnet-4.5") while its
    gateway routes on an alias ("claude-sonnet"). The mismatch is only visible
    as a 400 on the first message, long after deploy, so this asks the gateway
    up front and adapts.

    Returns `wanted` unchanged if it is valid, or if the catalogue cannot be
    read -- never fail startup over a name that might well be correct.
    """
    global _resolved_model
    if _resolved_model is not None:
        return _resolved_model

    _resolved_model = wanted
    try:
        import httpx

        response = httpx.get(
            base_url.rstrip("/") + "/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10.0,
        )
        response.raise_for_status()
        available = [m["id"] for m in response.json().get("data", []) if m.get("id")]
    except Exception as exc:                          # noqa: BLE001
        logger.warning("Could not read the gateway's model catalogue (%s); "
                       "using %r as configured.", exc, wanted)
        return _resolved_model

    if not available:
        logger.warning("Gateway returned an empty model catalogue; using %r.", wanted)
        return _resolved_model

    if wanted in available:
        return _resolved_model

    # Prefer a name that is a prefix of the requested one -- "claude-sonnet"
    # for "claude-sonnet-4.5" -- then fall back to a sole offering.
    prefix = [m for m in available if wanted.startswith(m) or m.startswith(wanted)]
    chosen = prefix[0] if prefix else (available[0] if len(available) == 1 else None)

    if chosen:
        logger.warning(
            "Gateway does not serve %r. Available: %s. Using %r instead.",
            wanted, ", ".join(available), chosen,
        )
        _resolved_model = chosen
    else:
        logger.error(
            "Gateway does not serve %r and no close match was found. "
            "Available: %s. Set AGENT_MODEL to one of these.",
            wanted, ", ".join(available),
        )
    return _resolved_model


def load_model():
    """Build the model client, preferring a gateway when one is configured."""
    model_var, model_id = _first_env(MODEL_ID_VARS)
    if not model_id:
        model_var, model_id = "default", DEFAULT_MODEL_ID
    temperature = float(os.environ.get("AGENT_TEMPERATURE", "0.2"))

    url_var, base_url = _first_env(GATEWAY_URL_VARS)

    if base_url:
        from strands.models.openai import OpenAIModel

        _, api_key = _first_env(GATEWAY_KEY_VARS)
        if not api_key:
            # A gateway that authenticates will reject every call. Say so once
            # at startup rather than letting it surface as a 401 mid-answer.
            logger.warning(
                "Gateway URL found in %s but no key in any of %s. Calls will "
                "fail if the gateway requires authentication.",
                url_var, ", ".join(GATEWAY_KEY_VARS),
            )

        if api_key:
            model_id = _resolve_model_against_gateway(base_url, api_key, model_id)

        logger.info(
            "Routing model calls through the gateway at %s (from %s, auth=%s), "
            "model=%s (from %s)",
            base_url, url_var, bool(api_key), model_id, model_var,
        )

        return OpenAIModel(
            client_args={
                "base_url": base_url,
                # The OpenAI client requires a key even when the gateway does
                # not; a placeholder keeps it from raising at construction.
                "api_key": api_key or "not-required",
            },
            model_id=model_id,
            params={"temperature": temperature},
        )

    from strands.models.bedrock import BedrockModel

    logger.info(
        "No gateway configured (checked %s); calling Bedrock directly with the "
        "runtime's execution role, model=%s",
        ", ".join(GATEWAY_URL_VARS), model_id,
    )
    return BedrockModel(model_id=model_id, temperature=temperature)
