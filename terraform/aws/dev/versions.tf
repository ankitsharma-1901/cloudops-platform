terraform {
  required_version = ">= 1.17.1, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.23.0, < 7.0.0"
    }
  }

  # Bucket/account settings are supplied privately at initialization.
  backend "s3" {
    region               = "ca-central-1"
    key                  = "cloudops/development/terraform.tfstate"
    encrypt              = true
    use_lockfile         = true
    workspace_key_prefix = "cloudops/workspaces"
  }
}
