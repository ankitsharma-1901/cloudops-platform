# Day 9: first-deployment approval runbook

Status: first lifecycle completed November 15, 2026. The owner reports that the
approved workflow created the protected backend and deployment IAM outside the
dev root, created all 12 planned dev resources, verified EC2 `running`, SSM
`Online` and no drift, and then removed all 12 dev resources. The current dev
resource state is empty. The S3 bucket, remote state object and bootstrap IAM
resources remain intentionally. This document remains the approval runbook for
future deployments; the completed run does not pre-authorize another operation.

## Review and approval boundaries

There are five independent owner approval gates:

0. **Deployment IAM:** authorize the administrator-created deployment role,
   reviewed trust/MFA conditions, source-user assume-role grant and scoped
   policy attachments in [deployment-role/README.md](deployment-role/README.md).
   No role is created by the disposable environment or by this preparation task.
1. **Bootstrap:** authorize the exact state bucket name/account, reviewed bucket
   plan, administrator permissions and ongoing S3 charges. Only then create
   the bucket through the separate `terraform/bootstrap/state/` root.
2. **State initialization/migration:** authorize backend access and any state
   upload/lock-object writes after verifying bucket ownership and protections.
3. **EC2 deployment:** approve the exact saved dev plan, deployment IAM grants,
   expected monthly cost and cleanup time. A new/changed plan needs new review.
4. **Cleanup:** approve a saved destruction plan for dev resources only.
   Retain the bucket, remote state history and independent bootstrap state.

No AWS-changing step is authorized merely by merging code or passing CI.
Even a remote **plan** acquires/releases an S3 lock, which writes/deletes an
object. Do not run a remote plan during an offline-only session. Do not bypass
locking with `-lock=false`, use `-force-copy`, or automatically confirm migration.
The mutating commands below are for the owner only after the corresponding
specific approval; none was executed while preparing this runbook.

## Prepared configuration and prerequisites

- Existing EC2/VPC/IAM resource definitions are preserved: one public-subnet
  AL2023 x86_64 `t3.micro`, 8 GiB encrypted gp3 root, required IMDSv2, standard
  CPU credits, SSM-only role and instance profile. No inbound access is allowed;
  the only outbound rule is TCP 443 to IPv4 destinations.
- VPC DNS, the subnet's Internet Gateway route, public IPv4 and stateful HTTPS
  return traffic provide the intended SSM network path without NAT. The agent
  must be running and able to reach the regional `ssm` and `ssmmessages`
  endpoints; older agents can also use `ec2messages`. This is configuration
  readiness, not a successful connectivity test.
- Confirm the instance becomes an SSM managed node with `PingStatus=Online`
  after deployment. Human session permissions and the CLI Session Manager
  plugin are separate from the instance profile. Starting a shell/session is
  an operational action requiring explicit approval, not a read-only health check.
- Use verified temporary non-root credentials selected by `AWS_PROFILE`.
  Provider `allowed_account_ids` and the region validation remain unchanged.
  The **backend** has separate account checks in the private `.tfbackend` file.
- Use Terraform >= 1.17.1 (CI uses 1.17.1) and the locked AWS provider. The
  backend is part of Terraform itself; provider login support alone does not
  prove backend login works. Validate it with the owner's profile at gate 2.
- Review [PERMISSIONS.md](PERMISSIONS.md), private effective permissions,
  SCPs/boundaries, root credential hygiene/MFA, and the planned budget alerts.
  No claim is made that these account settings have been verified.
- Verify the account's default EBS encryption key before approval. The permission
  proposal assumes the AWS-managed EBS key; if a customer-managed default is
  configured, stop and separately review its key policy and required grants.
  Do not silently add broad KMS permissions or weaken disk encryption.

## Offline checks: no AWS credentials required

The following uses isolated, ignored provider-data directories. It does not
read/rewrite your existing `.terraform/terraform.tfstate` backend metadata,
initialize S3, migrate state or run a real plan. Stop if any command fails.
The tests contain only `command = plan` with mocked AWS providers.

```powershell
Set-Location "D:\Devops\PythonLearn\cloudops-platform"
$env:AWS_EC2_METADATA_DISABLED = "true"
foreach ($module in @("terraform/bootstrap/state", "terraform/aws/dev")) {
    $env:TF_DATA_DIR = Join-Path (Resolve-Path $module) ".terraform/day9-offline"
    terraform "-chdir=$module" fmt -check -recursive
    if ($LASTEXITCODE -ne 0) { throw "Formatting failed: $module" }
    terraform "-chdir=$module" init -backend=false -input=false -lockfile=readonly
    if ($LASTEXITCODE -ne 0) { throw "Initialization failed: $module" }
    terraform "-chdir=$module" validate
    if ($LASTEXITCODE -ne 0) { throw "Validation failed: $module" }
    terraform "-chdir=$module" test
    if ($LASTEXITCODE -ne 0) { throw "Mock tests failed: $module" }
}
Remove-Item Env:TF_DATA_DIR -ErrorAction SilentlyContinue
```

## Gate 1: independent bucket bootstrap

Use a separate owner session to verify identity privately; do not paste account
IDs, ARNs, credential output, plans or state into public documentation.

```powershell
$env:AWS_PROFILE = "REPLACE_WITH_APPROVED_BOOTSTRAP_ADMIN_PROFILE"
$env:AWS_REGION = "ca-central-1"
Remove-Item Env:TF_DATA_DIR -ErrorAction SilentlyContinue
Remove-Item Env:AWS_ACCESS_KEY_ID, Env:AWS_SECRET_ACCESS_KEY, Env:AWS_SESSION_TOKEN -ErrorAction SilentlyContinue
aws sts get-caller-identity --profile $env:AWS_PROFILE --region ca-central-1
if ($LASTEXITCODE -ne 0) { throw "Stop: authentication failed." }
# Compare privately with the intended Console account and reject root identity.
$env:TF_VAR_expected_account_id = Read-Host "Independently verified account ID"
$env:TF_VAR_state_bucket_name = Read-Host "Approved globally unique state bucket name"
```

Use a separately approved, non-root bootstrap administrator profile in that
example; do not add bucket-admin permissions to the read-only `cloudops-iam`
user or the deployment role. The bootstrap identity differs from the dev
deployment identity in this design. An
administrator must review its exact S3 grants; do not elevate the IAM user to
AdministratorAccess. Review names for collisions; never import an unrelated
bucket or overwrite existing state. Store bootstrap local state and any backup
on encrypted storage with an owner-only filesystem ACL and a separate secure
backup location. Its lifecycle is independent of EC2; it is not disposable.

After approval for bootstrap planning (authenticated reads; local plan file):

```powershell
terraform -chdir=terraform/bootstrap/state init -input=false -lockfile=readonly
if ($LASTEXITCODE -ne 0) { throw "Bootstrap init failed." }
terraform -chdir=terraform/bootstrap/state plan -input=false -out=bootstrap.tfplan
if ($LASTEXITCODE -ne 0) { throw "Bootstrap plan failed." }
terraform -chdir=terraform/bootstrap/state show bootstrap.tfplan
```

Review six S3 resources: bucket, ownership controls, public-access block,
versioning, encryption and TLS policy. There must be no IAM/EC2 changes. Retain
the provider lock file in Git. The bucket sets `force_destroy=false` and
`prevent_destroy=true`; removing configuration can bypass Terraform's guard,
so the deployment operator must not have bucket deletion/administration grants.

**Gate 1 write approval required:** owner executes only the reviewed saved plan:

```powershell
terraform -chdir=terraform/bootstrap/state apply bootstrap.tfplan
```

This creates the bucket/protections and local bootstrap state. Do not repeat
after a partial failure without reviewing a fresh plan. Record only sanitized
outcomes. For auditing, review S3 server access logging to a separate log bucket,
CloudTrail S3 data events and CloudWatch metrics/alarms before live use. These
are recommended follow-ups with additional cost and permissions; they are not
silently enabled by this minimal bootstrap. Do not log back into the state bucket.

## Gate 2: verify bucket and initialize/migrate dev state

With approved read access and the verified expected account variable:

```powershell
aws s3api head-bucket --bucket $env:TF_VAR_state_bucket_name --expected-bucket-owner $env:TF_VAR_expected_account_id --region ca-central-1
aws s3api get-bucket-location --bucket $env:TF_VAR_state_bucket_name --expected-bucket-owner $env:TF_VAR_expected_account_id
aws s3api get-bucket-versioning --bucket $env:TF_VAR_state_bucket_name --expected-bucket-owner $env:TF_VAR_expected_account_id
aws s3api get-bucket-encryption --bucket $env:TF_VAR_state_bucket_name --expected-bucket-owner $env:TF_VAR_expected_account_id
aws s3api get-public-access-block --bucket $env:TF_VAR_state_bucket_name --expected-bucket-owner $env:TF_VAR_expected_account_id
aws s3api get-bucket-ownership-controls --bucket $env:TF_VAR_state_bucket_name --expected-bucket-owner $env:TF_VAR_expected_account_id
aws s3api get-bucket-policy --bucket $env:TF_VAR_state_bucket_name --expected-bucket-owner $env:TF_VAR_expected_account_id
```

Stop on **any** failed command. Privately verify Canada Central, `Enabled`
versioning, AES256 encryption, all four public-access flags true, owner-enforced
ownership and TLS deny policy. The deployment operator is not required to have
all administrative inspection permissions; an approved bucket administrator can
perform these checks. `expected_bucket_owner` is not a Terraform S3 backend
argument in the selected Terraform version; these CLI checks perform ownership
verification, and `allowed_account_ids` guards the authenticated backend account.

Copy `backend.s3.tfbackend.example` to ignored `backend.s3.tfbackend`, replace
the bucket name and account ID privately, and confirm its account matches
`TF_VAR_expected_account_id`. Backend files cannot reference Terraform variables.
Do not include credentials or profile names. Preserve the default workspace;
the scoped backend policy does not grant access to named workspace state.

The file `.terraform/terraform.tfstate` is **backend metadata**, not the actual
managed-resource state. Do not migrate or upload it as environment state.
Look for `terraform/aws/dev/terraform.tfstate` and its backup instead. At the
start of this preparation task neither root-level file existed; that alone
does not prove there is no state elsewhere. Check the owner's prior state path
and intended S3 key privately. Stop if an unexpected existing remote state/key
or different lineage is found.

If genuine local resource state exists, secure a timestamped copy in encrypted,
owner-only storage before proceeding. Do not delete it or the old backend cache
to fix an initialization error. Before using the dev backend, complete the
deployment-role guide's private CLI-session verification and set
`$env:AWS_PROFILE = "cloudops-deploy"`. Both backend and provider must resolve
to the same approved role/account; keep the separate expected-account checks.
**Gate 2 write approval required:**

```powershell
# Existing local resource state: preserve cache and interactively review migration.
terraform -chdir=terraform/aws/dev init -migrate-state -backend-config=backend.s3.tfbackend
```

Do not combine `-migrate-state` with `-reconfigure`. Do not use `-force-copy`.
Review the source/destination and answer the prompt only after verifying them.
If there is no prior resource state, use fresh initialization instead:

```powershell
# Only after confirming no state needs migration and the target key is correct.
terraform -chdir=terraform/aws/dev init -reconfigure -backend-config=backend.s3.tfbackend
```

Both paths access the real backend; migration uploads state and remote
operations can create lock objects. With prior resources, compare resource
addresses, lineage and serial privately before/after migration using restricted
local exports, without printing state into logs. Preserve the backup until
recovery has been verified. Never commit exported state. State version recovery
and stale-lock removal require separate approval; establish that no writer is
active before any force-unlock or restoration. Never delete a lock automatically.

## Gate 3: approved development plan and deployment

Have the administrator approve the deployment IAM policy described in
`PERMISSIONS.md`. Then authorize backend lock writes and create a saved plan:

```powershell
terraform -chdir=terraform/aws/dev plan -input=false -lock-timeout=60s -out=deployment.tfplan
if ($LASTEXITCODE -ne 0) { throw "Stop: plan failed." }
terraform -chdir=terraform/aws/dev show deployment.tfplan
```

The first clean deployment should propose 12 managed resources (VPC, subnet,
Internet Gateway, route table, route, association, security group, HTTPS rule,
role, attachment, profile and instance), with no bucket resource and no unrelated
change. Check account/region, AMI architecture, tags, encrypted disk, IMDSv2,
SSM role and no inbound rules. A differing existing state can change the count;
resolve every unexpected change rather than approving a count alone. Review
the AMI parameter's current value; it can change between plans. Stop on any
authentication, account or permission failure. Never disable guardrails to proceed.

**Gate 3 write approval required:** approve cost, cleanup time and exact saved
plan, then the owner may execute it once:

```powershell
terraform -chdir=terraform/aws/dev apply deployment.tfplan
```

Saved plans may contain sensitive data and can become stale. Keep their ACL
restricted, never publish them as CI artifacts, and discard/review a new plan
after configuration or state changes. CI remains validation-only with no secrets
or OIDC; it cannot bootstrap, migrate or deploy.

## Deployment verification (read-only)

After an approved deployment, obtain the instance ID from `terraform output
-raw instance_id`. Privately verify:

```powershell
$instanceId = terraform -chdir=terraform/aws/dev output -raw instance_id
if ($LASTEXITCODE -ne 0) { throw "Cannot read instance output." }
aws ec2 describe-instances --instance-ids $instanceId --region ca-central-1
aws ec2 describe-instance-status --instance-ids $instanceId --include-all-instances --region ca-central-1
aws ssm describe-instance-information --filters "Key=InstanceIds,Values=$instanceId" --region ca-central-1
```

Check EC2 state/status checks, IMDS tokens required, instance-profile ARN,
subnet, security-group IDs and tags. Separately inspect the attached group
(`ec2:DescribeSecurityGroups`), root volume (`ec2:DescribeVolumes`), route table
(`ec2:DescribeRouteTables`) and IAM attachment (`iam:ListAttachedRolePolicies`).
Confirm encrypted gp3 storage, empty inbound rules, HTTPS-only egress, correct
default route and SSM-only role. Verification additionally needs
`ec2:DescribeInstanceStatus` and `ssm:DescribeInstanceInformation`; scope the
operator's read grants appropriately. An online managed-node result is the SSM
connectivity check; an `instance_state` output is not a health check.
If SSM is offline, investigate DNS, public IP, routes, profile/policy, agent and
existing account session preferences; do not add inbound SSH as a shortcut.

## Costs (USD, public prices retrieved November 15, 2026)

Assumptions: Canada Central, Linux on-demand/shared tenancy, 730 running hours,
8 GB gp3 at baseline IOPS/throughput, one public IPv4, no credits/discounts/tax.
Calculations were performed with Python Decimal, not inferred from a budget.

| Component | Retrieved rate | Monthly estimate |
| --- | --- | --- |
| `t3.micro` Linux | $0.0116/hour | $8.468 |
| 8 GB gp3 | $0.088/GB-month | $0.704 |
| Public IPv4 | $0.005/hour | $3.650 |
| Known subtotal | | **$12.822 (about $12.82)** |

S3 Standard storage for state **and all retained versions**, state/lock requests,
transfer, and any added logging/monitoring are extra and usage-dependent. This
subtotal is not a full bill or a spending cap; confirm a calculator estimate and
actual budget alerts before approval. No NAT, paid interface endpoints, EIP,
load balancer, database or cluster is added. SSE-S3 has no customer-managed KMS
key fee. Stopping EC2 retains EBS costs; standard T3 credits avoid surplus-credit
billing but may throttle CPU. State history is intentionally not auto-expired.

Sources: [AWS regional EC2 pricing feed](https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/ec2-ondemand-without-sec-sel/Canada%20%28Central%29/Linux/index.json),
[AWS regional EC2/EBS offer CSV](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonEC2/current/ca-central-1/index.csv),
[IPv4 pricing](https://aws.amazon.com/vpc/pricing/),
[S3 pricing](https://aws.amazon.com/s3/pricing/),
[AWS calculator](https://calculator.aws/).

## Gate 4: dev cleanup and retained state

After explicit approval for cleanup planning and backend lock writes, the owner
can create/review a saved destruction plan in **dev only**:

```powershell
terraform -chdir=terraform/aws/dev plan -destroy -input=false -lock-timeout=60s -out=cleanup.tfplan
if ($LASTEXITCODE -ne 0) { throw "Cleanup plan failed." }
terraform -chdir=terraform/aws/dev show cleanup.tfplan
```

Confirm only approved CloudOps dev resources are removed. After **separate gate
4 write approval** the owner may execute `terraform -chdir=terraform/aws/dev
apply cleanup.tfplan`. No destroy/apply command was run by the assistant.
Verify instance termination, root-volume deletion, detached/deleted network and
IAM resources, and absence of remaining charges. Verify state reflects cleanup.
Never target the bootstrap root, delete the state bucket/history, remove the
state to hide resources, or remove `prevent_destroy` as routine cleanup. Retain
secure bootstrap backups and remote dev history for recovery/audit. Review any
future decision to retire the backend as a separate administrative change.

## Future-run release gates

The first deployment verified MFA-protected role authentication, remote backend
operation, live SSM readiness, no drift and complete dev cleanup. Every future
run must repeat the applicable identity, account, bucket, policy, saved-plan,
cost, SSM and cleanup checks because credentials, AMIs, policies, state and AWS
service conditions can change. Offline tests prove configuration behavior only.

References: [S3 backend and locking](https://developer.hashicorp.com/terraform/language/backend/s3),
[S3 security guidance](https://docs.aws.amazon.com/AmazonS3/latest/userguide/security-best-practices.html),
[Session Manager prerequisites](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-prerequisites.html).
