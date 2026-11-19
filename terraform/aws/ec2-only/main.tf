provider "aws" {
  region              = "ca-central-1"
  allowed_account_ids = ["294665681966"]

  default_tags {
    tags = {
      Project     = "CloudOps"
      Environment = "development"
      ManagedBy   = "Terraform"
    }
  }
}

data "aws_subnet" "selected" {
  id = "subnet-08dfc8d266e48fc91"

  lifecycle {
    postcondition {
      condition     = self.vpc_id == "vpc-05828cfb0f6ae89e4" && self.owner_id == "294665681966" && self.availability_zone == "ca-central-1a" && self.state == "available"
      error_message = "The reviewed subnet must exist in the expected account, VPC and availability zone."
    }
  }
}

data "aws_security_group" "selected" {
  id     = var.security_group_id
  vpc_id = "vpc-05828cfb0f6ae89e4"

  lifecycle {
    postcondition {
      condition     = self.name != "default"
      error_message = "The default security group is not permitted."
    }
  }
}

resource "aws_instance" "dev" {
  ami                         = "ami-009d185a5ea80b390"
  instance_type               = "t3.micro"
  subnet_id                   = data.aws_subnet.selected.id
  vpc_security_group_ids      = [data.aws_security_group.selected.id]
  associate_public_ip_address = false
  monitoring                  = false

  credit_specification {
    cpu_credits = "standard"
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = 8
    encrypted             = true
    delete_on_termination = true
  }

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
    instance_metadata_tags      = "disabled"
  }

  tags = { Name = "cloudops-dev-ec2-only" }
}
