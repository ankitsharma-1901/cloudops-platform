output "vpc_id" {
  description = "ID of the isolated development VPC."
  value       = aws_vpc.dev.id
}

output "subnet_id" {
  description = "ID of the public subnet."
  value       = aws_subnet.public.id
}

output "instance_id" {
  description = "EC2 identifier used as the SSM session target after future provisioning."
  value       = aws_instance.dev.id
}

output "instance_state" {
  description = "EC2 lifecycle state from the last refresh; not live health or SSM readiness."
  value       = aws_instance.dev.instance_state
}

output "instance_private_ip" {
  description = "Private IPv4 address; assigned only after provisioning."
  value       = aws_instance.dev.private_ip
}

output "ami_id" {
  description = "Public AL2023 AMI resolved at plan time; latest parameter can change between plans."
  value       = nonsensitive(data.aws_ssm_parameter.al2023.value)
}
