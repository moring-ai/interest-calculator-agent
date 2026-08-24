# --------------------------------------------------------------------------
# Container registry for the MCP server image
# --------------------------------------------------------------------------

resource "aws_ecr_repository" "mcp" {
  name                 = var.name
  image_tag_mutability = "MUTABLE"
  force_delete         = true
  image_scanning_configuration { scan_on_push = true }
}

resource "aws_ecr_lifecycle_policy" "mcp" {
  repository = aws_ecr_repository.mcp.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the 10 most recent images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 10 }
      action       = { type = "expire" }
    }]
  })
}

# --------------------------------------------------------------------------
# Secrets
# --------------------------------------------------------------------------

# The shared token the agent and the backend present to reach the MCP endpoint.
# Generated here so it never has to be invented or pasted by hand.
resource "random_password" "mcp_token" {
  length  = 48
  special = false
}

resource "aws_ssm_parameter" "mcp_token" {
  name        = "/${var.name}/mcp-token"
  description = "Bearer token required by the MCP endpoint"
  type        = "SecureString"
  value       = random_password.mcp_token.result
}

resource "aws_ssm_parameter" "fred_api_key" {
  count       = var.fred_api_key == "" ? 0 : 1
  name        = "/${var.name}/fred-api-key"
  description = "FRED API key for live interest rate data"
  type        = "SecureString"
  value       = var.fred_api_key
}

# --------------------------------------------------------------------------
# Instance identity
# --------------------------------------------------------------------------

resource "aws_iam_role" "instance" {
  name = "${var.name}-instance"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# Session Manager replaces SSH for shell access.
resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy" "instance" {
  name = "${var.name}-instance"
  role = aws_iam_role.instance.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      {
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "ecr:BatchGetImage",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchCheckLayerAvailability",
        ]
        Resource = aws_ecr_repository.mcp.arn
      },
      {
        Effect = "Allow"
        Action = ["ssm:GetParameter", "ssm:GetParameters"]
        Resource = compact([
          aws_ssm_parameter.mcp_token.arn,
          try(aws_ssm_parameter.fred_api_key[0].arn, ""),
        ])
      },
      {
        Effect    = "Allow"
        Action    = ["kms:Decrypt"]
        Resource  = "*"
        Condition = { StringEquals = { "kms:ViaService" = "ssm.${var.region}.amazonaws.com" } }
      },
    ])
  })
}

resource "aws_iam_instance_profile" "this" {
  name = "${var.name}-instance"
  role = aws_iam_role.instance.name
}

# --------------------------------------------------------------------------
# The instance
# --------------------------------------------------------------------------

data "aws_ssm_parameter" "al2023_arm64" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

# Allocated before the instance so its address can be baked into the Caddy
# config: the sslip.io hostname is derived from the IP, and Caddy needs to know
# its own hostname at boot to request a certificate for it.
resource "aws_eip" "this" {
  domain = "vpc"
  tags   = { Name = var.name }
}

locals {
  sslip_host = "${replace(aws_eip.this.public_ip, ".", "-")}.sslip.io"
  mcp_host   = var.custom_domain != "" ? var.custom_domain : local.sslip_host
  mcp_url    = "https://${local.mcp_host}/mcp"
}

resource "aws_instance" "this" {
  ami                    = data.aws_ssm_parameter.al2023_arm64.value
  instance_type          = var.instance_type
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.this.id]
  iam_instance_profile   = aws_iam_instance_profile.this.name

  root_block_device {
    volume_size = 20
    volume_type = "gp3"
    encrypted   = true
  }

  metadata_options {
    http_tokens   = "required" # IMDSv2 only
    http_endpoint = "enabled"
  }

  user_data = templatefile("${path.module}/cloud-init.yaml.tftpl", {
    region      = var.region
    ecr_repo    = aws_ecr_repository.mcp.repository_url
    mcp_host    = local.mcp_host
    token_param = aws_ssm_parameter.mcp_token.name
    fred_param  = var.fred_api_key == "" ? "" : aws_ssm_parameter.fred_api_key[0].name
    name        = var.name
  })

  # Replace the instance when the bootstrap changes, so the box never drifts
  # from what the repository says it should be.
  user_data_replace_on_change = true

  tags = { Name = var.name }
}

resource "aws_eip_association" "this" {
  instance_id   = aws_instance.this.id
  allocation_id = aws_eip.this.id
}
