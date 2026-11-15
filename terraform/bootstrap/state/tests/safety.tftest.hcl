mock_provider "aws" {}

variables {
  expected_account_id = "000000000000"
  state_bucket_name   = "cloudops-state-synthetic-test"
}

run "protected_state_bucket" {
  command = plan

  assert {
    condition     = !aws_s3_bucket.state.force_destroy
    error_message = "Never empty the state bucket automatically."
  }
  assert {
    condition     = aws_s3_bucket_versioning.state.versioning_configuration[0].status == "Enabled"
    error_message = "State history must be versioned."
  }
  assert {
    condition     = alltrue([for rule in aws_s3_bucket_server_side_encryption_configuration.state.rule : rule.apply_server_side_encryption_by_default[0].sse_algorithm == "AES256"])
    error_message = "State must use explicit SSE-S3 encryption."
  }
  assert {
    condition     = aws_s3_bucket_public_access_block.state.block_public_acls && aws_s3_bucket_public_access_block.state.block_public_policy && aws_s3_bucket_public_access_block.state.ignore_public_acls && aws_s3_bucket_public_access_block.state.restrict_public_buckets
    error_message = "All public access protections are required."
  }
  assert {
    condition     = aws_s3_bucket_ownership_controls.state.rule[0].object_ownership == "BucketOwnerEnforced"
    error_message = "Disable bucket ACLs."
  }
}

run "invalid_bucket_name" {
  command = plan
  variables { state_bucket_name = "Invalid.Bucket" }
  expect_failures = [var.state_bucket_name]
}

run "invalid_account" {
  command = plan
  variables { expected_account_id = "invalid" }
  expect_failures = [var.expected_account_id]
}

run "bucket_name_too_short" {
  command = plan
  variables { state_bucket_name = "ab" }
  expect_failures = [var.state_bucket_name]
}

run "reserved_bucket_suffix" {
  command = plan
  variables { state_bucket_name = "cloudops-state--x-s3" }
  expect_failures = [var.state_bucket_name]
}
