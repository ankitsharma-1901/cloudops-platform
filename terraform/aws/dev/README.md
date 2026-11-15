# Isolated AWS development configuration

This directory defines real AWS resources and completed its first controlled
deployment lifecycle on November 15, 2026. It is
independent of `terraform/labs/01-foundations/` and is never used by the CloudOps
API or worker. The first approved run created and verified all 12 resources,
reported no drift, and then destroyed the disposable environment. The current
dev resource state is empty; the protected remote backend and bootstrap
resources remain. Start with the
[approval and migration runbook](DEPLOYMENT.md) and
[permission proposal](PERMISSIONS.md) before any later deployment. A prior
successful run never authorizes a new apply or destroy.
The [deployment-role design](deployment-role/README.md) separates read-only
browser login from MFA-protected deployment and backend access.

## Files and architecture

| File | Purpose |
| --- | --- |
| `versions.tf` | Terraform >= 1.17.1, AWS provider >= 6.23 and < 7, encrypted S3 backend with native locking |
| `backend.s3.tfbackend.example` | Copy privately to an ignored backend file and set approved bucket/account values |
| `backend-policy.json.example` | Exact-state-key and lock-object backend access template |
| `providers.tf` | Canada Central provider, expected-account guard, fixed project tags |
| `variables.tf` | Required private account input and validation restricting the region |
| `network.tf` | VPC, public subnet, Internet Gateway, route, association, security group and HTTPS egress |
| `iam.tf` | EC2 trust role, SSM core policy attachment, instance profile |
| `compute.tf` | Public SSM AMI lookup and hardened single EC2 instance |
| `outputs.tf` | VPC/subnet/instance IDs, private IP, refreshed lifecycle state and selected AMI |
| `tests/safety.tftest.hcl` | Offline mock plans for security defaults and invalid inputs |
| `.terraform.lock.hcl` | Generated provider version/checksums; retain for reproducible initialization |

The VPC (`10.10.0.0/16`) contains one subnet (`10.10.1.0/24`). Its explicitly
associated route table sends `0.0.0.0/0` through the Internet Gateway. Terraform
lets AWS choose an available zone for this single subnet. The subnet does not
automatically assign public IPs; this one instance explicitly requests one.

The instance's security group has **no inbound rules**, including SSH. AWS's
default allow-all outbound rule is removed; the separate egress rule permits
only TCP 443 to IPv4 destinations. Return traffic is statefully allowed. This
limits ports, not destinations: any HTTPS destination is reachable. VPC-provided
DNS works through the Amazon resolver and is not filtered by security groups.
Default network ACL behavior is retained; no custom ACLs or IPv6 are configured.

The instance profile delivers temporary credentials for the EC2 role. Only EC2
can assume that role; its only configured permissions policy is the AWS-managed
`AmazonSSMManagedInstanceCore`. This is separate from the human Terraform
operator's permissions. No access keys, SSH keys, provisioners or user-data
commands are embedded. Existing resources with matching IAM names must be
reviewed before future provisioning; do not import or overwrite them blindly.

Amazon Linux 2023 x86_64 is looked up through the AWS-managed public parameter
`/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64`.
Its value can change, so later plans can propose instance replacement. Review
the selected AMI and replacement actions each time; no ignore-changes rule hides
image updates. Root storage is 8 GiB gp3, encrypted using the account's default
EBS encryption key, and deleted on termination. IMDSv2 tokens are required with
hop limit 1 for this host-only exercise. T3 standard CPU credits avoid surplus
credit billing but can throttle sustained CPU work.

## Authentication and IAM prerequisites

Use a current AWS CLI v2 with an already verified, non-root browser-login
profile. Provider >= 6.23 supports this credential type; the provider and
Terraform's S3 backend authenticate independently. The owner verified the
MFA-protected role and remote backend during the first deployment. Offline tests
still cannot prove future authentication or effective permissions. The bucket
is separately managed in `terraform/bootstrap/state/`.

`AWS_PROFILE` selects the profile; there is no hardcoded provider profile.
Remove stale credential environment overrides in your own terminal before
verification. Never print/export credentials into documentation. Check any
custom AWS endpoint/config-file environment overrides as well.

`allowed_account_ids` checks the authenticated account against the independently
supplied `expected_account_id`. It does not prove non-root authentication or
enforce plan-only execution. IAM permissions and the task's no-apply rule remain
essential. Do not copy the current CLI account automatically into the expected
value: compare it privately with the intended Console account first.

For an initial plan, the operator needs working authentication, STS identity
resolution, and `ssm:GetParameter` on the public AMI parameter. Provider reads
and future state refreshes also need the relevant EC2 Describe and IAM Get/List
permissions for the resources here. An existing read-only policy may cover
these reads; verify effective permissions, SCPs and permission boundaries.
`SignInLocalDevelopmentAccess` enables login, not infrastructure management.
No user policy is attached or changed by this configuration.

Future provisioning would separately require reviewed, scoped EC2 network,
security-group, instance, tagging and EBS actions; IAM role/profile creation and
attachment actions; and `iam:PassRole` limited to this EC2 role with
`iam:PassedToService = ec2.amazonaws.com`. These are prerequisites, not grants
or authorization to provision. Never add AdministratorAccess as a workaround.

## Systems Manager prerequisites

For each separately authorized deployment, SSM requires:

- The AL2023 SSM Agent installed, running and current; preinstallation does not
  prove that the node is online.
- The attached instance profile and effective SSM policy permissions.
- DNS resolution and outbound HTTPS to `ssm.ca-central-1.amazonaws.com` and
  `ssmmessages.ca-central-1.amazonaws.com` (older agents may also use
  `ec2messages.ca-central-1.amazonaws.com`). Here the public IPv4, route and
  Internet Gateway supply that network path without NAT or interface endpoints.
- Human operator permissions for Session Manager, scoped to the instance and
  permitted session document, plus appropriate resume/terminate permissions.
  The EC2 role does not grant the human access. CLI sessions also need the
  Session Manager plugin.

No inbound SSH is needed. Session transcript logging, KMS session settings and
audit configuration are not created by this exercise; review them before live
use. Existing account session preferences can require additional permissions.
The first deployment reached SSM `Online`, validating this path for that run.
Recheck it after every deployment because an earlier success does not guarantee
current agent, network, IAM or endpoint health.

## Manual setup and plan

Complete the runbook's bootstrap and backend approval gates first. The following
is a later owner-run example after backend setup/migration, not an offline check.
A locked remote plan creates/deletes its S3 lock object and requires approval
for those writes. Stop on errors or an unexpected account/root ARN. Authenticate
separately if needed; no login command is included or run by this task.

```powershell
Set-Location "D:\Devops\PythonLearn\cloudops-platform\terraform\aws\dev"
$env:AWS_PROFILE = "cloudops-deploy"
$env:AWS_REGION = "ca-central-1"

# Remove stale static credential overrides without displaying their contents.
Remove-Item Env:AWS_ACCESS_KEY_ID, Env:AWS_SECRET_ACCESS_KEY, Env:AWS_SESSION_TOKEN -ErrorAction SilentlyContinue

# Inspect privately: account must match the intended Console account; ARN must be non-root.
aws sts get-caller-identity --profile $env:AWS_PROFILE --region ca-central-1
if ($LASTEXITCODE -ne 0) { throw "Identity verification failed; stop here." }

# Enter the account independently verified in the intended Console account.
$env:TF_VAR_expected_account_id = Read-Host "Verified intended AWS account ID"

# Only after completing the runbook's appropriate init/migration path.
terraform init -input=false -backend-config=backend.s3.tfbackend
if ($LASTEXITCODE -ne 0) { throw "Terraform initialization failed." }
terraform fmt -check -recursive
if ($LASTEXITCODE -ne 0) { throw "Terraform formatting check failed." }
terraform validate
if ($LASTEXITCODE -ne 0) { throw "Terraform validation failed." }
terraform test
if ($LASTEXITCODE -ne 0) { throw "Mocked safety tests failed." }
terraform plan -input=false -no-color
if ($LASTEXITCODE -ne 0) { throw "Plan failed; review authentication, account and read permissions." }
```

The tests mock AWS and never prove real permissions or account-guard behavior.
The real plan performs AWS reads, credential resolution and backend lock-object
writes, but no EC2/VPC/IAM provisioning. This example does not save a plan;
the deployment runbook explains reviewed saved plans. Review every proposed
resource, the resolved AMI, tags, security rules and absence of unexpected
changes. Most resource IDs and instance state are unknown until provisioning;
`instance_state` after refresh would be lifecycle state, not SSM connectivity
or application health. Keep plan output private because it can contain account
identifiers even when inputs are marked sensitive.

## State and Git hygiene

The S3 backend isolates this environment at `cloudops/development/terraform.tfstate`
with `use_lockfile=true`. No state is shared with the local foundations lab or
application worker. The owner completed backend initialization for the first
deployment; any later migration or reconfiguration remains a separate approval.
Restrict filesystem access to local exports, backend metadata, state,
backup files, plans and `.terraform/`; they can hold sensitive values. Never
delete state to hide live resources. Existing repository ignores cover
`.terraform/`, `*.tfstate`, backups, `*.tfvars`, `*.tfvars.json` and `*.tfplan`.
Use the environment input above rather than committing an account ID. Use a
`.tfplan` extension if a future authorized workflow saves a plan. Review Git
status before staging; provider lock files are source-controlled metadata.

The independent `terraform/bootstrap/state/` root prepares the versioned,
encrypted, private bucket and TLS policy. Backend account guards are supplied in
the ignored `.tfbackend` file; variables cannot configure the backend. The
runbook separates fresh initialization from migration and requires protected
bootstrap-state backups. The bucket, remote state object and bootstrap IAM
resources remain after the dev teardown. The backend file, `.tflock` files and
saved-plan JSON exports are ignored.

## Costs and cleanup

The current destroyed dev environment has no running EC2/EBS/public-IPv4
charges from these 12 resources. Retained S3 state/history and bootstrap
resources can still have usage-dependent costs. If later provisioned, EC2, EBS, public
IPv4 and applicable transfer would incur charges. Do not assume free-tier
eligibility. Public IPv4 is listed at US$0.005/hour (about US$3.65 for 730 hours)
before credits; confirm current regional EC2 and gp3 prices using the AWS
calculator before approval. A US$20 budget alert is not a spending cap.

There are no NAT gateways, EIPs, paid interface endpoints, databases, clusters
or load balancers. No detailed monitoring is requested. Stopping a future
instance would not eliminate retained EBS costs. Root delete-on-termination does
not clean up snapshots or unrelated account resources.

For this task there are no deployed resources to clean up. Local caches or
unsaved plan output do not represent infrastructure. After any future approved
deployment, cleanup must be a separately authorized, reviewed operation using
the correct account and preserved state. No apply/destroy command is supplied
or executed here.

## Sources

Historical Day 8 checks: Terraform 1.17.1 initialized AWS provider 6.73.0 with
`init -backend=false -input=false`; `fmt -check -recursive` and `validate` passed.
`terraform test` passed all three mocked plan runs (secure defaults, malformed
account, disallowed region). No live AWS plan or identity check was run.

- [HashiCorp browser-login support discussion (>= 6.23)](https://github.com/hashicorp/terraform-provider-aws/issues/45316)
- [AWS CLI browser authentication](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sign-in.html)
- [AWS provider authentication and account guard](https://registry.terraform.io/providers/hashicorp/aws/latest/docs)
- [AL2023 public AMI parameter](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/finding-an-ami-parameter-store.html)
- [Session Manager prerequisites](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-prerequisites.html)
- [VPC/public IPv4 pricing](https://aws.amazon.com/vpc/pricing/)
- [AWS pricing calculator](https://calculator.aws/)
- [Terraform S3 backend and locking](https://developer.hashicorp.com/terraform/language/backend/s3)
