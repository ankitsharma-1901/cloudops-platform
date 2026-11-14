variable "name" {
  description = "A short, lowercase identifier for the sample server."
  type        = string
  default     = "web-dev-01"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,30}[a-z0-9]$", var.name))
    error_message = "Name must be 3-32 lowercase letters, digits, or hyphens, and start with a letter."
  }
}

variable "environment" {
  description = "The environment associated with this configuration."
  type        = string
  default     = "development"

  validation {
    condition     = contains(["development", "staging"], var.environment)
    error_message = "Environment must be development or staging."
  }
}

variable "region" {
  description = "The AWS region this sample configuration describes; no AWS resources are created."
  type        = string
  default     = "ca-central-1"

  validation {
    condition     = contains(["ca-central-1", "us-east-1"], var.region)
    error_message = "Region must be ca-central-1 or us-east-1."
  }
}

variable "instance_type" {
  description = "The instance type this sample configuration describes."
  type        = string
  default     = "t3.micro"

  validation {
    condition     = contains(["t3.micro", "t3.small"], var.instance_type)
    error_message = "Instance type must be t3.micro or t3.small."
  }
}

variable "install_nginx" {
  description = "Whether the sample configuration requests Nginx installation."
  type        = bool
  default     = true
}

variable "install_docker" {
  description = "Whether the sample configuration requests Docker installation."
  type        = bool
  default     = true
}
