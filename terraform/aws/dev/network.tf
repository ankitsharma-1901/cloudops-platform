resource "aws_vpc" "dev" {
  cidr_block           = "10.10.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = "cloudops-dev" }
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.dev.id
  cidr_block              = "10.10.1.0/24"
  map_public_ip_on_launch = false
  tags                    = { Name = "cloudops-dev-public" }
}

resource "aws_internet_gateway" "dev" {
  vpc_id = aws_vpc.dev.id
  tags   = { Name = "cloudops-dev" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.dev.id
  tags   = { Name = "cloudops-dev-public" }
}

resource "aws_route" "internet" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.dev.id
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

resource "aws_security_group" "instance" {
  name_prefix = "cloudops-dev-"
  description = "No inbound access; outbound HTTPS only for SSM"
  vpc_id      = aws_vpc.dev.id
  ingress     = []
  tags        = { Name = "cloudops-dev-instance" }
}

# Separate rule replaces AWS's default unrestricted outbound rule.
# HTTPS is protocol-limited, not restricted to AWS destinations.
resource "aws_vpc_security_group_egress_rule" "https" {
  security_group_id = aws_security_group.instance.id
  description       = "HTTPS to public SSM endpoints and HTTPS repositories"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  tags              = { Name = "cloudops-dev-https" }
}
