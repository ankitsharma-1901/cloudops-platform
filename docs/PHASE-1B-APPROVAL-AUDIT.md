# Phase 1B: direct EC2 approval audit

## Observed behavior before the gate

- Keycloak access tokens are validated by FastAPI, which assigns roles from the
  server-side mapping. The website uses an Auth.js session and BFF.
- Terraform plan approvals are bound to a job, revision and artifact digest;
  the approval API requires an approver distinct from the requester. The
  deployment worker revalidates the saved artifact, approval and role mapping.
- Direct EC2 review validates a fixed configuration and creates a short-lived,
  owner-bound review record. It is a configuration review, not an independent
  approval. Before this gate, both the legacy confirmation-only request and a
  reviewed request could call EC2 directly from an engineer's API request.
- The direct EC2 create path had no approver decision, durable approved job,
  worker claim or worker-side approval revalidation. Existing demo audit records
  track lifecycle events but do not constitute an approval decision.

## Current safety posture and unfinished work

Both direct EC2 creation variants fail closed before AWS client creation.
The first vertical slice adds owner-bound request submission from a validated
review, a configuration digest, separate approver decisions, cancellation,
expiry, status screens and append-only decision audit. SQLite protects request
identity and configuration from modification. The same review can be submitted
only once. Approval does not execute AWS, and the existing worker does not claim
these requests. Status, termination and reconciliation remain separate.

This is not complete Phase 1B. Before enabling execution, implement and test an
atomic single-use worker claim, worker-side digest, expiry, roles and ownership
revalidation, a reserved EC2 lifecycle, execution attempt/result audit and
crash/uncertainty recovery. Keep the gate until those protections are verified
together; approval is not permission to skip explicit AWS mutation authorization.

No live database migration, AWS call, IAM change or Terraform state change was
performed for this gate. Back up the actual database before any later migration.
