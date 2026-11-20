# EC2-only development planning

The website can explicitly select `AWS — EC2 Only` for asynchronous planning.
The worker targets this root and its distinct S3 state, verifies account and
network identity, and saves only a policy-checked binary plan. EC2-only deployment
requires separate approval, an explicit engineer action and the default-off switch.
The EC2-only runner requires the separate `cloudops-ec2-only-deploy` AWS profile
and assumed role. It rejects the legacy role. Role and policy setup remains a
separately authorized operator task; see [IAM review](iam/README.md).
The legacy option
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
inbound permissions and exactly TCP 443 IPv4 egress to `0.0.0.0/0`, before
planning and applying. The selected subnet must not
automatically assign IPv6 addresses. Configure `ec2_security_group_id` in the
private local manifest on both API and worker, and restart both services using
the same manifest. Leave it empty until the group is reviewed.

The root describes one private `t3.micro` with an encrypted 8 GiB gp3 root disk
and IMDSv2 required. It creates no network or instance-profile resources; without
an instance profile, SSM Session Manager access is not configured. A private
instance also needs separately reviewed networking for any desired outbound access.
The profile is explicitly set to an empty string, but a live plan with the pinned
provider still marked it unknown. The policy rejects unknown profiles and launch
templates, so EC2-only planning remains blocked until absence can be verified.
An isolated local-backend diagnostic with the real pinned provider reproduced the
unknown profile and an empty launch-template unknown marker. Plan configuration
JSON alone is insufficient evidence for an exception: Terraform omits dynamic
block expressions. Any future exception must independently verify the source
embedded in the exact saved plan, its provider lock and configuration provenance,
and repeat that verification before execution.
The local preflight parses HCL and pins the reviewed source file hashes; source
changes require explicit code review and pin updates. This check does not prove
the saved plan's embedded source or provider executable provenance.

Before execution the worker checks the exact binary digest and revision,
backend and configuration identities, network, and prior-state lineage and
serial. Terraform's native S3 lock remains authoritative for concurrent changes.
After apply, it requires exactly one expected managed instance in isolated state
and verifies its AWS identity. Apply or post-apply uncertainty retains the
reservation and requires operator reconciliation. No automatic retry is allowed.
These paths are mocked in tests and have not been verified by a real EC2-only apply.

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
