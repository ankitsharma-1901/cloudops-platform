output "state_bucket_name" {
  description = "Bucket name for the dev root's private backend configuration."
  value       = aws_s3_bucket.state.id
}
