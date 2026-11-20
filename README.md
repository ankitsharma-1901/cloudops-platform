
# CloudOps — AI-Powered DevOps Platform

A self-service cloud infrastructure management
platform built with DevOps, cloud automation,
Kubernetes, CI/CD, and AI technologies.

## Project Objective

Build a platform where authorized users can:

- Request, create, and manage cloud servers
- Automate infrastructure using Terraform
- Configure servers using Ansible
- Deploy applications using Docker and Kubernetes
- Automate builds and deployments using CI/CD
- Monitor infrastructure health and performance
- Use AI to assist with infrastructure planning
  and troubleshooting

## Planned Technology Stack

**Cloud:** AWS  
**Infrastructure:** Terraform, Ansible  
**Backend:** Python, FastAPI  
**Frontend:** React, Next.js, TypeScript  
**Containers:** Docker, Kubernetes  
**CI/CD:** GitHub Actions, Jenkins, Argo CD  
**Monitoring:** Prometheus, Grafana, Loki  
**AI:** LLM APIs with structured outputs

## Current Status

The Infrastructure page offers a read-only EC2 inventory and a separately gated,
default-disabled EC2 demo. The inventory compares instance
IDs with the isolated EC2-only Terraform state and reports ownership as unknown
if that state cannot be verified. The demo has its own create and terminate
controls and does not use Terraform. Live demo creation and termination have not
been verified; the effective IAM tag permissions remain a blocker. Live inventory
requires the existing authenticated deployment profile and read permissions.

An isolated [EC2-only planning path](terraform/aws/ec2-only/README.md) can be
selected explicitly in the website. A reviewed restricted existing security group
and backend permissions are still needed for live use. EC2-only apply is default-off
and has not been verified against live AWS.

Day 14 operator hardening adds a shared, validated disabled-mode API/worker
launcher, prefix-compatible bucket-owner preflight, sanitized deployment failure
codes and targeted offline recovery. Follow [local startup and recovery](docs/LOCAL-OPERATIONS.md)
before using the older individual-terminal examples. Live provisioning remains
unverified and disabled; no live IAM permissions were changed.

Local MVP development: validated server requests, SQLite persistence, a
Create Server frontend, and local/AWS Terraform plan-only workflows are implemented.

Day 7: A separate local worker can generate a sanitized Terraform plan summary
from the trusted `terraform_data` foundations lab. Apply, destroy, AWS-backed
Terraform integration with the application and AI features remain unavailable
or planned. The standalone AWS exercise below is operated manually.

Day 8: An isolated [AWS EC2 configuration](terraform/aws/dev/README.md)
is available for manual review in `terraform/aws/dev/`. It is separate from the
application's local worker.

Day 9: A separate state-bucket bootstrap, encrypted S3 backend with native
locking, permission proposal and [first-deployment runbook](terraform/aws/dev/DEPLOYMENT.md)
were prepared and then exercised through separate owner approval gates.
The [deployment-role proposal](terraform/aws/dev/deployment-role/README.md)
separates the read-only browser-login identity, deployment role, bootstrap
administrator and EC2 SSM role. Its IAM/profile templates require administrator
review and explicit authorization.

The owner reports that the first approved deployment created all 12 planned
development resources, reached EC2 `running` and SSM `Online`, and produced a
no-drift follow-up plan. The approved teardown then removed all 12 disposable
resources and left the current dev resource state empty. The protected S3
backend, remote state history and bootstrap IAM resources remain intentionally.

Day 10 introduced an explicit **AWS development
plan** mode alongside the original local lab. It uses the fixed
`terraform/aws/dev/` directory, the initialized S3 backend and temporary
`cloudops-deploy` credentials. The worker verifies the configured account,
`ca-central-1` and deployment-role ARN before Terraform runs. It supports
planning only at that stage. Day 14 adds the disabled deployment path below;
destroy, import and arbitrary state-changing commands remain unavailable.

Days 11-12: Completed plans now have revision-bound approval records and
append-only audit events. Auth.js adds OIDC Authorization Code with PKCE through
a Next.js BFF, while FastAPI independently validates API access tokens and
enforces engineer ownership and approver separation. Authentication remains
fail-closed until an identity provider and server role mapping are configured.
See [local authentication setup](docs/AUTHENTICATION.md).

## Local Backend

The backend lives in `cloudops-platform/backend/`.
From `D:\Devops\PythonLearn\cloudops-platform`, run these PowerShell commands:

```powershell
# Install dependencies into the existing virtual environment.
# This setting avoids the optional PyYAML C build on this Python setup.
$env:PYYAML_FORCE_LIBYAML = "0"
& ".\backend\.venv\Scripts\python.exe" -m pip install -r .\backend\requirements.txt

# Run tests.
Push-Location .\backend
& ".\.venv\Scripts\python.exe" -m pytest tests
Pop-Location

# Start the local development server.
& ".\backend\.venv\Scripts\python.exe" -m uvicorn app.main:app --app-dir .\backend --reload
```

Open http://127.0.0.1:8000/docs to try the API:

- `GET /health` returns `{"status":"healthy"}`.
- `POST /api/v1/resources/validate` returns HTTP 200 with
  `{"status":"validated","configuration":{...}}` for valid server settings.
- Invalid requests return HTTP 422 with field-level validation errors.
- `POST /api/v1/resources` saves the same configuration and returns HTTP 202
  with the six submitted fields, a generated UUID `id`, `status: "queued"`,
  and a UTC `created_at` timestamp.
- `GET /api/v1/resources` returns an array of saved requests (or `[]`).
- `GET /api/v1/resources/{id}` returns a saved request, or HTTP 404 if missing.
- `POST /api/v1/resources/{id}/plan` accepts `{"plan_mode":"local"}` or
  `{"plan_mode":"aws_development"}` and returns HTTP 202. Omitting the body
  preserves local mode. Only one AWS plan can be active globally.
- `GET /api/v1/jobs/{job_id}` returns `queued`, `planning`, `plan_ready`, or
  `failed` plus only sanitized action counts and resource addresses.
- Duplicate server names within the same environment return HTTP 409.
  The same name can be used in a different environment.

Example request:

```json
{
  "name": "web-dev-01",
  "environment": "development",
  "region": "ca-central-1",
  "instance_type": "t3.micro",
  "install_nginx": true,
  "install_docker": true
}
```

Names must contain 3-32 lowercase letters, digits, or hyphens, start with a
letter, and end with a letter or digit. Allowed environments are `development`
and `staging`; regions are `ca-central-1` and `us-east-1`; instance types are
`t3.micro` and `t3.small`. Both install flags are required JSON booleans.
Extra fields are rejected. Validation does not create or save resources.

### Local database

The save endpoint commits a request to `backend/cloudops.db`. The path is
independent of the terminal's current folder. Tables are created on application
startup. The database and SQLite journal files are excluded from Git.

To use another database file, set `DATABASE_URL` before starting the server:

```powershell
$env:DATABASE_URL = "sqlite:///D:/Devops/PythonLearn/cloudops-platform/backend/another.db"
```

Tests pass temporary database URLs to `create_app()` and never use the local
development database. Each API request receives a separate database session.
The server request's `queued` status means it was saved. Plan jobs are handled
by a separate local worker. Local mode still runs fixed `init`, `validate`, and
`plan` argument arrays against an isolated copy of
`terraform/labs/01-foundations/`.

AWS mode runs a fixed, noninteractive STS identity check, then fixed `validate`,
`plan`, and `show -json` arguments in `terraform/aws/dev/`. The worker now retains
the exact binary in restricted, ignored `backend/.private/plans/` storage and
records execution metadata privately. APIs return only sanitized summaries,
digests and approved history fields. Raw show JSON is discarded. A
remote plan reads AWS/state and temporarily writes and deletes the native S3
lock object; planning cannot create infrastructure. The new deployment path is
disabled by default; destroy, import, state commands and arbitrary paths remain
unavailable. See [approved provisioning](docs/APPROVED-PROVISIONING.md).

Start the worker in a separate PowerShell terminal from the repository root.
Keep the API server running in its own terminal. This worker stays running and
processes jobs queued later; leave the terminal open and use Ctrl+C to stop it:

```powershell
Push-Location .\backend
& .\.venv\Scripts\python.exe -m app.worker
Pop-Location
```

The API and worker share the repository's absolute `backend/cloudops.db` default,
independently of their working directories. If you override `DATABASE_URL`, set
the same absolute SQLite URL in both terminals. A worker stopped with `--once`
will not process requests submitted after it exits. A queued job remains readable
with HTTP 200 even if no worker is running.

Executable AWS plans additionally require matching private
`CLOUDOPS_AWS_EXPECTED_ACCOUNT_ID` and `CLOUDOPS_AWS_STATE_BUCKET` pins in API and
worker terminals. Keep `CLOUDOPS_ENABLE_AWS_APPLY=false` in both processes.

Unexpected runner errors become redacted failed jobs, and the service continues.
Transient database failures are retried without logging SQL/connection details.
Jobs left in `planning` by a hard process crash are not automatically replayed;
operator inspection is required to avoid overlapping Terraform runs. A supervised
container or OS service with persistent storage and health checks is a future
deployment step, not a prerequisite for this local architecture.

Local plans need no AWS settings. Before an AWS plan, complete the existing
manual backend initialization and warm the MFA-protected role session. Set the
independently verified account ID privately in the worker terminal; do not add
it to `.env`, SQLite or Git:

```powershell
$env:CLOUDOPS_AWS_EXPECTED_ACCOUNT_ID = Read-Host "Verified AWS account ID"
aws sts get-caller-identity --profile cloudops-deploy --region ca-central-1
if ($LASTEXITCODE -ne 0) { throw "Refresh cloudops-deploy MFA before starting the worker." }

Push-Location .\backend
& .\.venv\Scripts\python.exe -m app.worker
Pop-Location
```

Privately confirm the identity is the intended account and an assumed-role
session for `cloudops-terraform-deploy`. AWS mode does not run `terraform init`,
so it cannot migrate or reconfigure the backend. If credentials expire or MFA
would prompt, closed stdin and a timeout fail the job instead of hanging.

For a one-time worker pass, add `--once` after `app.worker`. This exits after
processing at most one queued job. Protected API routes require independently
verified OIDC access tokens and server-assigned roles.

## Local Frontend (Day 4)

`frontend/` is a Next.js App Router application using TypeScript and Tailwind CSS.
Use a current Node.js LTS installation with npm. In a separate PowerShell terminal:

```powershell
Set-Location "D:\Devops\PythonLearn\cloudops-platform\frontend"
npm install
npm run dev
```

For this workspace, a portable Node.js runtime is also available. If `npm`
is not recognized, run this in the same terminal before the commands above:

```powershell
$env:PATH = "D:\Devops\PythonLearn\cloudops-platform\.tools\node-v26.13.1-win-x64;" + $env:PATH
```

Keep FastAPI on port 8000 and the worker running, then open http://localhost:3000.
Sign in and select **New Infrastructure Request** (`/requests/new`).
The form sends the six configuration fields to `POST /api/v1/resources` and
shows the saved UUID and queued status. Choose **Generate local plan** for the
credential-free lab or **Generate AWS development plan** for the fixed real AWS
configuration. AWS mode is enabled only for a development, `ca-central-1`,
`t3.micro` request. It displays sanitized counts and resource addresses. Planning
does not provision infrastructure. Apply is disabled by default and requires the
separate approved deployment workflow; destroy remains unavailable in the app.

Browser API requests use the same-origin Next.js BFF at `/api/cloudops`.
The server-side `CLOUDOPS_API_URL` defaults to `http://127.0.0.1:8000`.
Configure OIDC and the BFF using [the authentication guide](docs/AUTHENTICATION.md).
API access tokens remain on the Next.js server.

FastAPI CORS permits exactly `http://localhost:3000` and
`http://127.0.0.1:3000`, GET/POST, and Authorization/Content-Type headers. Credentials
are not enabled. Other origins and ports need an explicit backend change.
CORS controls browser access; it is not authentication.

Run frontend checks from `frontend/`:

```powershell
npm test
npm run typecheck
npm run build
```

Component tests mock HTTP responses; backend tests use temporary databases.

## Terraform foundations lab

The [local Terraform foundations lab](terraform/labs/01-foundations/README.md)
practices variables, validation, plans, outputs, and local state using only
Terraform's built-in `terraform_data` resource. It creates no AWS or other
external resources.

## Documentation links

- [Project Vision](docs/PROJECT-VISION.md)
- [Architecture](docs/ARCHITECTURE.md)
- [AI Roadmap](docs/AI-ROADMAP.md)
- [Development Progress](docs/PROGRESS.md)

## Development Approach

This project is being developed incrementally,
with documented engineering decisions, tests,
daily progress notes, and regular GitHub commits.

The current target is a **three-month core MVP**. The core scope and milestones
will be refined separately from the longer-term platform vision. I will
personally write more application code, with Codex acting as a tutor and code
reviewer to support understanding, debugging, and maintainable changes.

For the implementation review and recorded verification limits, see the
[November 13, 2026 progress entry](docs/progress/2026-11-13.md).

## Feature branches and pull-request CI

`main` is the stable branch. Use `feature/<short-description>` for development,
then open a pull request targeting `main`. The owner reviews the diff and test
results and manually merges after checks pass. No automatic merge or deployment
is configured. CI does not grant approval to change AWS infrastructure.

The [CI workflow](.github/workflows/ci.yml) runs on every pull request to `main`,
without path filters, so documentation-only PRs receive the same required checks:

| Check | What it runs |
| --- | --- |
| Backend tests | Python 3.13, pinned `requirements.txt`, full pytest suite with temporary databases/mocked Terraform, plus offline deployment-policy checks |
| Frontend checks | Node.js 24, `npm ci`, component tests, production build, then TypeScript checking |
| Terraform (each root) | Terraform 1.17.1, formatting, backend-disabled initialization, schema validation |

Terraform roots are `terraform/labs/01-foundations`, `terraform/aws/dev`, and
`terraform/bootstrap/state`.
Add future root modules to the workflow matrix. AWS validation does not require
`AWS_PROFILE`, `expected_account_id`, credentials or a real plan: `validate`
checks configuration and provider schemas without configuring the provider.
The pipeline downloads dependencies but runs no authenticated Terraform plans,
apply/destroy, AWS commands, or deployments. Its token has only `contents: read`,
checkout does not retain credentials, and no repository secrets or OIDC access
are requested. Backend tests mock Terraform subprocesses; this is not an AWS
integration test. Terraform mock tests remain available separately in the AWS
directory but are not required for CI schema validation.

Local checks from the repository root (stop if any command fails):

```powershell
Push-Location backend
& .\.venv\Scripts\python.exe -m pytest tests
Pop-Location

Push-Location frontend
npm ci
npm test
npm run build
npm run typecheck
Pop-Location

terraform -chdir=terraform/labs/01-foundations fmt -check -recursive
terraform -chdir=terraform/labs/01-foundations init -backend=false -input=false -lockfile=readonly
terraform -chdir=terraform/labs/01-foundations validate
terraform -chdir=terraform/aws/dev fmt -check -recursive
terraform -chdir=terraform/aws/dev init -backend=false -input=false -lockfile=readonly
terraform -chdir=terraform/aws/dev validate
terraform -chdir=terraform/bootstrap/state fmt -check -recursive
terraform -chdir=terraform/bootstrap/state init -backend=false -input=false -lockfile=readonly
terraform -chdir=terraform/bootstrap/state validate
```

For a **new task with a clean working tree**, update `main` and create a branch.
`--ff-only` stops rather than creating an unexpected merge commit:

```powershell
git status --short
git switch main
git pull --ff-only origin main
git switch -c feature/my-change
```

Finish existing uncommitted work on its current branch before using that sequence.
After making a focused change, inspect `git diff` and `git status --short`, run
checks, then manually stage only intended files. Commit/push commands below are
for the owner to run after review; Codex does not execute them without permission:

```powershell
git diff --check
git diff
# Replace with the specific reviewed files; include new files intentionally.
git add <reviewed-file-paths>
git diff --cached --check
git diff --cached
git commit -m "ci: add pull request validation"
git push -u origin HEAD
```

Open a PR on GitHub with base `main`, describing behavior, tests and limitations.
Review the PR's Files changed and Checks tabs, resolve feedback, then use GitHub's
manual **Squash and merge** control when satisfied. New commits must pass checks
again. After merging and with a clean working tree, switch to `main` and run
`git pull --ff-only origin main` before the next feature branch.

The owner should configure a GitHub ruleset requiring PRs and these five checks:
`Backend tests`, `Frontend checks`, `Terraform (terraform/labs/01-foundations)`,
`Terraform (terraform/aws/dev)`, and `Terraform (terraform/bootstrap/state)`.
Also block force pushes/deletion and require
resolved review conversations; require approval when another reviewer is
available. Select check names after their first successful workflow run.
These settings have not been configured or verified by this change.

Git ignores local state/backups, plan files (`*.tfplan`, `*.plan`, `plan.out`,
`tfplan`), `.terraform/`, real environment files, local AWS files and common key
formats. Keep sanitized `.env.example` and `.terraform.lock.hcl` files in Git.
Ignore rules do not remove already tracked files or detect every secret: always
review staged changes. Never paste tokens, credential contents or private plan
output into PRs.

## Security

No secrets or cloud credentials are stored
in this repository.

## Approval workflow foundation

Completed local and AWS plans have content-derived revision identifiers. The
backend can bind a `pending` approval request to one job/revision, record an
`approved`, `rejected`, or `expired` outcome, prevent self-approval, and retain
append-only audit history. Decisions never run Terraform or change AWS.

Approval endpoints are disabled by default because the application does not yet
have complete OIDC settings. Unauthenticated requests receive HTTP 401 and
missing server configuration returns HTTP 503. The browser does not submit
usernames, approver identities, role assignments or API access tokens directly.

The existing form includes **New Request / Reset**. It clears only browser view
state and prevents late polling responses from restoring an old plan. Saved
SQLite requests, audits, backend jobs, Terraform state and AWS resources are
unchanged.

## Persistent request management (Day 13 Phase A)

The portal has separate screens: **Dashboard** (`/`), **New Infrastructure
Request** (`/requests/new`), **Requests / History** (`/requests`), and
**Approvals** (`/approvals`). Navigation and screen access reflect the user's
roles; FastAPI independently enforces all data access and actions.

After sign-in, engineers can open **Requests / History** in the portal to load
their own saved requests across browser sessions and API restarts. Requests are
ordered newest first with creation dates, latest job status, approval state and
available safe action counts. Selecting a request loads its plan-job history,
revision identifiers, sanitized resource actions, approval outcomes and audit
events. Request details have refreshable URLs (`/requests/{id}`). Only the latest
job for that request, successfully completed with an intact matching revision,
can be submitted for approval from history. Newer queued, failed or completed
jobs supersede older plan eligibility; previous decisions remain historical facts.
Legacy safe results without revisions remain readable but cannot be approved.

Approvers use **Pending reviews** and **My decisions**. Historical decisions
are filtered by the verified approver identity and ordered by decision time.
Reviewers can inspect submitted plans; unsubmitted jobs are not exposed to them.
Self-approval remains forbidden by the backend. Reviewers can also decide in
request details after inspecting the submitted plan. The backend rejects stale
or mismatched revisions at decision time. Decisions never execute Terraform.

Pages contain 20 records in the UI (API maximum 50). Use Previous/Next page and
Refresh controls to reload worker progress or decisions. History does not poll
automatically. **New Request / Reset** clears only the form view; saved history
remains. Requests with no jobs show "not started" rather than implying provisioning.
Unowned legacy rows are not assigned to whoever signs in next.

The dashboard uses existing SQLite tables; no database migration, new service,
cloud deployment or sample history was introduced by Day 13. Day 14 adds the
private artifact/deployment tables described below. PostgreSQL, Redis/Celery and
broader resource lifecycle management remain future work.
See [manual dashboard verification](docs/AUTHENTICATION.md#verify-persistent-request-management).

**Sign out** clears Auth.js locally and opens Keycloak's RP-Initiated Logout
flow. Confirm logout at Keycloak to end its SSO session before signing in as a
different user. The return URL is fixed and registered; no tokens are put in
browser JavaScript or logout URLs. See [logout configuration](docs/AUTHENTICATION.md#keycloak-logout).

Plan polling distinguishes expired sessions (401), forbidden jobs (403), missing
jobs (404), temporary API/network failures and invalid response formats. Permanent
errors stop polling with actionable messages. No automatic access-token refresh
is implemented; sign in again after expiry. Old stored summaries without a
revision are serialized safely rather than causing response-validation errors.

## Approval-controlled AWS provisioning (Day 14 Phase A)

Saved executable AWS plans, digest-bound approvals, an explicit owner-only
**Deploy Approved Plan** action, a durable shared-state mutex and asynchronous
deployment history are implemented. **Real AWS apply is disabled by default** by the
server switch in API, worker and execution boundary. The local launcher supports
explicitly confirmed enabled startup as described in [local operations](docs/LOCAL-OPERATIONS.md).
Approval alone never
deploys. Local/legacy, expired, superseded, consumed, destructive or mismatched
plans cannot deploy. Updates, deletes, replacements and unexpected resources
are rejected; execution is limited to create-only changes in the existing sandbox.

Deployment history uses `queued`, `running`, `succeeded`, `failed` and
`needs_operator_review`. Uncertain outcomes retain the mutex and are never
automatically retried. New tables and a nullable approval digest are additive;
back up existing SQLite data before upgrading. No IAM, Terraform source or AWS
resources were changed during implementation. No real apply was tested.

Read [security boundaries, configuration and recovery](docs/APPROVED-PROVISIONING.md)
before any future enablement review. The feature is a controlled foundation for
the long-term platform, not authorization to provision resources now.
