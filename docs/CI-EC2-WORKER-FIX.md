# EC2 worker CI investigation — 2026-11-23

## Confirmed root cause

The worker integration fixture replaced `boto3.Session`, but left
`botocore.session.get_session()` real. `ec2_demo.clients()` selects the fixed demo
profile before constructing Boto3. Building Botocore's credential resolver reads
that profile and raises `ProfileNotFound` when it is absent. The worker catches
the preflight exception and safely records `needs_operator_review`; it never
reaches the fake launch or the intended uncertain-outcome boundary.

A developer's configured AWS profile hid this fixture dependency. This is not
evidence of a Python 3.13 or SQLite state-transition defect. On the unchanged
checkout, Python 3.15.2/Windows passed all 20 worker tests with the host profile;
pointing both AWS configuration paths to empty files reproduced exactly the
five reported failures (15 passed, 5 failed). A direct credential-resolver check
confirmed `ProfileNotFound` without contacting AWS.

## Fix

`backend/tests/test_ec2_worker.py` now supplies an offline Botocore session and
custom-process provider as well as the existing fake Boto3 clients. It verifies
the selected profile, session wiring, and installation of the bounded
noninteractive process adapter. Each fixture points AWS configuration paths to
absent temporary files and disables metadata lookup, preventing a developer's
profile from masking regressions. The real `clients()` STS account/role checks,
network validation, approval and current-authority checks remain exercised.
No test expectations or production code changed.

## Repository safety

`.cloudops.local.json.before-ui-test` was tracked. It contains private deployment
pins, local paths, identity configuration and role assignments, rather than
credential fields. It is removed from the index while the local file is retained.
`.gitignore` now excludes `.cloudops.local.json.*` backups. Existing published
history is unchanged and still contains that file; removing a file from the tip
does not erase earlier commits. Do not rewrite history without owner approval.
Pattern scans are limited checks, not proof that no secret exists.

## Validation

- Original worker module with empty AWS files: 5 failed, 15 passed.
- Fixed worker module with empty AWS files: 20 passed, including all five reported
  cases, current-role revocation, expiry, wrong identity, concurrency, stale-worker
  fencing and positive reconciliation requirements.
- Offline deployment-policy unittest suite: 10 passed.
- Complete backend suite: 466 passed, 1 skipped, 1 failed in 233.84 seconds.
  The only failure is `test_store_rejects_paths_tampering_hardlinks_and_symlinks`:
  this managed Windows session denies `os.link` with WinError 5 at line 352.
  A separate rerun confirmed that environment restriction. The test and
  artifact-security protections remain unchanged.
- Current tracked-file and all-history scans for AWS access-key IDs, GitHub token
  patterns, and private-key headers: no findings; history included 386 blobs.
- Frontend component tests: 95 passed across 12 files; production build passed;
  TypeScript check passed (build before typecheck). Existing installed dependencies
  were copied into the isolated checkout; npm ci could not run without network.
- Existing CI at `27bdbae`: backend failed; frontend and all three Terraform
  matrix jobs passed. Run: https://github.com/ankitsharma-1901/cloudops-platform/actions/runs/45102368446

Validation uses temporary databases and mocked providers; no AWS resource,
IAM, Terraform apply/destroy, deployment or Keycloak launcher operation was run.
The Python 3.13 workflow remains unchanged.

## Execution limits and remaining work

Work is in `C:/workspace/cloudops-ci-fix` on
`feature/approved-aws-provisioning`, copied from the clean owner checkout at
`D:/Devops/PythonLearn/cloudops-platform`. The latter is outside this session's
writable roots and was preserved. Installed local Python is 3.15.2. Docker and
WSL access are denied, so Linux/Python 3.13 reproduction could not run locally.

Shell Git cannot resolve github.com. The connected diagnostic tool failed, and
the browser is signed out; full Actions logs require sign-in. A dry-run push confirmed the DNS failure. A new push and
Ubuntu CI verification remain required. The branch must not be declared ready
to merge until the new revision passes every required CI job. No merge is authorized.
