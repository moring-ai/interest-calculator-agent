#!/usr/bin/env bash
# Point this repo at whichever AWS account and region you are currently using.
#
#   AWS_PROFILE=moring ./scripts/set-aws-target.sh
#   AWS_PROFILE=moring AWS_REGION=us-east-1 ./scripts/set-aws-target.sh
#
# Rewrites agentcore/aws-targets.json from the live caller identity rather than
# asking you to keep a 12-digit account number correct by hand. That file is the
# one place the agent deploy is pinned to an account, so getting it wrong fails
# late and confusingly -- usually as a permissions error against a runtime that
# lives somewhere else entirely.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"
TARGETS="$REPO_ROOT/agentcore/aws-targets.json"

REGION="${AWS_REGION:-$(aws configure get region 2>/dev/null || echo us-east-1)}"

IDENTITY="$(aws sts get-caller-identity --output json)" || {
    echo "Could not read AWS identity. Set AWS_PROFILE, or run 'aws configure'." >&2
    exit 1
}

ACCOUNT="$(printf '%s' "$IDENTITY" | python3 -c 'import json,sys; print(json.load(sys.stdin)["Account"])')"
ARN="$(printf '%s' "$IDENTITY" | python3 -c 'import json,sys; print(json.load(sys.stdin)["Arn"])')"

# AgentCore is not available everywhere; a wrong region here fails at deploy
# time with a generic endpoint error rather than anything actionable.
case "$REGION" in
    us-east-1|us-east-2|us-west-2|ap-northeast-1|ap-northeast-2|ap-south-1|\
    ap-southeast-1|ap-southeast-2|ca-central-1|eu-central-1|eu-north-1|\
    eu-west-1|eu-west-2|eu-west-3|sa-east-1) ;;
    *) echo "Warning: $REGION is not a known AgentCore region. Continuing." >&2 ;;
esac

python3 - "$TARGETS" "$ACCOUNT" "$REGION" <<'PY'
import json, sys, pathlib
path, account, region = sys.argv[1], sys.argv[2], sys.argv[3]
p = pathlib.Path(path)
targets = json.loads(p.read_text()) if p.exists() else []
if not targets:
    targets = [{"name": "default", "description": "Interest Calculator agent deployment target"}]
targets[0]["account"] = account
targets[0]["region"] = region
p.write_text(json.dumps(targets, indent=2) + "\n")
PY

echo "agentcore/aws-targets.json ->"
echo "  account : $ACCOUNT"
echo "  region  : $REGION"
echo "  identity: $ARN"
echo
echo "Next: CDK must be bootstrapped in this account and region."
echo "  cdk bootstrap aws://$ACCOUNT/$REGION"
