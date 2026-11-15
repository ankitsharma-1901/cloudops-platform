variable "expected_account_id" {
  description = "Independently verified account ID, supplied privately."
  type        = string
  nullable    = false
  sensitive   = true

  validation {
    condition     = can(regex("^[0-9]{12}$", var.expected_account_id))
    error_message = "expected_account_id must contain exactly 12 digits."
  }
}

variable "state_bucket_name" {
  description = "Globally unique, lowercase state bucket name. Do not use an account ID or secrets."
  type        = string
  nullable    = false

  validation {
    condition = (
      length(var.state_bucket_name) >= 3 && length(var.state_bucket_name) <= 63 &&
      can(regex("^[a-z0-9][a-z0-9-]*[a-z0-9]$", var.state_bucket_name)) &&
      !can(regex("^(xn--|sthree-|amzn-s3-demo-)|(-s3alias|--ol-s3|--x-s3|--table-s3)$", var.state_bucket_name))
    )
    error_message = "Use a 3-63 character lowercase letters/digits/hyphens bucket name with alphanumeric ends and no reserved S3 prefix/suffix."
  }
}
