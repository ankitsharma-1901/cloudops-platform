output "selected_configuration" {
  description = "The validated sample configuration stored by terraform_data."
  value       = terraform_data.server_configuration.output
}

output "configuration_id" {
  description = "The local Terraform identifier for the stored configuration."
  value       = terraform_data.server_configuration.id
}
