"""Model selection for the Interest Calculator agent.

The model id is read from the environment so the deployed runtime can be
repointed without a code change. Cross-region inference profiles ("global." /
"us." prefixes) are used because AgentCore Runtime runs in us-east-2, where the
on-demand model ids are not directly invokable.
"""

import os

from strands.models.bedrock import BedrockModel

# Sonnet 4.5 is the default because it is what this AWS account currently has
# Bedrock model access for. Claude Sonnet 5 is a drop-in upgrade once it is
# enabled in the Bedrock console -- set AGENT_MODEL_ID to
# "global.anthropic.claude-sonnet-5" and redeploy, no code change needed.
DEFAULT_MODEL_ID = "global.anthropic.claude-sonnet-4-5-20250929-v1:0"


def load_model() -> BedrockModel:
    """Get a Bedrock model client using the runtime's IAM execution role."""
    return BedrockModel(
        model_id=os.environ.get("AGENT_MODEL_ID", DEFAULT_MODEL_ID),
        temperature=float(os.environ.get("AGENT_TEMPERATURE", "0.2")),
    )
