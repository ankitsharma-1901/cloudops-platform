# Dedicated EC2 demo role: administrator procedure

These files are a proposal; do not execute the commands without separate owner
authorization and administrator review. Run from the repository root. Never
attach the old demo-additional-policy.json to the Terraform role. Demo actions
are billable and still require separate website confirmations and approval.

Check the administrator identity, account, and that names do not already exist.
Replace the profile placeholder; stop on any error. Never overwrite existing IAM
objects. Commands use two new managed policies and one new role.

```powershell
$admin = '<APPROVED_ADMIN_PROFILE>'
aws sts get-caller-identity --profile $admin
aws iam get-role --profile $admin --role-name cloudops-ec2-demo-deploy
aws iam get-policy --profile $admin --policy-arn arn:aws:iam::294665681966:policy/CloudOpsEC2DemoExecution
aws iam get-policy --profile $admin --policy-arn arn:aws:iam::294665681966:policy/CloudOpsAssumeEC2Demo
```

Only proceed if STS identifies the authorized administrator in account
294665681966 and all three lookups return NoSuchEntity (not AccessDenied).
Run each command separately and stop on failure:

```powershell
aws iam create-role --profile $admin --role-name cloudops-ec2-demo-deploy --assume-role-policy-document file://terraform/aws/ec2-only/iam/demo-trust-policy.json --max-session-duration 3600
aws iam create-policy --profile $admin --policy-name CloudOpsEC2DemoExecution --policy-document file://terraform/aws/ec2-only/iam/demo-execution-policy.json
aws iam attach-role-policy --profile $admin --role-name cloudops-ec2-demo-deploy --policy-arn arn:aws:iam::294665681966:policy/CloudOpsEC2DemoExecution
aws iam create-policy --profile $admin --policy-name CloudOpsAssumeEC2Demo --policy-document file://terraform/aws/ec2-only/iam/demo-assume-role-policy.json
aws iam attach-user-policy --profile $admin --user-name cloudops-dev --policy-arn arn:aws:iam::294665681966:policy/CloudOpsAssumeEC2Demo
```

Verify the role trust, attached policies, inline policies and boundary; verify the
source user's attachment. Compare returned documents to the reviewed files.

```powershell
aws iam get-role --profile $admin --role-name cloudops-ec2-demo-deploy
aws iam list-attached-role-policies --profile $admin --role-name cloudops-ec2-demo-deploy
aws iam list-role-policies --profile $admin --role-name cloudops-ec2-demo-deploy
aws iam get-policy-version --profile $admin --policy-arn arn:aws:iam::294665681966:policy/CloudOpsEC2DemoExecution --version-id v1
aws iam list-attached-user-policies --profile $admin --user-name cloudops-dev
aws iam get-policy-version --profile $admin --policy-arn arn:aws:iam::294665681966:policy/CloudOpsAssumeEC2Demo --version-id v1
```

Privately merge `demo-aws-config.ini.example` into the AWS config using the
reviewed MFA device ARN, without storing codes. Warm the MFA session using the
approved interactive flow, then verify this command returns account 294665681966
and an `assumed-role/cloudops-ec2-demo-deploy/` ARN:

```powershell
aws sts get-caller-identity --profile cloudops-ec2-demo-deploy --region ca-central-1
```

Repeat the full resource-by-resource simulation against the created principal;
check organization, boundary, session, network and encryption restrictions.
The simulator cannot prove service-supplied request context. In particular, a
missing `ec2:AssociatePublicIpAddress` key on the network interface fails the
allow; confirm actual authorization context during separately approved use.
IAM permits termination of any resource bearing the demo tags, while the app
checks the saved exact ID, token and configuration. Tags alone do not prove
ownership. IAM does not enforce a single instance; the application reservation
is local to its database.

For separately authorized rollback, detach the source-user policy first and
then the role policy. Existing STS sessions may remain valid until expiry;
removing the role policy changes role permissions, but do not assume immediate
propagation. Verify attachments. Keep state and audit records.

```powershell
aws iam detach-user-policy --profile $admin --user-name cloudops-dev --policy-arn arn:aws:iam::294665681966:policy/CloudOpsAssumeEC2Demo
aws iam detach-role-policy --profile $admin --role-name cloudops-ec2-demo-deploy --policy-arn arn:aws:iam::294665681966:policy/CloudOpsEC2DemoExecution
aws iam list-attached-user-policies --profile $admin --user-name cloudops-dev
aws iam list-attached-role-policies --profile $admin --role-name cloudops-ec2-demo-deploy
```
