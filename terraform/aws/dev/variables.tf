variable "expected_account_id" {
  description = "Independently verified target account ID. Supply privately through TF_VAR_expected_account_id."
  type        = string
  nullable    = false
  sensitive   = true

  validation {
    condition     = can(regex("^[0-9]{12}$", var.expected_account_id))
    error_message = "expected_account_id must contain exactly 12 digits."
  }
}

variable "region" {
  description = "This isolated development exercise is restricted to Canada Central."
  type        = string
  default     = "ca-central-1"
  nullable    = false

  validation {
    condition     = var.region == "ca-central-1"
    error_message = "Only ca-central-1 is approved for this exercise."
  }
}
