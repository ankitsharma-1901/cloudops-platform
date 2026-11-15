terraform {
  required_version = ">= 1.17.1, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.23.0, < 7.0.0"
    }
  }

  # Independent lifecycle: this root must never depend on the dev environment.
  backend "local" {}
}

provider "aws" {
  region              = "ca-central-1"
  allowed_account_ids = [var.expected_account_id]

  default_tags {
    tags = {
      Project     = "CloudOps"
      Environment = "bootstrap"
      ManagedBy   = "Terraform"
    }
  }
}
