variable "security_group_id" {
  description = "Existing reviewed security group in the selected VPC; no inbound SSH."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^sg-[0-9a-f]{8}([0-9a-f]{9})?$", var.security_group_id)) && var.security_group_id != "sg-0a1df66f2332f53e3"
    error_message = "Supply an existing non-default reviewed security group ID."
  }
}
