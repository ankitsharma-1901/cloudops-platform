# Problems encountered and resolutions

## Read-only infrastructure inventory

- An EC2 instance list alone cannot establish Terraform ownership. The
  inventory compares IDs against the pinned EC2-only state and reports unknown
  when that evidence is unavailable or unexpected; it never exposes state.
- The first frontend refresh test reused a consumed `Response` object and
  failed. The test fixture now creates a fresh response for each request.
- Live AWS inventory has not been verified in this implementation session.
  Credential, backend and IAM prerequisites remain operator checks.

## EC2-only apply verification

- A first plan against an empty backend has a prior state with serial zero and
  no lineage. A missing state object is accepted only with that empty prior state;
  existing state must match lineage and serial.
- An apply may partly succeed, or succeed while result verification fails. Such
  outcomes remain `needs_operator_review` with the reservation retained. Mocked
  tests do not establish successful live AWS execution.
# EC2-only computed profile provenance

Provider 6.73.0 reported the profile as unknown in a real plan despite an empty
configured profile. CloudOps continues to reject the unknown value. Local HCL
source is now parsed and pinned as an additional preflight, but that does not
verify the saved plan's source snapshot or the executing provider binary. Do not
enable an unknown-value exception until both are bound to the exact saved plan
and checked again at approval and execution.

## November 18–19 investigations

- **Provider unknowns:** A real AWS provider 6.73.0 plan reported an unknown
  `iam_instance_profile`, with a `launch_template` unknown shape, even though
  the source configures an empty profile. The strict policy rejects the plan;
  source validation alone cannot prove what produced a saved binary. No safe
  exception has been verified. Bind and recheck source and provider provenance
  before considering one; never interpret an unknown as proof of absence.
- **IAM role boundaries:** Legacy role permissions and the proposed dedicated
  EC2-only role do not establish one another's effective access. Inspect the
  actual assumed identity, attached policies, boundaries and applicable denies;
  a checked-in policy draft is not evidence of a live grant.
- **Credential timeout:** An AWS session timed out during investigation but the
  timeout did not recur, according to the reported investigation. STS identity
  was subsequently verified. A transient timeout does not establish its cause
  or guarantee later credentials will remain valid; recheck identity privately.
- **Demo tag mismatch:** The Terraform policy requires `ManagedBy=Terraform`,
  while the demo uses `ManagedBy=CloudOpsDemo` and a demo ID. The proposed demo
  policy is not proof of effective permissions. Live creation and termination
  have not succeeded; review effective permissions before a separately authorized
  live test instead of assuming the Terraform grant covers the demo.
- **Windows lock recovery:** Stopping a virtual-environment launcher can leave
  its child interpreter holding the service lock. The test launches the actual
  interpreter and waits for that process to exit. Track the lock-owning process,
  not just its launcher, and do not delete active lock files.
- **Frontend test-worker timeouts:** A single-worker full Vitest run reached its
  startup banner but produced no test results during the observed interval and
  was interrupted. The initial sandboxed run failed earlier with `EPERM` writing
  a Vite temporary file; the approved outside-sandbox rerun passed that point.
  The stall's cause remains unestablished. Diagnose individual test files and
  worker startup before interpreting it as an application failure.
