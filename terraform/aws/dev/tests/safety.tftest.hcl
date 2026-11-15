# Mock plans only: no real provider authentication or AWS calls.
mock_provider "aws" {
  mock_data "aws_ssm_parameter" {
    defaults = { value = "ami-0123456789abcdef0" }
  }
}

variables {
  # Synthetic fixture, not an account used by this project.
  expected_account_id = "000000000000"
}

run "secure_defaults" {
  command = plan

  assert {
    condition     = aws_vpc.dev.cidr_block == "10.10.0.0/16" && aws_subnet.public.cidr_block == "10.10.1.0/24"
    error_message = "Keep the isolated development network ranges."
  }
  assert {
    condition     = length(aws_security_group.instance.ingress) == 0
    error_message = "The security group must have no inbound rules."
  }
  assert {
    condition     = aws_vpc_security_group_egress_rule.https.from_port == 443 && aws_vpc_security_group_egress_rule.https.to_port == 443 && aws_vpc_security_group_egress_rule.https.ip_protocol == "tcp"
    error_message = "Only outbound TCP HTTPS is permitted."
  }
  assert {
    condition     = aws_instance.dev.instance_type == "t3.micro" && aws_instance.dev.credit_specification[0].cpu_credits == "standard"
    error_message = "Keep the small instance and avoid unlimited CPU credit charges."
  }
  assert {
    condition     = aws_instance.dev.root_block_device[0].encrypted && aws_instance.dev.metadata_options[0].http_tokens == "required"
    error_message = "Encrypted root storage and IMDSv2 are required."
  }
  assert {
    condition     = aws_iam_role_policy_attachment.ssm.policy_arn == "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
    error_message = "The instance role must use the SSM core policy."
  }
  assert {
    condition     = aws_instance.dev.iam_instance_profile == aws_iam_instance_profile.instance.name && aws_iam_instance_profile.instance.role == aws_iam_role.instance.name
    error_message = "EC2 must use the SSM role through its instance profile."
  }
  assert {
    condition     = aws_instance.dev.associate_public_ip_address && aws_vpc.dev.enable_dns_support && aws_vpc.dev.enable_dns_hostnames && aws_route.internet.destination_cidr_block == "0.0.0.0/0"
    error_message = "Preserve the intended public HTTPS/DNS path for Systems Manager."
  }
}

run "reject_malformed_account" {
  command = plan
  variables { expected_account_id = "invalid" }
  expect_failures = [var.expected_account_id]
}

run "reject_other_region" {
  command = plan
  variables { region = "us-east-1" }
  expect_failures = [var.region]
}
