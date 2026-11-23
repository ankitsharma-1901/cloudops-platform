# Phase 1A: reviewed EC2 configuration

Phase 1B currently blocks the direct create endpoint while independent approval
and worker execution are completed. The creation description below records the
Phase 1A behavior, not an enabled production path.

This is an offline implementation. No live EC2 or IAM change was performed to
validate it. The direct AWS API demo remains gated by its existing feature flag,
dedicated role and singleton reservation.

The authenticated engineer reads the backend catalog, submits a typed
configuration for validation and receives an owner-bound review ID. The server
stores the reviewed values for 15 minutes. Creation accepts that ID and the
existing explicit confirmation; it does not accept configuration overrides.
Consumption of the review and creation of the singleton reservation are in the
same database transaction. A failed reservation leaves the review unconsumed.
The fixed legacy confirmation-only create remains available for compatibility;
it cannot accept custom settings and is not the reviewed configurable path.

Only the instance name is editable: 3–32 lowercase letters, digits and hyphens,
starting with a letter and ending in a letter or digit. The catalog fixes the
region, AL2023 AMI, type, encrypted gp3 root volume and reviewed network. Public
IPv4, an instance profile, and software installation are not provided. No cost
estimate is calculated; actual AWS charges apply.

The active and completed records carry an optional name. SQLite startup adds
nullable columns to existing records; missing legacy names display the historic
fixed name. Existing instance ownership and exact-ID termination checks, audit
events and uncertain-operation recovery remain unchanged. Back up the local
database before applying the additive schema migration by restarting services.

The next phase should independently review authorization, network and billing
controls before expanding any catalog choice or considering removal of the
legacy fixed path. The initial release and a subsequent lifecycle require
separately authorized live testing; local mocks are not evidence of AWS success.

**Unresolved operational task:** verify that the bootstrap Terraform state has
a protected, recoverable backup. This phase did not inspect or modify that state.
