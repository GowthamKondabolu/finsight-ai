resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = local.name_prefix }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = local.name_prefix }
}

resource "aws_subnet" "public" {
  for_each = { for index, az in local.availability_zones : az => index }

  vpc_id                  = aws_vpc.main.id
  availability_zone       = each.key
  cidr_block              = cidrsubnet(var.vpc_cidr, 4, each.value)
  map_public_ip_on_launch = false

  tags = {
    Name = "${local.name_prefix}-public-${each.value + 1}"
    Tier = "public"
  }
}

resource "aws_subnet" "application" {
  for_each = { for index, az in local.availability_zones : az => index }

  vpc_id            = aws_vpc.main.id
  availability_zone = each.key
  cidr_block        = cidrsubnet(var.vpc_cidr, 4, each.value + 4)

  tags = {
    Name = "${local.name_prefix}-application-${each.value + 1}"
    Tier = "application"
  }
}

resource "aws_subnet" "database" {
  for_each = { for index, az in local.availability_zones : az => index }

  vpc_id            = aws_vpc.main.id
  availability_zone = each.key
  cidr_block        = cidrsubnet(var.vpc_cidr, 4, each.value + 8)

  tags = {
    Name = "${local.name_prefix}-database-${each.value + 1}"
    Tier = "database"
  }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = { Name = "${local.name_prefix}-public" }
}

resource "aws_route_table_association" "public" {
  for_each = aws_subnet.public

  subnet_id      = each.value.id
  route_table_id = aws_route_table.public.id
}

resource "aws_eip" "nat" {
  count = var.nat_gateway_count

  domain = "vpc"

  depends_on = [aws_internet_gateway.main]
  tags       = { Name = "${local.name_prefix}-nat-${count.index + 1}" }
}

resource "aws_nat_gateway" "main" {
  count = var.nat_gateway_count

  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = values(aws_subnet.public)[count.index].id

  depends_on = [aws_internet_gateway.main]
  tags       = { Name = "${local.name_prefix}-${count.index + 1}" }
}

resource "aws_route_table" "application" {
  for_each = { for index, az in local.availability_zones : az => index }

  vpc_id = aws_vpc.main.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main[min(each.value, var.nat_gateway_count - 1)].id
  }

  tags = { Name = "${local.name_prefix}-application-${each.value + 1}" }
}

resource "aws_route_table_association" "application" {
  for_each = aws_subnet.application

  subnet_id      = each.value.id
  route_table_id = aws_route_table.application[each.key].id
}

resource "aws_route_table" "database" {
  for_each = aws_subnet.database

  vpc_id = aws_vpc.main.id
  tags   = { Name = "${local.name_prefix}-database-${index(local.availability_zones, each.key) + 1}" }
}

resource "aws_route_table_association" "database" {
  for_each = aws_subnet.database

  subnet_id      = each.value.id
  route_table_id = aws_route_table.database[each.key].id
}

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [for route_table in aws_route_table.application : route_table.id]

  tags = { Name = "${local.name_prefix}-s3" }
}

resource "aws_service_discovery_private_dns_namespace" "main" {
  name        = "${local.name_prefix}.internal"
  description = "Private service discovery for FinSight ECS tasks"
  vpc         = aws_vpc.main.id
}

resource "aws_service_discovery_service" "api" {
  name = "api"

  dns_config {
    namespace_id = aws_service_discovery_private_dns_namespace.main.id

    dns_records {
      ttl  = 10
      type = "A"
    }

    routing_policy = "MULTIVALUE"
  }

  health_check_custom_config {
    failure_threshold = 1
  }
}
