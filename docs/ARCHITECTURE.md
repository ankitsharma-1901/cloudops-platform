
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

GitHub Actions will perform automated tests,
validation, scanning, and image builds.

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

Phase 1 local API and persistence are implemented. A Day 7 plan-only workflow
uses the local Terraform foundations lab; AWS provisioning remains unimplemented.
The `backend/` directory lives inside the `cloudops-platform/` repository
alongside `docs/`.

- `app/main.py` defines the application factory, health and validation routes,
  and create/list/retrieve routes for persistent server requests.
- `app/schemas.py` uses Pydantic v2 models to enforce allowed server settings,
  required strict booleans, and rejection of extra fields.
- `app/models.py` defines the SQLModel `ServerRequest` table with a UUID string
  primary key, six configuration fields, `queued` status, and UTC creation time.
  A database uniqueness constraint covers `(environment, name)`.
- `app/database.py` creates a configurable engine and provides one session per
  API request through a FastAPI dependency. The default database is
  `backend/cloudops.db`; `DATABASE_URL` or an explicit app-factory URL overrides it.
- `tests/conftest.py` supplies isolated temporary SQLite databases.
- `tests/test_resources.py` preserves Day 2 validation coverage;
  `tests/test_server_requests.py` covers persistence and the server request API.
- `ProvisioningJob` in `app/models.py` links local plan jobs to server requests.
  A partial unique SQLite index permits only one `queued` or `planning` job per
  request at a time.
- `app/worker.py` is a separate polling process. It atomically changes a job
  from `queued` to `planning`, validates the saved values again, runs the
  plan-only adapter, and stores either a safe summary or a generic failure.
- `app/terraform_runner.py` copies only the trusted lab files to a unique
  temporary directory, invokes fixed Terraform argument arrays with timeouts,
  and returns only `terraform_data` action counts.
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

The plan endpoint only writes a queued database row. The separate worker
processes it using a copy of the local foundations lab, never AWS-backed
Terraform. Job statuses are `queued`, `planning`, `plan_ready`, and `failed`.
The summary contains no raw Terraform output, saved plan, state, or credentials.

SQLite stores creation timestamps as naive UTC values; the response schema
restores UTC timezone information when serializing them. Startup creates missing
tables, but does not migrate existing tables. A migration tool will be needed
when existing schemas change.

Authentication, authorization, approvals, a general-purpose job-processing
platform, and AWS-backed cloud integrations remain planned.

### Day 4 local frontend

`frontend/` contains a Next.js App Router client with TypeScript and Tailwind CSS.
`app/page.tsx` renders the workspace; `components/create-server-form.tsx` manages
the accessible form, summary, pending state, and result feedback. `lib/api.ts`
defines approved configuration choices and sends JSON directly to FastAPI.
The backend remains the authority for validation and persistence.

The browser defaults to `http://127.0.0.1:8000`; `NEXT_PUBLIC_API_URL` can
override it. CORS only allows localhost and 127.0.0.1 on port 3000, without
credentials. Vitest and Testing Library exercise form behaviour using mocked
fetch responses; Pytest checks allowed and disallowed CORS preflights.

No AWS infrastructure has been deployed.

Architecture will evolve as implementation
and testing reveal new requirements.
