# Deployment permission proposal

No IAM permissions are granted by these documents. Have an authorized account
administrator review effective permissions, boundaries and SCPs before changes.
Never attach AdministratorAccess or widen a deny failure to `Action: "*"`.
The EC2 instance role remains SSM-only; deployment permissions belong to a
separate human-operated, temporary session/role, never to the EC2 instance or CI.

The concrete [deployment-role design](deployment-role/README.md) includes
administrator-reviewable trust, source-user assume-role permission, separate
network/compute/instance-IAM policy templates, a credential-free CLI profile
example and offline regression tests. This supplies a scoped initial-deployment
proposal; it does not assert that effective permissions have been verified.

## Backend access: complete policy template

`backend-policy.json.example` is an identity policy for the **default workspace**.
Replace `<STATE_BUCKET>` privately. Its three statements allow filtered listing
for backend discovery, read/write of one state key, and read/write/delete of its
exact `.tflock` object. There is no state-object deletion, bucket administration,
version deletion, DynamoDB or KMS permission. SSE-S3 does not need a KMS grant.
`workspace_key_prefix` is fixed in `versions.tf` so backend workspace discovery
uses the permitted prefix. Do not create named workspaces: their state objects
are deliberately outside this policy. Keep dev and bootstrap operator grants
separate. Do not add S3 access to the SSM instance role.

For private, owner-run verification of bucket settings, a bucket administrator
needs these actions on `arn:aws:s3:::<STATE_BUCKET>`:
`s3:ListBucket`, `s3:GetBucketLocation`, `s3:GetBucketVersioning`,
`s3:GetEncryptionConfiguration`, `s3:GetBucketPublicAccessBlock`,
`s3:GetBucketOwnershipControls`, and `s3:GetBucketPolicy`.
Version-history recovery is a separate administrative operation requiring
`s3:ListBucketVersions` and `s3:GetObjectVersion`; these are not backend grants.

## EC2/VPC actions for the current environment

The table below is an explicit **candidate lifecycle action inventory**, not a validated final
identity policy. The action names are checked against AWS's service authorization
reference. Provider 6.73.0 can take different read/update paths depending on the
actual plan and existing state. Only the user's successful plan is reported;
its private JSON and account identity have not been supplied or inspected here.
Do not treat this inventory as a claim that an apply has been authorized or tested.
The concrete templates deliberately omit some future update paths listed below;
do not union the entire inventory into the deployment role.

| Operation group | Exact IAM actions to review |
| --- | --- |
| Provider/refresh reads | `ec2:DescribeVpcs`, `ec2:DescribeVpcAttribute`, `ec2:DescribeSubnets`, `ec2:DescribeInternetGateways`, `ec2:DescribeRouteTables`, `ec2:DescribeSecurityGroups`, `ec2:DescribeSecurityGroupRules`, `ec2:DescribeInstances`, `ec2:DescribeInstanceAttribute`, `ec2:DescribeInstanceCreditSpecifications`, `ec2:DescribeVolumes`, `ec2:DescribeTags`, `ec2:DescribeImages`, `ec2:DescribeInstanceTypes`, `ec2:DescribeNetworkInterfaces` |
| VPC and DNS attributes | `ec2:CreateVpc`, `ec2:ModifyVpcAttribute`, `ec2:DeleteVpc` |
| Subnet attributes | `ec2:CreateSubnet`, `ec2:ModifySubnetAttribute`, `ec2:DeleteSubnet` |
| Internet Gateway | `ec2:CreateInternetGateway`, `ec2:AttachInternetGateway`, `ec2:DetachInternetGateway`, `ec2:DeleteInternetGateway` |
| Routes and association | `ec2:CreateRouteTable`, `ec2:DeleteRouteTable`, `ec2:CreateRoute`, `ec2:DeleteRoute`, `ec2:AssociateRouteTable`, `ec2:DisassociateRouteTable`, `ec2:ReplaceRouteTableAssociation` |
| Security group and HTTPS egress | `ec2:CreateSecurityGroup`, `ec2:DeleteSecurityGroup`, `ec2:AuthorizeSecurityGroupEgress`, `ec2:RevokeSecurityGroupEgress`, `ec2:ModifySecurityGroupRules` |
| Instance and configured attributes | `ec2:RunInstances`, `ec2:TerminateInstances`, `ec2:ModifyInstanceAttribute`, `ec2:ModifyInstanceCreditSpecification`, `ec2:ModifyInstanceMetadataOptions` |
| Resource tags | `ec2:CreateTags`, `ec2:DeleteTags` |
| Public AMI lookup | `ssm:GetParameter` on `arn:aws:ssm:ca-central-1::parameter/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64` |

There is no inbound-rule authorization action in this proposal. Root EBS is
created by `RunInstances` and removed with instance termination; the configuration
has no standalone volume or snapshot resource, so it does not justify standalone
`CreateVolume`, `DeleteVolume`, `CreateSnapshot`, or EIP/NAT permissions.
The root disk uses the account's default EBS key. This inventory assumes an
AWS-managed EBS key; a customer-managed account default needs separate review
of the actual key policy/grants before deployment. It is not covered by the
SSE-S3 backend policy, and no broad KMS permissions are proposed.

Constrain EC2 writes to `ca-central-1` using `aws:RequestedRegion`. Use explicit
resource-type ARNs under `arn:aws:ec2:ca-central-1:<ACCOUNT_ID>:` rather than an
all-region wildcard. Launch authorization includes image ARNs with an empty
account component, plus instance, volume, network-interface, subnet and
security-group ARNs; review each resource type separately. Constrain creation
to the CloudOps/development/ManagedBy tags, and subsequent modifications/deletes
to those tags and known resource IDs wherever supported. Describe calls that
lack resource-level authorization need `Resource: "*"` in their own read-only
statement. Do not put a tag condition on all actions indiscriminately: unsupported
condition keys or missing parent-resource tags can block legitimate operations.

## IAM actions and scopes

| Purpose | Exact IAM actions | Resource/condition |
| --- | --- | --- |
| EC2 role lifecycle/read/tagging | `iam:CreateRole`, `iam:GetRole`, `iam:UpdateAssumeRolePolicy`, `iam:DeleteRole`, `iam:TagRole`, `iam:UntagRole`, `iam:ListRoleTags`, `iam:ListAttachedRolePolicies`, `iam:ListRolePolicies`, `iam:ListInstanceProfilesForRole` | Only `arn:aws:iam::<ACCOUNT_ID>:role/cloudops-dev-ec2-ssm` |
| Managed attachment | `iam:AttachRolePolicy`, `iam:DetachRolePolicy` | Same role; require `iam:PolicyARN = arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore` |
| Instance profile | `iam:CreateInstanceProfile`, `iam:GetInstanceProfile`, `iam:DeleteInstanceProfile`, `iam:AddRoleToInstanceProfile`, `iam:RemoveRoleFromInstanceProfile`, `iam:TagInstanceProfile`, `iam:UntagInstanceProfile`, `iam:ListInstanceProfileTags` | Only `arn:aws:iam::<ACCOUNT_ID>:instance-profile/cloudops-dev-ec2-ssm` |
| Pass EC2 role | `iam:PassRole` | Only the exact role ARN; require `iam:PassedToService = ec2.amazonaws.com`; separate statement |

No IAM user management, access-key creation, inline-policy writes, arbitrary
managed-policy attachment or `iam:*` is justified. The ability to create/change
a role is still privileged: an administrator should constrain its trust policy
and use an approved permission boundary if required by the account. A boundary
would need an explicitly reviewed Terraform change; none is added here.

## Obtain and review the final deployment policy

AWS-native `.tf` files alone are not an input to IAM Policy Autopilot. After
backend bootstrap/migration and **separate approval for the locked plan's S3
writes**, the owner can create a private saved plan and render it locally.
Use encrypted storage with an owner-only ACL. The files can contain sensitive
attributes: never paste them into chat, commit them, or publish them as CI artifacts.

```powershell
# Owner only, after approval for backend lock-object writes.
terraform plan -input=false -lock-timeout=60s -out=deployment.tfplan
if ($LASTEXITCODE -ne 0) { throw "Plan failed." }
$planJson = terraform show -json deployment.tfplan
if ($LASTEXITCODE -ne 0) { throw "Plan JSON rendering failed." }
[System.IO.File]::WriteAllText((Join-Path (Get-Location) "deployment.tfplan.json"), ($planJson -join [Environment]::NewLine), [System.Text.UTF8Encoding]::new($false))
Remove-Variable planJson
uvx iam-policy-autopilot@latest generate-policies `
  "D:\Devops\PythonLearn\cloudops-platform\terraform\aws\dev\deployment.tfplan.json" `
  --region ca-central-1 --account $env:TF_VAR_expected_account_id --pretty
```

This command generates a proposal; it does not upload policies. Its output
contains account/resource identifiers: keep it private. Review read/refresh and
cleanup coverage as well as creation actions; narrow generated wildcards using
the current plan and authorization reference. Combine the separately reviewed
backend policy with the resulting deployment policy only for the deployment
operator. IAM attachment needs separate administrator approval. Real permission
simulation/verification and any changed-plan review are release gates, not
offline test results. The generator was not installed or run in this task
because no real plan JSON was available.

## Bootstrap administrator actions

The bootstrap operator is separate from the deployment operator. Its grants
are scoped to `arn:aws:s3:::<STATE_BUCKET>`; it must not create arbitrary buckets.
The separate configuration needs `s3:CreateBucket`, `s3:PutBucketTagging`,
`s3:GetBucketTagging`, `s3:PutBucketOwnershipControls`,
`s3:GetBucketOwnershipControls`, `s3:PutBucketPublicAccessBlock`,
`s3:GetBucketPublicAccessBlock`, `s3:PutBucketVersioning`,
`s3:GetBucketVersioning`, `s3:PutEncryptionConfiguration`,
`s3:GetEncryptionConfiguration`, `s3:PutBucketPolicy`, `s3:GetBucketPolicy`,
`s3:GetBucketLocation`, and `s3:ListBucket`. Provider refresh of the bucket's legacy
attributes can also invoke reads authorized by `s3:GetBucketAcl`,
`s3:GetBucketCORS`, `s3:GetBucketWebsite`, `s3:GetBucketLogging`,
`s3:GetBucketRequestPayment`, `s3:GetAccelerateConfiguration`,
`s3:GetLifecycleConfiguration`, and `s3:GetReplicationConfiguration`.
These names distinguish IAM actions from API names (for example,
`PutBucketEncryption` uses `s3:PutEncryptionConfiguration`).
Bucket deletion, version deletion and disabling protections are not part of
the bootstrap task. The runtime deployment role gets none of these bucket
administration writes. The bootstrap root's local state needs protected storage
and backups independent of the bucket; do not move it into the dev state key.

## Sources

- [S3 backend permissions and lock-object separation](https://developer.hashicorp.com/terraform/language/backend/s3)
- [AWS service authorization reference](https://docs.aws.amazon.com/service-authorization/latest/reference/)
- [Programmatic EC2 authorization reference](https://servicereference.us-east-1.amazonaws.com/v1/ec2/ec2.json)
- [Programmatic IAM authorization reference](https://servicereference.us-east-1.amazonaws.com/v1/iam/iam.json)
- [Programmatic S3 authorization reference](https://servicereference.us-east-1.amazonaws.com/v1/s3/s3.json)
- [IAM Policy Autopilot](https://github.com/awslabs/iam-policy-autopilot)
