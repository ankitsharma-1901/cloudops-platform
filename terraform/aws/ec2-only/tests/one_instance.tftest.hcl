mock_provider "aws" {
  mock_data "aws_subnet" {
    defaults = {
      vpc_id            = "vpc-05828cfb0f6ae89e4"
      owner_id          = "294665681966"
      availability_zone = "ca-central-1a"
      state             = "available"
    }
  }

  mock_data "aws_security_group" {
    defaults = {
      name = "cloudops-restricted"
    }
  }
}

run "one_private_hardened_instance" {
  command = plan

  variables {
    security_group_id = "sg-0123456789abcdef0"
  }

  assert {
    condition     = aws_instance.dev.iam_instance_profile == ""
    error_message = "The instance profile must be known and empty at plan time."
  }

  assert {
    condition     = aws_instance.dev.ami == "ami-009d185a5ea80b390" && aws_instance.dev.instance_type == "t3.micro" && aws_instance.dev.associate_public_ip_address == false
    error_message = "The reviewed AMI, instance type and private networking are required."
  }

  assert {
    condition     = aws_instance.dev.root_block_device[0].encrypted && aws_instance.dev.root_block_device[0].volume_size == 8 && aws_instance.dev.root_block_device[0].volume_type == "gp3" && aws_instance.dev.metadata_options[0].http_tokens == "required"
    error_message = "The encrypted disk and IMDSv2 are required."
  }
}

run "default_security_group_rejected" {
  command = plan

  variables {
    security_group_id = "sg-0a1df66f2332f53e3"
  }

  expect_failures = [var.security_group_id]
}
