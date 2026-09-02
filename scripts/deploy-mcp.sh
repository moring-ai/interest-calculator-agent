#!/usr/bin/env bash
# Build the MCP server image, push it to ECR, and restart it on the ToolHive host.
#
#   ./scripts/deploy-mcp.sh
#
# Reads the ECR repository and instance id from Terraform outputs, so there is
# nothing to keep in sync by hand.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"
TF_DIR="$REPO_ROOT/infra/toolhive"
REGION="${AWS_REGION:-us-east-1}"

cd "$TF_DIR"
ECR_REPO="$(terraform output -raw ecr_repository_url)"
INSTANCE_ID="$(terraform output -raw instance_id)"
REGISTRY="${ECR_REPO%%/*}"
cd "$REPO_ROOT"

echo "==> Building linux/arm64 image"
docker build --platform linux/arm64 -f mcp_server/Dockerfile -t "${ECR_REPO}:latest" .

echo "==> Pushing to $ECR_REPO"
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "$REGISTRY"
docker push "${ECR_REPO}:latest"

echo "==> Restarting the server on the ToolHive host"
CMD_ID="$(aws ssm send-command \
  --region "$REGION" \
  --instance-ids "$INSTANCE_ID" \
  --document-name AWS-RunShellScript \
  --comment "Redeploy MCP server" \
  --parameters 'commands=["systemctl restart interest-mcp.service"]' \
  --query 'Command.CommandId' --output text)"

aws ssm wait command-executed --region "$REGION" \
  --command-id "$CMD_ID" --instance-id "$INSTANCE_ID" 2>/dev/null || true

aws ssm get-command-invocation --region "$REGION" \
  --command-id "$CMD_ID" --instance-id "$INSTANCE_ID" \
  --query '{Status:Status,Output:StandardOutputContent,Error:StandardErrorContent}' \
  --output json

cd "$TF_DIR"
echo
echo "MCP endpoint: $(terraform output -raw mcp_url)"
