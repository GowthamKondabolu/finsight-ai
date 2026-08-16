resource "aws_security_group" "load_balancer" {
  name        = "${local.name_prefix}-alb"
  description = "Public TLS ingress to the analyst web application"
  vpc_id      = aws_vpc.main.id

  tags = { Name = "${local.name_prefix}-alb" }
}

resource "aws_security_group" "web" {
  name        = "${local.name_prefix}-web"
  description = "Private analyst web tasks"
  vpc_id      = aws_vpc.main.id

  tags = { Name = "${local.name_prefix}-web" }
}

resource "aws_security_group" "api" {
  name        = "${local.name_prefix}-api"
  description = "Private FinSight API tasks"
  vpc_id      = aws_vpc.main.id

  tags = { Name = "${local.name_prefix}-api" }
}

resource "aws_security_group" "database" {
  name        = "${local.name_prefix}-database"
  description = "Isolated PostgreSQL access from API and migration tasks"
  vpc_id      = aws_vpc.main.id

  tags = { Name = "${local.name_prefix}-database" }
}

resource "aws_vpc_security_group_ingress_rule" "alb_http" {
  security_group_id = aws_security_group.load_balancer.id
  description       = "HTTP redirect"
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  security_group_id = aws_security_group.load_balancer.id
  description       = "HTTPS"
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_egress_rule" "alb_to_web" {
  security_group_id            = aws_security_group.load_balancer.id
  description                  = "Forward only to web tasks"
  from_port                    = 3000
  to_port                      = 3000
  ip_protocol                  = "tcp"
  referenced_security_group_id = aws_security_group.web.id
}

resource "aws_vpc_security_group_ingress_rule" "web_from_alb" {
  security_group_id            = aws_security_group.web.id
  description                  = "Next.js from the public ALB"
  from_port                    = 3000
  to_port                      = 3000
  ip_protocol                  = "tcp"
  referenced_security_group_id = aws_security_group.load_balancer.id
}

resource "aws_vpc_security_group_egress_rule" "web_to_api" {
  security_group_id            = aws_security_group.web.id
  description                  = "Allowlisted server-side API proxy"
  from_port                    = 8000
  to_port                      = 8000
  ip_protocol                  = "tcp"
  referenced_security_group_id = aws_security_group.api.id
}

resource "aws_vpc_security_group_egress_rule" "web_https" {
  security_group_id = aws_security_group.web.id
  description       = "ECR, Secrets Manager, and CloudWatch Logs"
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_egress_rule" "web_dns_udp" {
  security_group_id = aws_security_group.web.id
  description       = "VPC DNS over UDP"
  from_port         = 53
  to_port           = 53
  ip_protocol       = "udp"
  cidr_ipv4         = var.vpc_cidr
}

resource "aws_vpc_security_group_egress_rule" "web_dns_tcp" {
  security_group_id = aws_security_group.web.id
  description       = "VPC DNS over TCP"
  from_port         = 53
  to_port           = 53
  ip_protocol       = "tcp"
  cidr_ipv4         = var.vpc_cidr
}

resource "aws_vpc_security_group_ingress_rule" "api_from_web" {
  security_group_id            = aws_security_group.api.id
  description                  = "API requests from web tasks"
  from_port                    = 8000
  to_port                      = 8000
  ip_protocol                  = "tcp"
  referenced_security_group_id = aws_security_group.web.id
}

resource "aws_vpc_security_group_egress_rule" "api_https" {
  security_group_id = aws_security_group.api.id
  description       = "HTTPS for SEC, model provider, AWS APIs, and OTLP"
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_egress_rule" "api_dns_udp" {
  security_group_id = aws_security_group.api.id
  description       = "VPC DNS over UDP"
  from_port         = 53
  to_port           = 53
  ip_protocol       = "udp"
  cidr_ipv4         = var.vpc_cidr
}

resource "aws_vpc_security_group_egress_rule" "api_dns_tcp" {
  security_group_id = aws_security_group.api.id
  description       = "VPC DNS over TCP"
  from_port         = 53
  to_port           = 53
  ip_protocol       = "tcp"
  cidr_ipv4         = var.vpc_cidr
}

resource "aws_vpc_security_group_egress_rule" "api_to_database" {
  security_group_id            = aws_security_group.api.id
  description                  = "PostgreSQL"
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
  referenced_security_group_id = aws_security_group.database.id
}

resource "aws_vpc_security_group_ingress_rule" "database_from_api" {
  security_group_id            = aws_security_group.database.id
  description                  = "PostgreSQL from API and migration tasks"
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
  referenced_security_group_id = aws_security_group.api.id
}
