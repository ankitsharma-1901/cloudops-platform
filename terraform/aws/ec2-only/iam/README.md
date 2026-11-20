# Proposed dedicated EC2-only role

These documents are review drafts, not authorization to create the role. The
EC2-only plan policy still rejects provider-computed unknown profile and launch
template fields. No real EC2-only plan or apply has succeeded under this role.

The trust permits only `cloudops-dev` when AWS supplies the MFA context key.
The source user also needs `operator-assume-policy.json`. The role's permissions
allow the reviewed launch resources, state and native lock, and explicitly deny
role passing, profile association and use of a launch-template resource. There
is deliberately no termination permission. These statements need independent
effective-IAM simulation and review including boundary, session, organization,
bucket and encryption controls. Do not assume the absence of `iam:PassRole`
alone makes an unverified launch safe.

## Policy simulation and limitations

The approved six-resource EC2 VPC/EBS/subnet simulation returned allowed when
supplied the reviewed tags, `t3.micro`, IMDSv2, encrypted 8 GiB gp3 and no
public IPv4 or profile. Separate simulations rejected a wrong AMI, subnet,
security group or instance type; IMDSv1; a profile; public IPv4; an unencrypted
volume; and a volume larger than 8 GiB. These are simulations with supplied
context, not proof of actual EC2 request context or effective role permissions.
The dedicated role has not been created or tested live. Confirm the
`ec2:AssociatePublicIpAddress` condition key is present and false in the actual
launch authorization context; missing context fails the allow closed.

The S3 list allow uses only the exact state and lock prefixes and
`cloudops/workspaces/`. Terraform's S3 backend lists the configured workspace
prefix even for state-manager initialization; non-default workspace state-object
access is not granted. Confirm the bucket's missing-object behavior and the
actual effective bucket permissions during separately authorized setup.

The IAM simulator expects `--policy-input-list` to be a JSON array of policy
document strings. Passing a policy file directly as the CLI list parameter
previously produced a `ValidationError` instead of evaluating the document.

For future approved cleanup, separately authorize a Terraform destroy plan and
limit termination to the exact reconciled instance ARN; tags alone do not prove
ownership. No destroy workflow or termination permission is included here.

After independent policy review and separate operator authorization, from the
repository root using an administrator profile selected out of band:

```powershell
$admin = '<APPROVED_ADMIN_PROFILE>'
aws sts get-caller-identity --profile $admin --query '{Account:Account,Arn:Arn}'
# Stop unless the expected administrator and account 294665681966 are confirmed.
aws iam create-role --profile $admin --role-name cloudops-ec2-only-deploy `
  --assume-role-policy-document file://terraform/aws/ec2-only/iam/trust-policy.json `
  --max-session-duration 3600
aws iam create-policy --profile $admin --policy-name CloudOpsEC2OnlyDeploy `
  --policy-document file://terraform/aws/ec2-only/iam/permissions-policy.json
aws iam attach-role-policy --profile $admin --role-name cloudops-ec2-only-deploy `
  --policy-arn arn:aws:iam::294665681966:policy/CloudOpsEC2OnlyDeploy
aws iam create-policy --profile $admin --policy-name CloudOpsAssumeEC2OnlyDeploy `
  --policy-document file://terraform/aws/ec2-only/iam/operator-assume-policy.json
aws iam attach-user-policy --profile $admin --user-name cloudops-dev `
  --policy-arn arn:aws:iam::294665681966:policy/CloudOpsAssumeEC2OnlyDeploy
```

Execute one command at a time; stop on failure or pre-existing names. Verify
the actual identity, trust, attached and inline policies, boundaries and
effective permissions. Simulate wrong image/subnet/group, wrong instance type,
IMDSv1, unencrypted or oversized volume, PassRole, profile association,
launch-template use, state deletion and unrelated state access. Simulations do
not establish all runtime context and restrictions. Test MFA using the approved
authentication workflow without printing credentials. The operator must set up
the `cloudops-ec2-only-deploy` shared profile and its noninteractive credential
process separately, keeping all credential configuration private.

If rollback is separately authorized, detach the new source-user policy first
to stop new assumptions, then detach the role policy; existing STS sessions can
remain usable until expiry. Verify attachments afterward. Do not delete policy
versions, the legacy role, state, artifacts or approval records as cleanup.

```powershell
aws iam detach-user-policy --profile $admin --user-name cloudops-dev `
  --policy-arn arn:aws:iam::294665681966:policy/CloudOpsAssumeEC2OnlyDeploy
aws iam detach-role-policy --profile $admin --role-name cloudops-ec2-only-deploy `
  --policy-arn arn:aws:iam::294665681966:policy/CloudOpsEC2OnlyDeploy
aws iam list-attached-user-policies --profile $admin --user-name cloudops-dev
aws iam list-attached-role-policies --profile $admin --role-name cloudops-ec2-only-deploy
```
