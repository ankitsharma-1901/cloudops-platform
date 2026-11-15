# Authentication comes from AWS_PROFILE and the local AWS credential chain.
# Do not bypass credential validation or account-ID checks.
provider "aws" {
  region              = var.region
  allowed_account_ids = [var.expected_account_id]

  default_tags {
    tags = {
      Project     = "CloudOps"
      Environment = "development"
      ManagedBy   = "Terraform"
    }
  }
}
