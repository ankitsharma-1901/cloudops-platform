# Phase 1C approved EC2 execution

The CloudOps worker can process an independently approved EC2 request when the
dedicated execution switch, verified identity authority, and EC2 demo configuration
are enabled. Both legacy direct-create variants still return HTTP 503. Terraform
planning, approval, apply, and state use their existing paths.

## Execution and recovery

1. The worker validates the saved configuration against the pinned catalog and
   recomputes its digest. It checks the decision audit, independent approver,
   expiry, and current roles and accounts. The network calls run outside SQLite
   transactions. The claim transaction rechecks eligibility and atomically
   records the execution identity, idempotency token, audit, and singleton demo
   reservation.
2. The worker uses the dedicated demo profile and verifies the AWS account and
   assumed role with STS. Before launch it checks the pinned network and image,
   rechecks both Keycloak accounts and roles, and transactionally revalidates
   the exact approval and reservation. It saves the execution-start marker
   before calling RunInstances. The fixed payload uses the saved name and
   client token, a private interface, IMDSv2, and an encrypted root volume.
3. A successful result updates the request, reservation, and audit together.
   The request's `execution_status` is the outcome. The execution record retains
   the immutable claim identity and launch-boundary marker. The demo's existing
   owner-only termination and history flow uses the same singleton.
4. An abandoned pre-launch claim is fenced after five minutes. Fencing marks the
   request for operator review and releases its reservation in the same
   transaction. The old worker can no longer cross the launch boundary. That
   request stays consumed and requires a new reviewed request and approval to
   try again. A preflight or reauthorization failure also consumes the claim
   and releases the slot when the worker can prove it never crossed the boundary.
5. A failed or interrupted launch transitions to `outcome_unknown` and retains
   the slot. The worker only issues read calls to reconcile it. It looks up both
   the AWS client token and demo tag, requires one exact same-account match, and
   verifies the instance ID, returned client token, name, pinned compute and
   network settings, and ownership tags. Missing, inaccessible, duplicate, or
   mismatched results retain the reservation. The worker never replays a launch
   or frees a slot on negative evidence. A pause past the short launch window
   after committing the marker also retains the slot for review.

The read-only reconciler runs while the worker is enabled. If the result remains
unknown, keep the reservation and request audit. An authorized operator can
investigate the exact token, account, region, and AWS launch evidence separately.
There is no automated negative-outcome clearance or destructive recovery command.
The earlier claim prototype remains disconnected.

## Keycloak and service setup

For local persistence, TLS trust, and the worker's scoped account and realm-role
reads, follow the [Keycloak HTTPS cutover](KEYCLOAK-LOCAL-HTTPS.md) after local
cutover approval. Keep EC2 execution disabled until separately authorized.

1. **Protect the database.** Stop the API and all workers and follow [Local
   Operations](LOCAL-OPERATIONS.md) to copy the database and artifacts, verify
   integrity and restore the copy in isolation. Review startup schema changes
   before running the updated services against the real database.
2. **Use the exact HTTPS issuer.** Configure Keycloak and the API with the same
   issuer in the form `https://<host>[/<base-path>]/realms/<realm>`. The worker
   uses normal certificate validation and direct connections with no redirects.
   The existing HTTP development issuer will fail closed. If the issuer changes,
   issue fresh requests under the new issuer. Previously saved identities remain
   bound to their original issuer.
3. **Create one confidential OpenID Connect client** in that realm, for example
   `cloudops-ec2-worker`. Enable client authentication and service accounts.
   Disable Standard Flow, Direct Access Grants, and other interactive flows.
   Under **Realm settings**, enable **Admin Permissions** if approved for this
   realm. Open **Permissions**, create a user policy selecting the client's
   service-account user, and assign that policy to a **Users** permission with
   only the `view` scope. Select the specific authorized requester and approver
   users as resources. Keep broad
   `realm-management` roles and user write, impersonation, and role-mapping
   permissions off. Verify permission behavior on the deployed Keycloak version.
   If exact-user read access cannot be established, leave execution disabled.
   See the [Keycloak admin guide](https://www.keycloak.org/docs/latest/server_admin/#_fine_grained_permissions)
   and [Admin REST API](https://www.keycloak.org/docs-api/latest/rest-api/index.html).
4. **Verify the service account offline from AWS.** Use client credentials at
   `<issuer>/protocol/openid-connect/token` with HTTP Basic client authentication.
   Verify the service account can read
   `<origin>[/<base-path>]/admin/realms/<realm>/users/<user-id>` for each intended
   user and confirm the exact token subject ID and Boolean `enabled` field.
   Also verify `/users/<user-id>/role-mappings/realm/composite` returns the
   expected effective realm role. Verify both reads are denied outside scope
   and test denied writes on an isolated restore.
   The worker accepts user IDs made of 1–128 ASCII letters, digits, underscores
   or hyphens. Manage the client secret with the worker's secret injection
   mechanism. Keep it out of the manifest and normal logs.
5. **Use one private manifest for API and worker.** Its `roles` map is the
   CloudOps role authority, keyed by `<issuer>|<user-id>`. Give the requester
   `engineer` and a different account `approver`. The worker reloads the validated
   manifest around each Keycloak check. A missing or changed manifest that no
   longer agrees with the startup environment blocks launches until the service
   configuration is synchronized. Change roles through the shared manifest and
   restart both processes with the updated mapping. Keycloak account disablement
   is observed independently at the next current-account check. The two remote
   account reads and SQLite commit cannot form one distributed transaction.
6. **Review the AWS role and isolate activation.** Follow the [existing demo
   role runbook](../terraform/aws/ec2-only/iam/DEMO-ROLE.md) for
   `cloudops-ec2-demo-deploy`. Keep Terraform apply disabled for this rollout.
   After separate authorization, start the API with the same manifest and
   `--confirm-ec2-demo`. For the worker only, supply
   `CLOUDOPS_EC2_KEYCLOAK_CLIENT_ID` and `CLOUDOPS_EC2_KEYCLOAK_CLIENT_SECRET`
   from protected configuration and set `CLOUDOPS_ENABLE_APPROVED_EC2=true`.
   With `enable_ec2_demo` true in that manifest, the worker command from
   `backend` is:

   ```powershell
   & .\.venv\Scripts\python.exe -m app.local_start --config ..\.cloudops.local.json --confirm-ec2-demo worker
   ```

   Review all queued, unexpired approvals before enabling the worker. Omit or
   clear `CLOUDOPS_ENABLE_APPROVED_EC2` and restart the worker to disable both
   new launches and automatic reconciliation.

The external blocker is verification of the deployed HTTPS Keycloak realm,
service-account permissions, and effective dedicated AWS role. No real database
migration, Keycloak configuration, or AWS verification was performed for this
implementation. The integration tests use temporary SQLite and offline providers.

AWS reference: [EC2 idempotency](https://docs.aws.amazon.com/ec2/latest/devguide/ec2-api-idempotency.html)
and [DescribeInstances](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeInstances.html).
