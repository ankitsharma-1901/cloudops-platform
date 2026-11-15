# Proposed human-operated deployment role

This directory contains reusable **review templates**. The owner reports that
the independently configured deployment role and MFA-protected profile worked
during the November 15, 2026 lifecycle. These files contain no private profile
configuration and do not establish current effective permissions. An authorized
administrator must configure the role outside the environment it manages.
Do not create the deployment role through `terraform/aws/dev/` or grant it
permission to modify itself.

## Identities and policy placement

| Identity | Purpose | Grants |
| --- | --- | --- |
| `cloudops-dev` via existing `cloudops-iam` profile | Temporary browser-login source identity | Existing approved read/login policies plus `source-user-policy.json.example` |
| `cloudops-terraform-deploy` via proposed `cloudops-deploy` profile | Human-approved Terraform dev operations | Network, compute, instance-IAM templates here plus `../backend-policy.json.example` |
| Separate bucket-bootstrap administrator | Creates/protects the retained state bucket | Approved bootstrap grants in `../PERMISSIONS.md`; never attach these to the deployment role |
| `cloudops-dev-ec2-ssm` instance role | SSM Agent access from EC2 | Only `AmazonSSMManagedInstanceCore`; no deployment/backend policies |

The source user's read-only permissions do **not** carry into the assumed role.
ReadOnlyAccess is not a deployment grant. The administrator must review both the
source `sts:AssumeRole` permission and the destination trust relationship, along
with SCPs, boundaries and any explicit denies. No root credentials or
AdministratorAccess are used in this design.

## Files and scopes

- `trust-policy.json.example`: delegates from the intended account but narrows
  the caller with an exact `aws:PrincipalArn` match for `cloudops-dev` and
  requires `aws:MultiFactorAuthPresent=true`. The account's `:root` ARN denotes
  account delegation; the caller condition excludes root and other users.
  Verify the actual IAM user path/ARN privately before substitution.
- `source-user-policy.json.example`: grants only `sts:AssumeRole` on
  `cloudops-terraform-deploy`. Attach to the approved source user/group, not the
  instance role. No policy writes or resource creation are granted to the user.
- `network-policy.json.example`: regional reads; tagged resource creation;
  separately authorized, tagged parent VPC/group references; management and
  cleanup of tagged dev networking; tag-on-create permissions only. There is no
  inbound-rule authorization, NAT, EIP, peering or bucket administration grant.
- `compute-policy.json.example`: regional reads and public AL2023 parameter
  lookup; launch of one approved AMI with `t3.micro`, the exact SSM profile,
  required IMDSv2 and an encrypted gp3 root no larger than 8 GB; owned subnet/group
  references; regional launch NICs; tagged-instance termination and SSM readiness
  reads. New NICs have no inherited ownership tags, so the launch-only NIC ARN
  uses the account/region wildcard. No standalone volume/snapshot or start-session
  permissions are included.
- `instance-iam-policy.json.example`: manages only the exact EC2 SSM role and
  profile; managed attachments are restricted to `AmazonSSMManagedInstanceCore`;
  `iam:PassRole` is restricted to that role and `ec2.amazonaws.com`. No deployment
  role self-modification, arbitrary role, user, access-key, inline-policy write
  or arbitrary managed-policy attachment is permitted.
- `aws-config.ini.example`: credential-free role-profile example. Merge its
  substituted block privately into the user's shared AWS config; preserve the
  existing browser-login source profile. Do not copy credentials into it.
- `test_policies.py`: offline JSON/policy-boundary regression checks. These are
  structural checks, not an AWS IAM simulator or a proof of live authorization.
  The existing CI backend job runs them without credentials.

Replace `<ACCOUNT_ID>`, `<STATE_BUCKET>`, `<APPROVED_AMI_ID>` and
`<MFA_DEVICE_NAME>` privately where present; never commit filled templates.
Use the AMI selected by the reviewed AL2023 parameter lookup. An AMI change
requires an administrator to approve the new image ARN before a later launch.
Use separate customer-managed policies for network, compute and instance IAM,
plus the backend policy. Each template fits the 6,144 non-whitespace-character
managed-policy limit; do not concatenate them into one oversized policy.

These proposals target **initial creation and approved cleanup**. They do not
grant arbitrary future configuration updates: for example, tag edits, route
replacement and role trust-policy edits are not generally allowed. First obtain
an updated plan and review the exact additional actions if such an update is
requested. Missing permissions must fail closed, not trigger AdministratorAccess.
The earlier action inventory in `PERMISSIONS.md` is broader lifecycle guidance,
not an instruction to attach every listed action.

## Administrator review and approval

1. Verify the intended account, exact non-root caller, source MFA device and
   temporary authentication method. The trust template requires MFA; browser
   sign-in alone does not prove this STS context key is present. Test the actual
   CLI/SDK MFA flow. If it cannot meet the trust condition, stop and review an
   approved authentication method; do not remove MFA or create permanent keys.
2. Review template substitutions, service action/resource conditions, provider
   read paths and the actual plan. API action names and selected resource
   conditions were checked against AWS's public authorization reference, but
   these offline checks do not simulate effective IAM permissions. The owner
   reports successful role/backend authentication for the first lifecycle.
3. Outside the disposable environment, create `cloudops-terraform-deploy` with
   the reviewed trust policy and a 3,600-second maximum session duration. Attach
   only the four reviewed policy documents described above. This IAM-changing
   action requires explicit owner/administrator approval and is not executed here.
4. Add only the reviewed assume-role permission to the source user/group.
   Keep bucket creation/protection grants with the bootstrap administrator.
5. Review CloudTrail coverage and alarms for role assumptions and IAM changes;
   additional audit infrastructure needs its own cost/permission approval.

Tags limit accidental reach but are not a hard isolation boundary against other
principals who can retag resources. Generated IDs require regional/account ARN
wildcards before creation. Narrow to known IDs where practical after deployment.
IAM cannot constrain every SG egress property through this policy; review the
saved plan's HTTPS-only rule. The role can create more than one instance/network
set with matching tags: it does not enforce a one-instance quota or budget cap.
The initial `CreateRole` grant can specify a different trust document for the
one permitted SSM role, although only SSM core policy attachment is allowed;
administrator review of that saved-plan trust policy remains necessary.

## Owner's future role-profile verification

After administrator approval/configuration, merge the example profile with
private substitutions. The `mfa_serial` should identify an actual TOTP-compatible
device usable by the CLI flow; do not assume browser/passkey MFA is equivalent.
The `cloudops-deploy-session` profile lets the CLI request MFA interactively and
obtain temporary role credentials. The `cloudops-deploy` process profile lets
Terraform use the CLI-resolved session without relying on SDK-specific MFA
prompt support. The process response goes directly to the consuming SDK, not
to the terminal or a credentials file; never invoke that export command by hand.
Protect the AWS CLI's temporary credential caches with user-only filesystem
permissions and encrypted storage; they are not permanent access keys.
Both Terraform's S3 backend and AWS provider then use `AWS_PROFILE`—there is no
provider-only assume-role block that could leave backend access on the user.

```powershell
# Owner only, after role/profile approval; inspect identity results privately.
Remove-Item Env:AWS_ACCESS_KEY_ID, Env:AWS_SECRET_ACCESS_KEY, Env:AWS_SESSION_TOKEN -ErrorAction SilentlyContinue
$env:AWS_PROFILE = "cloudops-deploy"
$env:AWS_REGION = "ca-central-1"
# Warm/verify the MFA-protected CLI session first; stop on any error.
aws sts get-caller-identity --profile cloudops-deploy-session --query '{Account:Account,Arn:Arn}'
if ($LASTEXITCODE -ne 0) { throw "Stop: source/MFA session verification failed." }
aws sts get-caller-identity --profile cloudops-deploy --query '{Account:Account,Arn:Arn}'
if ($LASTEXITCODE -ne 0) { throw "Stop: role/MFA verification failed." }
```

Expected identity: the verified target account and an assumed-role ARN for
`cloudops-terraform-deploy/cloudops-dev-deployment`, not the source IAM user or
root. The owner reports this identity was verified for the first lifecycle;
repeat the verification for each separately approved session. Do not manually run `sts assume-role`
without output filtering: its ordinary response prints credentials. No such
credential-export command is needed here.

If either source login or role credentials expire, renew the approved source
authentication and warm the CLI session again. Do not enable credential debug
logging or print/cache the process response yourself.

Continue with `../DEPLOYMENT.md` using `AWS_PROFILE=cloudops-deploy` for both
backend and dev. Do not replace the bootstrap administrator's identity with
the deployment role. Keep the provider and backend expected-account checks.
Backend initialization/migration and real locked plans still require separate
approval for state and lock-object writes. Assume-role verification is not
approval to provision.

## Offline check

```powershell
# From the repository root, using the existing Python environment.
& .\backend\.venv\Scripts\python.exe -m unittest discover -s terraform/aws/dev/deployment-role -p test_policies.py -v
```

Sources: [CLI role profiles](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-role.html),
[source assume-role permission](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_use_permissions-to-switch.html),
[MFA-protected API access](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_credentials_mfa_configure-api-require.html),
[CLI credential-process format](https://docs.aws.amazon.com/cli/latest/reference/configure/export-credentials.html),
[EC2 authorization metadata](https://servicereference.us-east-1.amazonaws.com/v1/ec2/ec2.json).
