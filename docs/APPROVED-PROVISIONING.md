# Approved AWS provisioning — Day 14 Phase A

**Implemented, but real AWS apply is disabled.** This document is not permission
to enable execution or provision infrastructure. No AWS/IAM changes or real apply
were performed while implementing this feature. Daily progress reporting remains
a separate end-of-Day-14 task.

## Scope and current behavior

The existing AWS development stack is the only target: the operator-verified
account, `ca-central-1`, development, `cloudops-deploy` profile assuming
`cloudops-terraform-deploy`, and the initialized S3 backend at
`cloudops/development/terraform.tfstate`, in the default Terraform workspace.
The stack remains shared; creating a portal request does not create a new stack
or make arbitrary user configuration executable. No Terraform/IAM source or
permission policy was expanded for this feature.

The continuous worker saves the exact binary produced by the existing fixed
`terraform plan -out` command. It records the binary SHA-256, request/job IDs,
plan revision, account, region, environment, backend/configuration fingerprints,
creation/expiration times and sanitized summary in `SavedPlanArtifact`.
The plan expires after 30 minutes. Approval expiration cannot exceed artifact
expiration, and expiration is checked even after an approval is approved.

The existing approval API now binds saved AWS approvals to `artifact_digest` as
well as job/revision. Approval itself never queues or executes deployment.
Local-lab and legacy summary-only results remain readable and cannot deploy.
An older approval without an artifact digest is never upgraded implicitly.
Destructive plans can still be inspected, but are ineligible for execution.

The owner must initiate a distinct **Deploy Approved Plan** request. The UI
requires acknowledgement that real resources and charges may result. A strict
request body contains only the fixed confirmation, selected revision and digest;
identities, commands, account, region and paths are never client parameters.

## Independent safety gates

`CLOUDOPS_ENABLE_AWS_APPLY=false` is the default. Only the exact value `true`
would enable a process; unset, uppercase, `1`, and `yes` remain disabled. Leave
this variable false in both API and worker. There is no frontend/public version
of this setting. Tests exercise enabled branches by mocking guard functions and
CLI boundaries; they do not enable a live deployment environment.

This environment setting is not an instantaneous distributed cancellation switch.
Changing one terminal does not change another process's environment, and cannot
undo an apply already started. Keep both processes disabled for this phase.
Role-map revocations likewise require synchronized API/worker configuration and
restart; roles are not trusted from token role claims. A centralized control and
revocation mechanism is future platform work.

- The API rejects deployment while disabled, including direct API calls.
- The worker rechecks the switch after claiming, after preflight, and the runner
  checks it immediately before the fixed apply command.
- Only the verified engineer who owns the request may initiate deployment.
  The approver must be a different subject. The worker rechecks current server
  role assignments for both subjects before execution.
- The job must be the latest job for its request, successfully completed in AWS
  mode, with a matching intact summary/revision and saved artifact. A later local,
  failed or queued job also supersedes the old plan's eligibility.
- Approval must be explicitly approved, current, unexpired, revision/digest
  matched, and separated from the requester/initiator. Pending, rejected,
  expired, consumed or mismatched artifacts are rejected.
- The binary is read and hashed again. Current configuration and backend
  fingerprints must match the captured values. STS must return the exact
  permitted account and assumed-role ARN. A bounded `list-objects-v2` with the
  exact state-key prefix, `--max-keys 1`, `--no-paginate`, and
  `--expected-bucket-owner` verifies access/ownership before execution. Only
  `KeyCount` is returned internally; it is not a state-existence/integrity check.
  Unlike HeadBucket this supplies `s3:prefix`, satisfying the existing ListBucket
  grant. No IAM template permissions were changed or expanded. Attached policies,
  explicit denies and actual ownership require separately authorized live review.
- The worker inspects the exact executable binary with `show -json` again.
  Only create/no-op actions on the twelve existing fixed-stack addresses are
  allowed; the single existing AL2023 data lookup may read/no-op. Updates,
  deletes, replacement action sequences, imports, moved addresses, deferred or
  errored plans, unexpected resources, empty-create plans and drift are blocked.
- Execution passes a verified private copy of the exact approved binary as the
  positional plan argument. It never generates a replacement plan. There is no
  generic command, arbitrary path, destroy, import or state-management endpoint.

Saved-plan execution does not prompt interactively in Terraform; the application
approval/confirmation gates must run first. See HashiCorp's
[saved-plan apply behavior](https://developer.hashicorp.com/terraform/cli/commands/apply)
and [sensitive plan-file guidance](https://developer.hashicorp.com/terraform/cli/commands/plan).

## Private storage and trust boundary

Binary artifacts and execution copies live in `backend/.private/plans/`, outside
Next.js/public/static content, with an explicit Git ignore rule. Names are
generated UUIDs, never user paths. Files are exclusively created, size bounded
and hashed. Symlinks, hardlinked files and Windows reparse points/parent junctions
are rejected. POSIX modes are 0700/0600; Windows uses a protected DACL granting
only the current service user access. Permission-setting failures fail closed.

Binary plans may contain sensitive configuration even when CLI output is
redacted. No binary/raw JSON/state download API exists. Public schemas expose
only safe plan details, digest, identities, statuses, timestamps, fixed messages
and allowlisted non-sensitive `instance_id`, `vpc_id` and `subnet_id` values.
Other outputs, IP addresses and raw CLI diagnostics are discarded.

The trusted host, service OS identity, private database, Terraform/provider
installation, AWS credential configuration and repository configuration remain
part of the security boundary. A SHA-256 digest is not encryption and cannot
defend against a privileged attacker rewriting both application/database and
files. Use protected persistent storage and volume encryption in a later reviewed
deployment. No encryption/KMS infrastructure is created here.

API and worker currently run with access to the same private directory and
database. Do not put artifacts in a public/shared folder. Retention/purge is
operator-managed in this phase: no background deletion removes forensic evidence.
An interrupted save can leave an orphan private file, which cannot deploy without
its database/job/approval binding. Review retention and backup procedures before
any real enablement.

## State serialization and uncertain outcomes

`TerraformStateOperation` is a durable, unique mutex for the shared AWS state.
The API reserves it atomically when queuing an AWS plan or deployment. Workers
retain it through completion. Existing partial unique plan indexes and Terraform's
native S3 lock remain in place. The API and a SQLite trigger also prevent any
replan of a request with queued/running/uncertain deployment.

All application processes must use the same database. Independent databases or
manual CLI workflows do not participate in the application mutex; native S3
locking and saved-plan stale-state checks remain the final external protection.
Do not run manual Terraform concurrently with this worker.

Deployment states:

| State | Meaning / recovery |
| --- | --- |
| `queued` | Explicit owner request persisted; never triggered by approval alone. |
| `running` | Atomically claimed; artifact consumed durably before execution. |
| `succeeded` | Apply exited successfully and sanitized result collection completed. |
| `failed` | Blocked before the apply boundary; artifact still single-use, no automatic retry. |
| `needs_operator_review` | Apply may have partially changed AWS, or an interrupted run was marked by an operator. Mutex retained; no replay. |

Audit events (`queued`, `running`, `apply_started`, outcome) accompany durable
transitions. SQLite triggers prevent audit updates/deletes. A unique job/artifact
binding prevents duplicate attempts, even after failure or success. A new attempt
requires a fresh plan and approval, after reconciliation where applicable.

If a process dies after its durable claim, the record may remain `running`.
This is intentional; no startup sweep assumes another worker is dead. After
stopping all workers and confirming no Terraform process is still running, an
operator must inspect and target one interrupted operation locally:

```powershell
# From backend/. This only changes local review status; it runs no AWS commands.
.\.venv\Scripts\python.exe -m app.deployment_recovery inspect
```

Inspection is bounded to 100 records per category and does not initialize or
migrate the database. See [LOCAL-OPERATIONS.md](LOCAL-OPERATIONS.md) for targeted
mark/release, required attestations, evidence and state/resource comparison.
Marking retains reservations. Release is a separate explicit reconciliation
action; neither operation retries execution or resets consumption. There is no
web retry/clear-lock button. A supervised worker remains future work.

## Server configuration and IAM requirements

Keep `CLOUDOPS_ENABLE_AWS_APPLY=false`. The API and worker need the same database,
artifact directory/service identity and server-controlled role assignments.
For executable AWS plan capture, set the existing independently verified
`CLOUDOPS_AWS_EXPECTED_ACCOUNT_ID` and the new `CLOUDOPS_AWS_STATE_BUCKET` privately
in both terminals. These are operator pins, not browser inputs. Account metadata
is stored server-side with the artifact; it is not returned by deployment APIs.

The initialized backend must match the pinned bucket/account/key/region, with
`encrypt=true`, `use_lockfile=true`, matching `allowed_account_ids`, and default
workspace. No static credentials, custom service endpoints, credential-validation
bypass or alternate profile/assume-role chain is accepted in backend metadata.
The configuration fingerprint covers the seven existing `.tf` files and provider
lock file; unexpected root Terraform/variable files are rejected. API and worker
must see the same trusted configuration and initialized backend metadata.

No role/policy changes were made or tested against AWS. Any future enabled run
requires the already-reviewed `cloudops-terraform-deploy` permissions for this
fixed network/EC2/SSM-instance-IAM stack, restricted PassRole, and the exact S3
state object/native-lock permissions. Existing KMS-backed state, if used, needs
its separately approved key grants. Do not add administrator permissions or
broaden IAM to work around an error. The existing
[deployment-role proposal](../terraform/aws/dev/deployment-role/README.md) and
[owner-operated runbook](../terraform/aws/dev/DEPLOYMENT.md) still require owner
review; current attached grants were not inspected live.

## APIs and manual verification while disabled

- `GET /api/v1/jobs/{job_id}/deployment`: authorized eligibility, safe attempt
  record and bounded audit history. No filesystem paths/backend/account data.
- `POST /api/v1/jobs/{job_id}/deploy`: explicit owner action, fixed confirmation,
  revision and digest. Returns 503 while disabled, 403 for unauthorized subjects,
  409 for invalid plans/duplicates/state reservations, and 422 for invalid bodies.

1. Keep API and worker switches false; restart the updated API/worker against
   the same database. Back up an existing SQLite database before the additive
   migration (new tables plus nullable approval artifact digest).
2. Sign in and open existing request history. Legacy/local jobs must remain
   readable; local jobs have no deploy action. Saved AWS details show disabled
   execution and no Deploy Approved Plan button.
3. After separately authorizing an existing real AWS **plan-only** operation,
   supply the private account/bucket pins and generate an AWS plan. Inspect only
   sanitized history and confirm a private artifact/metadata record is created.
   Do not open, upload or paste binary plans or raw show output.
4. Request and separately approve that plan using the existing OIDC identities.
   Approval must not add a deployment row or start Terraform apply. The panel
   still says deployment is disabled.
5. In an authenticated API test, submit the displayed digest/revision and fixed
   confirmation to the deploy endpoint. It must return 503 and create no attempt.
   Do not enable execution to test the UI; use the mocked frontend tests below.
6. Use another engineer or an approver identity to verify direct deployment is
   forbidden. Confirm no paths, raw plans, credentials, state or raw diagnostics
   appear in network responses. Retain the existing portal navigation/screens.

Automated commands (backend temporary databases and all cloud CLI calls mocked):

```powershell
Push-Location backend
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider
Pop-Location
Push-Location frontend
npm test
npm run build
npm run typecheck
Pop-Location
```

Frontend tests model confirmation/cancellation, charges, server refusal, eligible
and unavailable states, status polling and operator-review history without fake
production records. Backend tests cover integrity, expiry, stale/mismatched
approvals, destructive plans, duplicate/concurrent submission, state serialization,
role changes, invalid credentials, disabled API/worker/runner, interrupted execution
and sanitization. Real end-to-end AWS apply has not been performed and remains a
separate future authorization gate. Stop before enabling it.
