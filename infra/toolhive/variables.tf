variable "region" {
  description = "AWS region. Must match the region the AgentCore runtime lives in."
  type        = string
  default     = "us-east-1"
}

variable "name" {
  description = "Name prefix for every resource."
  type        = string
  default     = "interest-mcp"
}

variable "instance_type" {
  description = "Graviton instance. t4g.small is the smallest size with enough memory for Docker plus ToolHive plus the server."
  type        = string
  default     = "t4g.small"
}

variable "vpc_cidr" {
  type    = string
  default = "10.20.0.0/16"
}

variable "fred_api_key" {
  description = "FRED API key for live rates. Stored in SSM as a SecureString, never in state output."
  type        = string
  sensitive   = true
  default     = ""
}

variable "custom_domain" {
  description = <<-EOT
    Optional DNS name for the MCP endpoint, e.g. mcp.moring.ai. You must point
    it at the Elastic IP yourself. Leave empty to use an sslip.io hostname
    derived from the Elastic IP, which still gets a real Let's Encrypt
    certificate and requires no DNS setup.
  EOT
  type        = string
  default     = ""
}
