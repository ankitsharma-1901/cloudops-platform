# Independent Terraform state bootstrap

This root prepares one state bucket in `ca-central-1`. It is not part of the
development EC2 environment and must outlive that environment. The owner reports
that the bucket was created during the November 15, 2026 approved lifecycle and
remains after dev teardown. Follow the [Day 9 approval runbook](../../aws/dev/DEPLOYMENT.md) before
initialization against AWS, bucket creation, or state migration.

- `versions.tf`: pinned-compatible provider, required Terraform version, local
  bootstrap state, expected-account guard and tags.
- `variables.tf`: private expected account and validated unique bucket name.
- `main.tf`: retained bucket, blocked public access, ownership enforcement,
  versioning, explicit SSE-S3 encryption and HTTPS-only bucket policy.
- `outputs.tf`: bucket name for the dev root's private backend file.
- `tests/safety.tftest.hcl`: mocked plan assertions and invalid-input checks.
- `.terraform.lock.hcl`: same reviewed AWS provider selection as dev, including
  upstream platform checksums; retain it in Git.

Bootstrap state stays local and must be backed up to encrypted, owner-only
storage independently. Do not put it into the dev state key or treat it as a
throwaway cache. `prevent_destroy` and `force_destroy=false` are safeguards,
not a substitute for restricted IAM. No lifecycle expiration is configured for
state versions, because recovery retention needs an explicit owner decision.

The [permission proposal](../../aws/dev/PERMISSIONS.md) distinguishes bootstrap
administration from deployment/backend access. Bucket creation/protection changes
require separate approval. Routine dev cleanup must never delete this bucket.
