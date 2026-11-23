# Phase 0: feature inventory and configuration boundaries

This is the Phase 0 snapshot. Phase 1A subsequently added a reviewed custom name;
see [the Phase 1A design](PHASE-1A-DESIGN.md) for current behavior.

This inventory distinguishes the owner's reported live test from local automated
tests. It does not grant permission to change AWS resources.

| Feature | Classification | Decision |
| --- | --- | --- |
| Direct EC2 demo: create, display, terminate | Owner reports one verified live lifecycle; dedicated role and local tests exist | Keep; direct AWS API, separately gated |
| Demo completion, history and next lifecycle | Implemented and tested locally; second live lifecycle not verified here | Keep; preserve reservation, ownership and audit |
| Keycloak/OIDC, roles and same-origin proxy | Implemented; website authentication used during the reported live workflow | Keep; an expired token requires sign-in again |
| EC2 inventory and Terraform-state ownership comparison | Implemented; AWS and state availability can be partial | Keep read-only; unknown ownership is not proof of ownership |
| Requests, local lab and AWS plans, history and approvals | Implemented and covered by local tests; not equivalent to a live EC2 demo | Keep; group request history under Activity and label planning |
| EC2-only Terraform provisioning | Experimental/incomplete: saved plan policy rejects provider-computed unknowns; live apply unverified | Keep code and validator; present as unfinished |
| Terraform dev and bootstrap roots | Historical owner-reported dev lifecycle and separate bootstrap; not a demo replacement | Keep sources, locks, IAM documents and safety tests |
| Workers, recovery, launcher and private flags | Implemented; local operation depends on the operator environment | Keep; apply and demo are independently gated |
| Prior additive demo IAM proposal | Superseded by dedicated demo-role documents, retained as review history | Hide from the user-facing flow; do not attach it |
| Shared frontend components | All nine have import references in the current tree | Keep; no component deletion established as safe |

No repository files were removed in this cleanup. Existing uncommitted work is
preserved. Database audit events, Terraform state and IAM are outside cleanup.

## Future EC2 configuration schema

This is a design inventory, not an implemented API or approved choice list.
All demo values remain fixed and backend-controlled. The confirmation text is
user-entered, but no EC2 configuration field is user-editable today.

| Field | Current direct demo | Future work before making editable |
| --- | --- | --- |
| Instance name | Fixed `cloudops-ec2-demo` tag | Validate length and allowed characters; define ownership and collision rules |
| AWS region | Fixed `ca-central-1` | Review regional IAM, AMIs, network, pricing and identity pins |
| Operating system / AMI | Fixed AL2023 AMI `ami-009d185a5ea80b390` | Maintain approved regional AMI catalog and ownership/architecture verification |
| Instance type | Fixed `t3.micro` | Approve type catalog, policy limits, quotas and cost implications |
| Root storage | Fixed encrypted 8 GiB gp3 | Validate size/type limits, encryption and IAM/KMS permissions |
| VPC, subnet and security group | Fixed reviewed network; no public IPv4 | Add owned-network catalog and validate route, group, IPv6 and public-IP controls |
| Optional software | None installed by the demo | Design a separately authorized, validated configuration mechanism; no arbitrary commands |
| Tags and ownership | Fixed project/environment/manager tags plus operation token | Define protected tags, owner binding and audit compatibility |
| Approval | Explicit create and exact-ID termination confirmations | Define approval requirements for new capabilities without implying Terraform approval applies to direct API calls |
| Billing | Instance and associated AWS charges apply | Present verified cost information and limits before offering billable options |

Terraform request form options are planning inputs, not supported demo choices.
Changing the direct demo catalog alone cannot expand permissions: backend
validation, IAM, network controls, user review, tests and documentation must
agree before any new value is exposed.
