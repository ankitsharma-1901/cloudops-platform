# Local service startup and operator recovery

AWS apply is disabled by default. These procedures do not authorize AWS apply,
destroy, IAM changes or other live operations. No live provisioning has been
verified by the automated checks.

## One service configuration

Back up `D:\Devops\PythonLearn\cloudops-platform\backend\cloudops.db` before
starting updated services. Stop API, frontend and all workers, confirm no Terraform
or provider processes remain, and copy the DB plus any `-journal`, `-wal`, `-shm`
siblings and `backend/.private` into a restricted, encrypted backup location.
Keep DB and artifacts together. Do not upload backups to Git.

From the repository root, make a private manifest once:

```powershell
Copy-Item .\config\local.example.json .\.cloudops.local.json
```

Edit the private file with the independently verified account ID, existing state
bucket and actual issuer/subject role mapping. Never add credentials or secrets.
The example deliberately has invalid target placeholders. The private filename
is gitignored. Both services read the same file, pinned to:

- Existing `backend/cloudops.db`; missing/unrelated schema fails instead of creating a DB.
- Private `backend/.private/plans`; no public/arbitrary artifact directory.
- Development, `ca-central-1`, `cloudops-deploy`, initialized S3 backend with
  `cloudops/development/terraform.tfstate`, default workspace and account pin.
- OIDC issuer, API audience, JWKS location, RS256/JWT checks and role assignments.
- `enable_aws_apply: false` by default; enabled mode also pins account
  `294665681966` and bucket `cloudops-tfstate-ca1`.

Use fresh PowerShell terminals. Conflicting inherited settings fail closed;
correct them instead of bypassing validation. The launcher validates configuration
and backend fingerprints locally before opening the writable database engine or
starting either service; SQLite schema verification uses a read-only connection.
It invokes no AWS CLI. API and worker take separate operating-system locks and
share durable agreement metadata in `backend/.private/service-agreement`. A
second service starts only if its complete service and backend/configuration
fingerprints match the running peer; a duplicate service is rejected. Do not
delete or replace the agreement directory or its lock files while either service
is running. Both services must use this launcher on the same host and checkout;
legacy entry points and separate hosts/databases do not participate in this guard.
Use the same OS identity: artifact Windows ACLs are private to that identity.
Direct legacy entry points remain compatible but are not the approved Day 14
startup procedure. `CLOUDOPS_LOCAL_CONFIG` also enforces pins on engine creation
and each AWS execution-identity check; changing the file during operation fails
closed instead of silently changing targets.

API terminal (starts additive migration; start this first, after backup):

```powershell
Set-Location D:\Devops\PythonLearn\cloudops-platform\backend
& .\.venv\Scripts\python.exe -m app.local_start --config ..\.cloudops.local.json api
```

Worker terminal (after API initialization completes):

```powershell
Set-Location D:\Devops\PythonLearn\cloudops-platform\backend
& .\.venv\Scripts\python.exe -m app.local_start --config ..\.cloudops.local.json worker
```

### Explicitly authorized enabled startup

Only after a separately authorized deployment and live preflight, stop both
services and verify that neither service nor a Terraform/provider child remains.
Set `enable_aws_apply` to `true` in the private manifest. It must contain the
fixed account and bucket above, `ca-central-1`, the documented state key, and
the `cloudops-deploy` profile. In separate backend terminals start:

```powershell
& .\.venv\Scripts\python.exe -m app.local_start --config ..\.cloudops.local.json --confirm-aws-apply api
& .\.venv\Scripts\python.exe -m app.local_start --config ..\.cloudops.local.json --confirm-aws-apply worker
```

The flag is required independently for each enabled process and is rejected
in disabled mode. Approval alone never starts deployment: the owning engineer
must still explicitly select Deploy Approved Plan. Confirmation of startup is
not approval of an AWS change. Confirm the plan and approval have not expired;
an expired plan requires a fresh plan and separate approval. An enabled worker
may process already queued work, so inspect the queue and state reservation first.

To return to disabled mode, stop both services and confirm they and any Terraform
children have exited, change the private manifest to `false`, then use the
disabled commands above without the confirmation flag. OS locks are released
on process exit, including a crash. A failed or interrupted deployment still
requires the recovery procedure below; stopping services never clears its mutex,
retries it, or changes the approval. This launcher agreement does not verify
live AWS credentials, S3 locks, remote-state freshness, or other hosts.

The worker continuously polls; `--once` remains available on the legacy worker
entry point for isolated diagnostics. Before starting a worker, inspect existing
queued jobs. Apply disabled does not prohibit AWS plan/read commands or native
S3 planning locks. For strictly offline verification use local-lab jobs only.

Frontend: retain existing private Auth.js/Keycloak secrets in the terminal or
`frontend/.env.local` (never copy example secret placeholders). With those
configured, synchronize the non-secret role mapping from the same manifest:

```powershell
Set-Location D:\Devops\PythonLearn\cloudops-platform
$settings = Get-Content .\.cloudops.local.json -Raw | ConvertFrom-Json
$env:CLOUDOPS_ROLE_ASSIGNMENTS = $settings.roles | ConvertTo-Json -Compress
$env:AUTH_ISSUER = $settings.oidc_issuer
$env:AUTH_URL = 'http://localhost:3000'
$env:AUTH_CLIENT_ID = 'cloudops-web'
$env:CLOUDOPS_API_URL = 'http://127.0.0.1:8000'
$nodeTools = (Resolve-Path .\.tools\node-v26.13.1-win-x64).Path
$env:Path = "$nodeTools;$env:Path"
Set-Location .\frontend
& "$nodeTools\npm.cmd" run dev
```

Keep the existing Keycloak instance available. API and worker need no OIDC client
secret or AWS access keys in the manifest. The worker retains the existing
restricted shared-config/MFA credential chain. Custom AWS configuration-file
environment overrides and Terraform CLI argument overrides are not forwarded.

## Backend preflight and IAM boundary

Preflight now uses ListObjectsV2 with the exact state-key prefix, one result,
pagination disabled and expected bucket owner. It selects only KeyCount and
does not expose returned object metadata. The original prefix-scoped ListBucket
statement already permits this request. Permission delta: **none**; no policy
template or live IAM policy changed. Unrelated buckets/objects and unprefixed
listing receive no new grant. An empty result does not prove state exists;
Terraform's initialized backend, state lineage/serial checks and native lock are
still authoritative. STS exact account/role and provider account guards remain.
The [AWS ListObjectsV2 API reference](https://docs.aws.amazon.com/AmazonS3/latest/API/API_ListObjectsV2.html)
documents ListBucket authorization, prefix selection and expected-owner rejection.

Offline tests evaluate this template's Allow/StringLike subset and the fixed
CLI arguments; they are not a full IAM simulator. Actual attached policies,
bucket policy/SCP/boundary denies, bucket owner, role chain, credentials and AMI
permissions require a separately authorized live read-only review. Do not expand
permissions merely to silence a denial.

## Inspect, mark, then reconcile

Stop API, all workers and manual Terraform; independently confirm no Terraform
or provider process remains. Back up first. Inspection does not migrate schema:
initialize the updated API once beforehand if needed, then stop it.

```powershell
# backend/; all commands below run no AWS/Terraform commands.
& .\.venv\Scripts\python.exe -m app.local_start --config ..\.cloudops.local.json recovery inspect
```

Recovery validates the shared manifest but does not require healthy Terraform
backend metadata, since it executes no cloud commands. Inspection returns only
IDs, statuses, phase/reason, recovery evidence references and the current reservation,
bounded to 100 records per category. It includes a failed reservation owner.
Confirm the exact ID and operation; mismatched/missing reservations are refused
by recovery rather than guessed or replaced.

Mark an interrupted deployment (replace ID/evidence reference):

```powershell
& .\.venv\Scripts\python.exe -m app.local_start --config ..\.cloudops.local.json recovery mark `
  --kind deployment --id '<DEPLOYMENT_ID>' --confirm-workers-stopped `
  --confirm-terraform-stopped --evidence 'INC-14'
```

No apply marker means `failed/interrupted_before_apply`. A durable apply marker
means `needs_operator_review`, even if the process died immediately before the
subprocess. Both retain the AWS reservation and consume the artifact if needed.
No command resets consumption or queues a retry. Older records with ambiguous
markers require conservative manual investigation; do not infer success.

For a stranded `planning` job, use the same command with `--kind plan` and its
job ID. It becomes failed; an AWS reservation is retained. Local-lab planning
jobs have no AWS reservation and can be marked without creating one.

Before **any** release, record evidence outside CloudOps sensitive-data fields:

1. Establish that all execution processes stopped, including provider children
   after timeout. Identify exact account, region, backend key, workspace, operation
   timestamps and approved digest. Do not run another apply/plan concurrently.
2. Under separate authorization for live read-only investigation, compare remote
   state lineage/serial/version and recorded resource IDs with actual EC2, VPC,
   networking and IAM resources. Check identifiers, CloudOps tags, relationships,
   partial creations and relevant CloudTrail events around the operation window.
3. Determine whether a Terraform lock belongs to a live operation. This tool
   never force-unlocks S3, edits state, imports resources or removes resources.
   Such corrective operations require a separately reviewed, specific plan.
4. Document partial creation, resources missing from state, unexpected state
   changes and remaining uncertainty. If unresolved, retain the reservation.
   A stopped process or absent state entry alone does not prove no AWS changes.
5. Only when state/resources are reconciled and any Terraform lock is understood,
   attest the result with a non-secret incident/evidence reference.

Explicit release (same exact ID/kind as marked):

```powershell
& .\.venv\Scripts\python.exe -m app.local_start --config ..\.cloudops.local.json recovery release `
  --kind deployment --id '<DEPLOYMENT_ID>' --confirm-workers-stopped `
  --confirm-terraform-stopped --confirm-state-and-resources-reconciled --evidence 'INC-14'
```

This atomically releases only that target's application reservation and records
the OS operator/evidence reference in append-only recovery audit. A reconciled
deployment remains failed with `reconciled_no_retry`; its artifact stays consumed.
Generate a fresh plan and obtain fresh separate approval for any future attempt.
For an AWS planning job use `--kind plan`. Release of running/queued operations,
other owners, or local-lab locks is refused. The command relies on operator
attestation, not automatic proof of AWS reconciliation; local DB/host access is
an administrative security boundary. No web recovery API is exposed.

## Safe diagnostics and disabled-mode checks

Deployment responses/UI persist phase and allowlisted reason codes:
`execution_disabled`, `credential_preflight_failed`, `backend_preflight_denied`,
`configuration_validation_failed`, `plan_integrity_failed`, `preflight_failed`,
`apply_started`, `needs_operator_review`, `interrupted_before_apply`, and
`reconciled_no_retry`. Generic unknown failures do not store exception text.
An apply/output error remains uncertain even when its exact cause is unknown.
Reason codes classify phases, not every possible AWS error: a backend denial code
can also mean a backend preflight timeout/unavailable CLI, requiring operator review.

1. Compare API/worker startup fingerprints; wrong account/bucket/backend/file or
   conflicting environment must stop startup with exit 2, without DB fallback.
2. Verify local request/history/approval flows remain functional across refresh.
3. An owned AWS job's deployment GET returns `enabled:false`, `eligible:false`;
   the UI shows disabled execution and no Deploy button. A valid owner POST to
   `/api/v1/jobs/<JOB_ID>/deploy` returns 503 and creates no deployment/audit row.
   Anonymous calls return 401, unauthorized users 403, invalid bodies 422.
4. Use isolated mocked tests for queued deployments, credentials, policy denials,
   tampering, crashes and release. Never insert synthetic recovery records in the
   real history database or enable apply merely to exercise a button.
5. Inspect API status logs, safe phase/reason responses and audit events. An
   unexpected 202/apply_started while disabled is a defect. Keep cookies/tokens,
   raw state, binaries and Terraform diagnostics out of screenshots/log exports.

Remaining live gate: effective IAM/backend/credential verification, protected
backups, operator evidence/recovery rehearsal and separate explicit approval of
the exact deployment and costs. Automated passing tests do not verify live apply.
