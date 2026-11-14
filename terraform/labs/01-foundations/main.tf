terraform {
  required_version = ">= 1.4.0"
}

resource "terraform_data" "server_configuration" {
  input = {
    name           = var.name
    environment    = var.environment
    region         = var.region
    instance_type  = var.instance_type
    install_nginx  = var.install_nginx
    install_docker = var.install_docker
  }
}