
# CloudOps — System Architecture

## 1. Project Overview

CloudOps is an AI-powered self-service infrastructure
management platform.

Users will be able to request, provision, configure,
monitor, and manage AWS infrastructure through a web
dashboard.

AI will assist with planning, troubleshooting,
cost analysis, and operational recommendations.

## 2. Technology Stack

| Component | Technology |
|---|---|
| Frontend | Next.js, React, TypeScript |
| Backend | Python, FastAPI |
| Database | PostgreSQL |
| Background Jobs | Redis, Celery |
| Cloud | AWS |
| Infrastructure as Code | Terraform |
| Configuration Management | Ansible |
| Containerization | Docker |
| Orchestration | Kubernetes |
| CI | GitHub Actions |
| Enterprise Pipeline Demo | Jenkins |
| GitOps | Argo CD |
| Monitoring | Prometheus, Grafana |
| Logging | Loki |
| AI Integration | LLM API with structured outputs |

Note: SQLite will be used for the first local MVP
before moving to PostgreSQL.

## 3. High-Level Architecture

```text
             USER
               |
        Next.js Dashboard
               |
          FastAPI Backend
               |
       Authentication / RBAC
               |
       Request Validation
               |
        Approval Workflow
               |
        PostgreSQL Database
               |
        Redis Job Queue
               |
         Celery Workers
          /     |      \
         /      |       \
    Terraform Ansible  AWS SDK
         \      |       /
              AWS
               |
       EC2 / VPC / ALB / EKS

Additional components:

AI Assistant -> FastAPI -> Validated Suggestions

GitHub -> GitHub Actions -> Container Registry
                                    |
                                Argo CD
                                    |
                                Kubernetes

Prometheus + Grafana + Loki
         |
 Infrastructure and App Monitoring
```

## 4. Core Components

### Frontend

The frontend will provide:
- Infrastructure request forms
- Server management dashboard
- Job status and history
- Monitoring views
- AI assistant interface

### Backend

FastAPI will:
- Validate incoming requests
- Authenticate and authorize users
- Record infrastructure requests
- Track provisioning jobs
- Enforce security and approval policies

### Terraform

Terraform will manage the desired state of
AWS infrastructure, including:
- EC2 instances
- VPCs and subnets
- Security groups
- Load balancers
- Kubernetes infrastructure

Terraform runs will use controlled templates,
isolated remote state, locking, and approvals.

#### Day 7 local plan-only adapter

The current local MVP uses only the trusted `terraform/labs/01-foundations/`
configuration and its built-in `terraform_data` resource. FastAPI queues a
`ProvisioningJob` in SQLite; a separate polling worker atomically claims jobs
and runs fixed `init`, `validate`, and `plan` commands outside the request
handler. Each job gets a temporary working directory containing only the
lab's three `.tf` files, its own local Terraform data directory, and validated
request values passed as `TF_VAR_*` environment variables. This isolates job
state and avoids concurrent Terraform operations sharing a working directory.

Only the resource address and plan action counts are stored and returned.
Terraform's raw output and working directory are not exposed through the API.
Apply, destroy, AWS providers, cloud credentials, and infrastructure changes
are not part of this adapter. The worker must be started separately with
`python -m app.worker`.

#### Isolated Day 8/9 AWS configuration

`terraform/aws/dev/` defines a separate manually operated development environment
in `ca-central-1`: one VPC/public subnet, Internet Gateway and routing, an
outbound-HTTPS-only security group with no inbound access, and one AL2023
`t3.micro` with encrypted gp3 storage and IMDSv2. An EC2 instance profile grants
only `AmazonSSMManagedInstanceCore`. A public IPv4 provides the intended SSM
network path without NAT. Authentication uses `AWS_PROFILE`; a required account
input feeds the provider's `allowed_account_ids` guard. Day 9 configures an
encrypted S3 backend with native `.tflock` locking and private account/bucket
settings.
`terraform/bootstrap/state/` independently prepares the versioned, private,
SSE-S3-encrypted state bucket with TLS enforcement and deletion safeguards.
Its local bootstrap state requires separate protected backups and outlives dev.
This configuration is not connected to the API or worker. On November 15, 2026,
the owner used the approved workflow to create the 12 planned resources, verify
EC2 `running`, SSM `Online` and a no-drift plan, and then destroy all 12 dev
resources. The current dev resource state is empty. The retained private S3
backend, state object and bootstrap IAM resources have an independent lifecycle.
The human deployment role `cloudops-terraform-deploy` is independently
administrator-managed. The existing `cloudops-iam` browser-login profile supplies
the source identity; the proposed `cloudops-deploy` credential-process profile
supplies MFA-protected role credentials to both backend and provider. Review
templates restrict deployment grants to tagged regional dev resources, the exact
SSM role/profile and state/lock keys. They do not grant bucket administration or
deployment-role self-management. MFA-protected role authentication succeeded
for the first deployment. These live results are owner-verified; this
documentation update made no authenticated AWS calls.

#### Day 10 AWS plan-only application adapter

The existing FastAPI job API and polling worker support two explicit targets:
`local` preserves the foundations-lab workflow, while `aws_development` plans
only the fixed `terraform/aws/dev/` root. The browser cannot submit a Terraform
path or command.

AWS mode inherits no static AWS credential variables. It selects the fixed
`cloudops-deploy` profile and `ca-central-1`, then a noninteractive STS call must
return the operator-configured account and an assumed-role ARN for
`cloudops-terraform-deploy`. Missing, expired or MFA-blocked credentials fail
within a short timeout with a safe categorized message.

The backend must already be initialized manually. The worker never runs AWS
`terraform init`, migrates state or reconfigures the backend. It uses fixed
`validate`, `plan` and `show -json` argument lists, the approved working
directory, closed stdin and timeouts. The binary plan exists only in a private
temporary directory. Only resource addresses, action lists and counts enter
SQLite or the API; raw plan JSON, attributes, diagnostics, state and credentials
are discarded.

A partial unique SQLite index allows only one queued/planning AWS job across all
requests. The S3 lock protects against external writers. A plan temporarily
creates and removes `.tflock`, but no apply, destroy, import or state mutation
route exists. Website authentication remains future work, so AWS mode is for
the owner's local workstation only.

#### Day 11 plan approval and audit boundary

Every completed Terraform plan now receives a SHA-256 revision. AWS revisions
are derived from the complete private `terraform show -json` output before that
output is discarded; local revisions cover the validated request and plan
output. SQLite stores the digest and sanitized summary, never the raw plan.
An approval references both the provisioning job and its revision. A new plan
job therefore requires a new approval, even if its visible counts look similar.

Approval requests use `pending`, `approved`, `rejected`, and `expired` states.
Creation, decisions, and expiry append audit events in the same database
transaction as the state change. Conditional updates allow only one transition
from `pending`, including when two approvers race. SQLite triggers reject audit
event updates and deletes. The API rejects self-approval and repeated decisions.

All approval routes depend on a trusted server-side `AuthenticatedPrincipal`.
No request or decision payload contains a username. Because CloudOps has no
authentication provider yet, the production dependency returns HTTP 503 and
the approval workflow is disabled by default. Tests replace that dependency
with authenticated principals. A future OIDC/session integration must validate
the issuer, audience, signature, expiry and server-side roles before creating
the principal; browser-supplied identity headers must never satisfy it.

Approval records are review evidence only. There is no execution token and no
apply, destroy, import, state or arbitrary-command endpoint.

#### Day 12 OIDC, RBAC and browser BFF

Auth.js implements OpenID Connect Authorization Code with PKCE, state and nonce
checks in the Next.js server. The browser receives an encrypted, HttpOnly
session cookie; its session view contains the stable subject and UI roles but no
access token. A same-origin Next.js route acts as the BFF, reads the access token
server-side and forwards it only in the FastAPI `Authorization` header.

FastAPI independently verifies the API access token with PyJWT and the trusted
JWKS. It requires a configured issuer, `cloudops-api` audience, expiry, issued-at
time, subject, access-token header type and an explicitly configured asymmetric
algorithm. The API rejects an ID token because its audience is the web client,
not the API. Missing configuration fails closed; missing/invalid authentication
returns 401 and valid identities without permission receive 403.

Both servers map the verified `issuer|subject` through the server-only
`CLOUDOPS_ROLE_ASSIGNMENTS` setting. Browser fields and token role claims do not
grant permissions. Engineers create and plan only their own requests and may
request approval. Approvers see the review queue and decide other users'
requests. Administrator alone grants no request, plan, approval or Terraform
permission. Rows created before ownership tracking have no owner and remain
inaccessible until a separately reviewed migration policy exists.

The reset control is entirely client-side. It aborts outstanding browser
requests, invalidates the current view generation and removes request, plan and
approval state from React. The polling effect also aborts during cleanup, so a
late response cannot repopulate the reset screen. Resetting never deletes
SQLite history, audit events, state or cloud resources, and it does not cancel a
job already accepted by the backend.

### Ansible

Ansible will configure provisioned VMs:
- Install Nginx
- Install Docker
- Configure applications
- Apply approved system configurations

### Kubernetes

Kubernetes will host containerized applications
and support scaling, rolling updates,
health checks, and recovery demonstrations.

### CI/CD

`.github/workflows/ci.yml` defines pull-request checks targeting `main`:
backend pytest, frontend component tests/build/type checking, and credential-free
Terraform formatting and validation for the local lab, isolated AWS root and
independent state bootstrap root.
The workflow has read-only repository permissions and no AWS authentication,
authenticated plans, infrastructure changes, image publishing or deployment.
Its first GitHub-hosted run and branch-protection setup remain to be verified.
Development uses `feature/*` branches and owner-reviewed manual PR merges.
Security scanning and container image builds remain planned.

Argo CD will reconcile Kubernetes deployments
with version-controlled configuration.

Jenkins will demonstrate a separate enterprise
automation workflow.

### Observability

Prometheus will collect metrics.
Grafana will display dashboards.
Loki will centralize application logs.

## 5. AI Integration

The AI assistant will interpret natural-language
requests and produce structured suggestions.

All AI-generated configurations must pass
backend validation and policy checks.

AI must never directly execute arbitrary
Terraform, Ansible, or shell commands.

Infrastructure changes require authorization
and an explicit approval process.

## 6. Security Principles

- No hardcoded credentials
- Least-privilege AWS IAM roles
- Protected infrastructure state
- Approved regions and resource types
- Human approval for infrastructure changes
- Audit logs for significant operations
- Separate control-plane infrastructure
- No anonymous access to provisioning APIs

## 7. Development Approach

Phase 1: Local API and validated requests
Phase 2: Database and simulated job processing
Phase 3: Terraform and Ansible integration
Phase 4: Docker, Kubernetes, and CI/CD
Phase 5: Monitoring and reliability
Phase 6: AI integration and advanced features
Phase 7: Security hardening and portfolio demo

## 8. Current Status

Phase 1 local API and persistence are implemented. The Day 7 local plan-only
workflow remains available, and Day 10 adds owner-operated real AWS planning.
AWS provisioning through the application remains unimplemented.
The `backend/` directory lives inside the `cloudops-platform/` repository
alongside `docs/`.

- `app/main.py` defines the application factory, health and validation routes,
  and create/list/retrieve routes for persistent server requests.
- `app/schemas.py` uses Pydantic v2 models to enforce allowed server settings,
  required strict booleans, and rejection of extra fields.
- `app/models.py` defines `ServerRequest`, `ProvisioningJob`, `ApprovalRequest`,
  and append-only `ApprovalAuditEvent` tables. Server requests use a UUID string
  primary key, six configuration fields, `queued` status, and UTC creation time.
  A database uniqueness constraint covers `(environment, name)`.
- `app/database.py` creates a configurable engine and provides one session per
  API request through a FastAPI dependency. The default database is
  `backend/cloudops.db`; `DATABASE_URL` or an explicit app-factory URL overrides it.
- `tests/conftest.py` supplies isolated temporary SQLite databases.
- `tests/test_resources.py` preserves Day 2 validation coverage;
  `tests/test_server_requests.py` covers persistence and the server request API.
- `ProvisioningJob` in `app/models.py` links fixed-target plan jobs to server
  requests. Partial unique indexes permit one active job per request and only
  one active AWS plan globally.
- `app/worker.py` is a separate polling process. It atomically changes a job
  from `queued` to `planning`, validates the saved values again, runs the
  plan-only adapter, and stores either a safe summary or a generic failure.
- `app/terraform_runner.py` preserves the isolated local lab and adds the fixed
  AWS identity preflight, command allowlist, temporary plan and sanitized
  resource-action summary.
- `POST /api/v1/resources/{id}/plan` enqueues or reuses an active job.
  `GET /api/v1/jobs/{job_id}` exposes its state and sanitized summary.
- The frontend polls the job endpoint and displays the summary. Apply and
  Destroy remain disabled.

The validation endpoint returns the accepted configuration with status
`validated`. It does not persist requests or trigger infrastructure operations.
The create endpoint validates input using the same `CreateServerRequest` schema,
commits a new row, and returns HTTP 202 with the saved request. Duplicate names
within an environment produce HTTP 409 after a database constraint violation
and transaction rollback. GET endpoints return saved rows or HTTP 404.

The plan endpoint only writes a queued database row. The worker processes the
selected fixed target. Job statuses are `queued`, `planning`, `plan_ready`, and
`failed`. The summary contains no raw output, saved plan, state, attribute
values, diagnostics, or credentials.

SQLite stores creation timestamps as naive UTC values; the response schema
restores UTC timezone information when serializing them. Startup creates missing
tables and applies focused additive migrations for the plan mode and plan
revision. A general migration tool is needed before more schema changes.

OIDC authentication and RBAC are implemented and fail closed until their
identity-provider settings are supplied. A general-purpose job-processing
platform and AWS deployment execution remain planned.

### Day 4 local frontend

`frontend/` contains a Next.js App Router client with TypeScript and Tailwind CSS.
`app/page.tsx` renders the workspace; `components/create-server-form.tsx` manages
the accessible form, summary, reset state, pending state, and result feedback.
`lib/api.ts` defines approved configuration choices and sends same-origin JSON
to the Next.js BFF. The BFF attaches the server-held access token, and FastAPI
remains the authority for token validation, authorization and persistence.

The browser defaults to `http://127.0.0.1:8000`; `NEXT_PUBLIC_API_URL` can
override it. CORS only allows localhost and 127.0.0.1 on port 3000, without
credentials. Vitest and Testing Library exercise form behaviour using mocked
fetch responses; Pytest checks allowed and disallowed CORS preflights.

The standalone AWS exercise completed its first approved deployment and teardown on
November 15, 2026, as described above. The application itself does not provision AWS resources.

Architecture will evolve as implementation
and testing reveal new requirements.
