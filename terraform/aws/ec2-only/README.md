# EC2-only development planning

The website can explicitly select `AWS — EC2 Only` for asynchronous planning.
The worker targets this root and its distinct S3 state, verifies account and
network identity, and saves only a policy-checked binary plan. EC2-only deployment
is unavailable even when the global apply switch is enabled. The legacy option
still targets `terraform/aws/dev`; no stack is chosen as a fallback.

The intended S3 key is `cloudops/development/ec2-only/terraform.tfstate`, distinct
from the older root. Backend initialization and any S3 policy changes require
separate review and authorization. This root pins account `294665681966`, region
`ca-central-1`, VPC `vpc-05828cfb0f6ae89e4`, subnet
`subnet-08dfc8d266e48fc91`, and AMI `ami-009d185a5ea80b390`. The AMI and network
must be rechecked before an eventual deployment. One reviewed non-default existing
security group ID is mandatory; the account's current default group is refused.
The Terraform data source checks its VPC and non-default name. The worker also
requires the live security group to be in the expected account and VPC with no
inbound permissions before planning. Configure `ec2_security_group_id` in the
private local manifest on both API and worker, and restart both services using
the same manifest. Leave it empty until the group is reviewed. Future apply
integration must independently recheck the group before execution.

The root describes one private `t3.micro` with an encrypted 8 GiB gp3 root disk
and IMDSv2 required. It creates no network or instance-profile resources; without
an instance profile, SSM Session Manager access is not configured. A private
instance also needs separately reviewed networking for any desired outbound access.

The reviewed deployment role currently authorizes a different AMI. The owner must
review exact image and network permissions, the new state key and lock permissions,
and applicable boundaries or SCPs before any live plan. A subsequent cleanup
requires a separately reviewed and authorized destroy plan, post-cleanup AWS/state
verification, and preservation of the shared state bucket and historical state.

## Proposed IAM changes, not applied

At the last read-only inspection, `CloudOpsTerraformCompute` allowed the old
image, required the old instance profile on the instance resource, and required
CloudOps tags on both the subnet and security group. The candidate changes to
review are:

* Extend the image-specific `ec2:RunInstances` allow to the exact reviewed AMI
  `arn:aws:ec2:ca-central-1::image/ami-009d185a5ea80b390`.
* Add a separate no-profile instance allow retaining the existing account,
  region, `t3.micro`, IMDSv2 and required request-tag restrictions; constrain
  it to requests without an instance profile. Do not widen `iam:PassRole`.
* Scope the network-resource allow to the exact reviewed subnet and, once
  supplied, the exact restricted group. Verify the applicable resource-tag
  conditions against those existing resources rather than removing them blindly.
* Allow the existing bucket's new state object `s3:GetObject`/`s3:PutObject`,
  and its exact `.tflock` object `s3:GetObject`/`s3:PutObject`/`s3:DeleteObject`.
  Verify the bucket-list prefix condition permits the new key. Retain the
  expected-owner and native-lock checks. Do not grant deletion of the state object.

These are review requirements, not a tested policy or authorization to modify IAM.
Evaluate effective permissions, boundaries, SCPs, encryption and bucket policy
after the exact group exists. No real IAM changes were made.
