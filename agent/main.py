import json
from typing import Any
from collections import OrderedDict
from strands import Agent
import asyncio
from strands.agent.conversation_manager.null_conversation_manager import NullConversationManager
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from model.load import load_model
from mcp_client.client import get_streamable_http_mcp_client

app = BedrockAgentCoreApp()
log = app.logger

# Define a Streamable HTTP MCP Client
mcp_clients = [get_streamable_http_mcp_client()]

DEFAULT_SYSTEM_PROMPT = """
You are the Interest Calculator, an interest-rate analyst. You help people reason about
mortgages, savings and CD yields, and investment returns using current market
rates.

## The one rule that matters

NEVER do arithmetic yourself. Every number you state must come from a tool
call. Monthly payments, total interest, compound growth, tax owed -- all of it
comes from tools. If you catch yourself about to compute something, call a tool
instead. If no tool covers the question, say so plainly.

## How to work

- Start from what the user asked, not from a checklist. One good chart beats
  four mediocre ones.
- When a rate matters and the user did not give one, let the tool fetch it
  live rather than guessing, and tell them which rate you used and its date.
- Prefer `compare_mortgage_options` over several separate calls when the user
  is weighing choices; the comparison chart is the point.
- Tools return `assumptions`. Surface any that were not user-supplied, because
  the user needs to know what was filled in for them.
- Tools return `notes`. Repeat the ones that qualify the answer, especially any
  warning that a rate is SYNTHETIC placeholder data rather than a real rate.
- Charts render automatically from tool results. Refer to them in words -- do
  not re-list every number that is already plotted.

## Boundaries

You are not a licensed financial advisor. Explain trade-offs, show the numbers,
and lay out what each option costs. Do not tell anyone what they should do with
their money. Tax figures are estimates of federal tax only and are not tax
advice.
"""


# Define a collection of tools used by the model
tools = []

_INLINE_FUNCTION_NAMES = set()

# No inline tools: every capability comes from the MCP gateway below, so the
# deterministic finance code has exactly one deployment and one test surface.



# Add MCP client to tools if available
for mcp_client in mcp_clients:
    if mcp_client:
        tools.append(mcp_client)


def _make_conversation_manager():
    return NullConversationManager()

# Reuses one Agent per session_id so each session keeps its own in-process
# conversation history (best-effort; resets on cold start). The cache is bounded
# to 128 sessions with LRU eviction (least-recently-used is dropped and its
# history reset) so a single process serving many sessions cannot leak history
# between them or grow without limit. For durable history, attach a session manager.
def agent_factory():
    cache = OrderedDict()
    def get_or_create_agent(session_id):
        if session_id in cache:
            cache.move_to_end(session_id)
            return cache[session_id]
        if len(cache) >= 128:
            cache.popitem(last=False)
        cache[session_id] = Agent(
            model=load_model(),
            system_prompt=DEFAULT_SYSTEM_PROMPT,
            tools=tools,
            conversation_manager=_make_conversation_manager(),
            hooks=[
            ],
        )
        return cache[session_id]
    return get_or_create_agent
get_or_create_agent = agent_factory()


def strip_trailing_tool_use(messages: Any) -> list[dict]:
    """Strip toolUse blocks from the tail until the last message has none."""
    if not isinstance(messages, list):
        raise ValueError("messages must be a list")

    messages = list(messages)
    while messages:
        last = messages[-1]
        if not isinstance(last, dict):
            raise ValueError("each message must be an object")
        original_content = last.get("content", [])
        if not isinstance(original_content, list) or not all(isinstance(block, dict) for block in original_content):
            raise ValueError("each message content value must be a list of content blocks")

        content = [block for block in original_content if "toolUse" not in block]
        if len(content) == len(original_content):
            break
        if content:
            messages[-1] = {**last, "content": content}
            break
        messages.pop()

    return messages


def _extract_prompt(payload: dict):
    """Accept validated harness messages, tool results, or a plain prompt string."""
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")
    if "messages" in payload:
        return strip_trailing_tool_use(payload["messages"])
    if "tool_results" in payload:
        tool_results = payload["tool_results"]
        if not isinstance(tool_results, list) or not all(
            isinstance(tool_result, dict) and isinstance(tool_result.get("toolUseId"), str)
            for tool_result in tool_results
        ):
            raise ValueError("tool_results must contain objects with a toolUseId string")
        return [{"role": "user", "content": [{"toolResult": {
            "toolUseId": tr["toolUseId"],
            "status": tr.get("status", "success"),
            "content": tr.get("content", []),
        }} for tr in tool_results]}]
    prompt = payload.get("prompt", "")
    if not isinstance(prompt, str):
        raise ValueError("prompt must be a string")
    return prompt


def _has_inline_function_call(messages) -> bool:
    """Return True if messages contains an assistant toolUse for an inline function tool."""
    if not _INLINE_FUNCTION_NAMES or not isinstance(messages, list):
        return False
    for msg in messages:
        if msg.get("role") == "assistant":
            for block in msg.get("content", []):
                if isinstance(block, dict) and block.get("toolUse", {}).get("name") in _INLINE_FUNCTION_NAMES:
                    return True
    return False


def _is_inline_function_call(event: dict) -> bool:
    """Check if a contentBlockStart event is for an inline function tool."""
    if not _INLINE_FUNCTION_NAMES:
        return False
    cbs = event.get("contentBlockStart", {})
    start = cbs.get("start", {})
    tool_use = start.get("toolUse") if isinstance(start, dict) else None
    return tool_use is not None and tool_use.get("name") in _INLINE_FUNCTION_NAMES



#: Chart payloads are the largest thing crossing this boundary. A tool result
#: is forwarded whole, but oversized `detail` blocks are dropped: the frontend
#: renders from `summary` and `charts`, and the full amortization schedule can
#: run to hundreds of rows that nothing downstream reads.
MAX_DETAIL_BYTES = 16_000

#: Fence used to smuggle structured tool output through a text-only channel.
#: LiteLLM's AgentCore adapter reads ONLY `event.contentBlockDelta.delta.text`
#: from this stream and drops every other event shape, so a tool result that is
#: yielded as its own event type never reaches the interface. Emitting it as
#: text inside a fenced block is what keeps charts alive across that hop.
PAYLOAD_FENCE = "ic-payload"


def _parse_tool_content(block: Any) -> dict | None:
    """Pull a ToolResult envelope out of one MCP content block."""
    if not isinstance(block, dict):
        return None
    parsed = block.get("json")
    if parsed is None and isinstance(block.get("text"), str):
        try:
            parsed = json.loads(block["text"])
        except (json.JSONDecodeError, ValueError):
            return None
    return parsed if isinstance(parsed, dict) else None


def _payload_block(tool_name: str, tool_result: Any) -> str | None:
    """Render a tool result as a fenced JSON block, or None if there is nothing
    worth sending.

    Built by code rather than asked of the model on purpose: a model cannot be
    relied on to reproduce a few kilobytes of JSON verbatim, and a single
    mangled character would cost the whole chart.
    """
    if not isinstance(tool_result, dict):
        return None

    for block in tool_result.get("content") or []:
        parsed = _parse_tool_content(block)
        if parsed is None:
            continue
        if parsed.get("isError"):
            continue
        if not (parsed.get("charts") or parsed.get("summary")):
            continue

        # `detail` is the full schedule; nothing downstream renders it, and it
        # can be an order of magnitude larger than everything else combined.
        if len(json.dumps(parsed.get("detail", {}), default=str)) > MAX_DETAIL_BYTES:
            parsed = {**parsed, "detail": {}}

        envelope = {"tool": tool_name, **parsed}
        body = json.dumps(envelope, separators=(",", ":"), default=str)
        return f"\n\n```{PAYLOAD_FENCE}\n{body}\n```\n\n"

    return None


def _text_event(text: str) -> dict:
    """Wrap text so it survives LiteLLM's AgentCore transformation."""
    return {"event": {"contentBlockDelta": {"delta": {"text": text}}}}


@app.entrypoint
async def invoke(payload, context):
    log.info("Invoking Agent.....")


    session_id = getattr(context, 'session_id', 'default-session')
    agent = get_or_create_agent(session_id)

    prompt = _extract_prompt(payload)

    # Scoped to this turn so ids cannot leak between concurrent invocations.
    tool_names: dict[str, str] = {}


    async for event in agent.stream_async(
        prompt,
    ):
        if not isinstance(event, dict):
            continue

        # Raw model-stream events: text deltas and tool-use starts.
        if "event" in event:
            cbs = event["event"].get("contentBlockStart")
            if cbs is not None and not cbs.get("start"):
                continue
            # Remember which tool each toolUseId belongs to; the result arrives
            # later in a separate message that carries only the id.
            start = (cbs or {}).get("start", {})
            tool_use = start.get("toolUse") if isinstance(start, dict) else None
            if tool_use:
                raw_name = tool_use.get("name", "tool")
                tool_names[tool_use.get("toolUseId", "")] = raw_name
            yield event
            continue

        # Tool RESULTS arrive as completed messages, outside the "event"
        # wrapper. They carry the chart specs and the computed numbers, so
        # dropping them here would leave the UI with prose and no charts --
        # and the model narrating charts that were never sent.
        message = event.get("message")
        if isinstance(message, dict):
            for block in message.get("content") or []:
                if not (isinstance(block, dict) and "toolResult" in block):
                    continue
                tool_result = block["toolResult"]
                name = tool_names.pop(tool_result.get("toolUseId", ""), "tool")
                payload = _payload_block(name, tool_result)
                if payload:
                    yield _text_event(payload)


if __name__ == "__main__":
    app.run()
