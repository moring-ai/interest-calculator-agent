output "mcp_url" {
  description = "The MCP endpoint. Set this as MCP_SERVER_URL for the agent and the backend."
  value       = local.mcp_url
}

output "public_ip" {
  value = aws_eip.this.public_ip
}

output "ecr_repository_url" {
  description = "Push the MCP server image here."
  value       = aws_ecr_repository.mcp.repository_url
}

output "instance_id" {
  description = "For shell access: aws ssm start-session --target <id>"
  value       = aws_instance.this.id
}

output "mcp_token_parameter" {
  description = "SSM parameter holding the bearer token. Read it with: aws ssm get-parameter --name <name> --with-decryption"
  value       = aws_ssm_parameter.mcp_token.name
}

output "mcp_token" {
  description = "The bearer token itself."
  value       = random_password.mcp_token.result
  sensitive   = true
}
