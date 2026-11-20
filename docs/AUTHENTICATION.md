# Local OIDC authentication

For Day 14 API/worker startup, use the shared private manifest and commands in
[LOCAL-OPERATIONS.md](LOCAL-OPERATIONS.md). They preserve OIDC verification and
use one role mapping, database and fixed AWS target, with apply disabled. The
individual environment commands below document the underlying authentication setup.

CloudOps uses Authorization Code with PKCE through Auth.js in the Next.js
server. The browser receives an encrypted, HttpOnly session cookie. A same-origin
Next.js BFF route reads the API access token on the server and adds it to the
FastAPI request. FastAPI validates the token again with PyJWT and the configured
JWKS, issuer, API audience, expiry, access-token type and fixed algorithms.

Roles are mapped from the verified `issuer|subject` in
`CLOUDOPS_ROLE_ASSIGNMENTS`. Email, display name, browser fields and token role
claims do not grant CloudOps permissions.

## Local Keycloak setup on Windows

This setup requires Docker Desktop and is operated manually; it was not started
as part of the automated regression tests. The
realm file creates two stable user records without passwords and a confidential
PKCE client whose secret comes from the terminal environment.

From the repository root, generate temporary local secrets and start Keycloak:

```powershell
$clientSecretBytes = New-Object byte[] 32
[Security.Cryptography.RandomNumberGenerator]::Fill($clientSecretBytes)
$env:CLOUDOPS_OIDC_CLIENT_SECRET = [Convert]::ToBase64String($clientSecretBytes)
$adminPasswordInput = Read-Host "Temporary Keycloak admin password" -AsSecureString
$adminPassword = [Net.NetworkCredential]::new("", $adminPasswordInput).Password
$realmPath = (Resolve-Path .\docs\auth\keycloak).Path

docker run --name cloudops-keycloak --rm -d `
  -p 127.0.0.1:8080:8080 `
  -m 1g `
  -e KC_BOOTSTRAP_ADMIN_USERNAME=admin `
  -e KC_BOOTSTRAP_ADMIN_PASSWORD=$adminPassword `
  -e CLOUDOPS_OIDC_CLIENT_SECRET=$env:CLOUDOPS_OIDC_CLIENT_SECRET `
  -v "${realmPath}:/opt/keycloak/data/import:ro" `
  quay.io/keycloak/keycloak:26.8.2 `
  start-dev --import-realm
```

In the same PowerShell terminal, configure passwords for the two imported
users. The values remain terminal variables and are not written to the
repository:

```powershell
$adminPasswordInput = Read-Host "Temporary Keycloak admin password" -AsSecureString
$engineerPasswordInput = Read-Host "Password for cloudops-engineer" -AsSecureString
$approverPasswordInput = Read-Host "Password for cloudops-approver" -AsSecureString
$adminPassword = [Net.NetworkCredential]::new("", $adminPasswordInput).Password
$engineerPassword = [Net.NetworkCredential]::new("", $engineerPasswordInput).Password
$approverPassword = [Net.NetworkCredential]::new("", $approverPasswordInput).Password

docker exec cloudops-keycloak /opt/keycloak/bin/kcadm.sh config credentials `
  --server http://localhost:8080 --realm master --user admin --password $adminPassword
docker exec cloudops-keycloak /opt/keycloak/bin/kcadm.sh set-password `
  -r cloudops --username cloudops-engineer --new-password $engineerPassword
docker exec cloudops-keycloak /opt/keycloak/bin/kcadm.sh set-password `
  -r cloudops --username cloudops-approver --new-password $approverPassword
```

Continue in the same terminal so the generated client secret remains available
without being printed or written to disk. Generate a separate Auth.js session
secret and set the shared, non-secret role mapping:

```powershell
$authSecretBytes = New-Object byte[] 32
[Security.Cryptography.RandomNumberGenerator]::Fill($authSecretBytes)
$env:AUTH_SECRET = [Convert]::ToBase64String($authSecretBytes)
$env:AUTH_URL = "http://localhost:3000"
$env:AUTH_ISSUER = "http://127.0.0.1:8080/realms/cloudops"
$env:AUTH_CLIENT_ID = "cloudops-web"
$env:AUTH_CLIENT_SECRET = $env:CLOUDOPS_OIDC_CLIENT_SECRET
$env:CLOUDOPS_API_URL = "http://127.0.0.1:8000"
$env:CLOUDOPS_ROLE_ASSIGNMENTS = '{"http://127.0.0.1:8080/realms/cloudops|11111111-1111-4111-8111-111111111111":["engineer"],"http://127.0.0.1:8080/realms/cloudops|22222222-2222-4222-8222-222222222222":["approver"]}'

$nodeTools = Resolve-Path .\.tools\node-v26.13.1-win-x64
$env:Path = "$nodeTools;$env:Path"
Push-Location .\frontend
& "$nodeTools\npm.cmd" run dev
```

Start FastAPI in a separate terminal with its resource-server settings:

```powershell
$env:OIDC_ISSUER = "http://127.0.0.1:8080/realms/cloudops"
$env:OIDC_AUDIENCE = "cloudops-api"
$env:OIDC_JWKS_URL = "http://127.0.0.1:8080/realms/cloudops/protocol/openid-connect/certs"
$env:OIDC_ALLOWED_ALGORITHMS = "RS256"
$env:OIDC_ACCESS_TOKEN_TYPES = "JWT"
$env:CLOUDOPS_ROLE_ASSIGNMENTS = '{"http://127.0.0.1:8080/realms/cloudops|11111111-1111-4111-8111-111111111111":["engineer"],"http://127.0.0.1:8080/realms/cloudops|22222222-2222-4222-8222-222222222222":["approver"]}'
& ".\backend\.venv\Scripts\python.exe" -m uvicorn app.main:app --app-dir .\backend --reload
```

Start a long-running worker in a third terminal, from the repository root:

```powershell
Push-Location .\backend
& .\.venv\Scripts\python.exe -m app.worker
Pop-Location
```

Leave it running while using the portal. It polls continuously for queued jobs;
`--once` is an optional one-job/empty-queue diagnostic mode, not normal startup.
API and worker both default to the same absolute `backend/cloudops.db`. For a
custom database, set the same absolute `DATABASE_URL` in both terminals before
starting them. Do not run a second worker for manual testing against a live job.
Local plans use only the credential-free lab; AWS development plans require the
separate existing owner-operated credential/setup flow.

Day 14 keeps `CLOUDOPS_ENABLE_AWS_APPLY=false` in both API and worker. Do not
enable it for authentication or portal verification. Executable AWS plan capture
requires the same private account and `CLOUDOPS_AWS_STATE_BUCKET` pins in API and
worker processes; these values are not browser configuration. The worker must
also have the same validated `CLOUDOPS_ROLE_ASSIGNMENTS` mapping to revalidate
requester/approver authorization before any future execution. See the
[disabled provisioning operator guide](APPROVED-PROVISIONING.md).

Open `http://localhost:3000`. Sign in as `cloudops-engineer`, open
**New Infrastructure Request**, create and plan a request, then request approval.
Sign out, confirm Keycloak logout, and sign in as `cloudops-approver` to
review it. The approver cannot create requests, and the engineer cannot approve
their own request. Neither decision executes Terraform.

Stop Keycloak with `docker stop cloudops-keycloak`. Because the container uses
`--rm`, the local realm and its credentials are removed when it stops.

## Keycloak logout

Auth.js sign-out alone removes only the local session; it does not end Keycloak
SSO. The portal now submits a Next.js Server Action (POST with origin checking)
which calls Auth.js's server sign-out API, clearing its session cookie chunks,
then redirects to the configured Keycloak realm's
`/protocol/openid-connect/logout` endpoint. It sends `client_id`, a fixed
`post_logout_redirect_uri`, and a random, short-lived state nonce.

No access/refresh/ID token is sent to client JavaScript or placed in a logout
URL. This uses the standard client-ID-only flow, so **Keycloak asks you to confirm
logout**. Confirm to clear SSO. Cancelling leaves Keycloak SSO active even though
CloudOps is signed out. This is deliberate: avoiding an `id_token_hint` in browser
URLs trades a provider confirmation for keeping tokens server-side.
See [OIDC RP-Initiated Logout](https://openid.net/specs/openid-connect-rpinitiated-1_0.html)
and [Keycloak's logout endpoint](https://www.keycloak.org/securing-apps/oidc-layers#logout-endpoint).

In Keycloak, configure client `cloudops-web`:

- **Valid Redirect URIs**: `http://localhost:3000/api/auth/callback/keycloak`.
- **Valid Post Logout Redirect URIs**: `http://localhost:3000/auth/logout/callback`.
- Keep the existing confidential client, Standard Flow, PKCE S256 and API
  audience mapper. Do not use wildcard logout redirects.

The realm import includes the logout setting as `post.logout.redirect.uris`.
For an already imported realm, update the client setting manually; importing a
realm does not update an existing realm. No live Keycloak settings were changed
by this implementation. Production needs exact HTTPS URLs for both callbacks.

`AUTH_URL` must be the trusted application origin (local example:
`http://localhost:3000`); it is never read from user-supplied return URLs. The
callback checks state against a five-minute HttpOnly, SameSite=Lax cookie, clears
it, and redirects only to the fixed `/signed-out` page. Invalid/missing state
gets HTTP 400. Missing provider configuration still clears the local session and
shows that SSO logout is unconfirmed. Do not log cookies or logout query strings.

## Verify persistent request management

These are manual checks; automated tests use temporary databases and mocked
identity/JWKS boundaries. A live browser-to-Keycloak login is a separate check.
Use the local Keycloak, frontend and API setup above. Use a unique request name
for each run. Keep the same backend database for the persistence checks.

1. Sign in as `cloudops-engineer`. Open **Requests / History**. A fresh database
   shows an empty state; an existing database shows only this engineer's owned
   requests. Legacy rows without owners remain hidden.
2. Open **New Infrastructure Request** and create a unique request. Queue
   **Generate local plan** in the same form. With the continuous worker running,
   observe `queued`, `planning` and `plan_ready` (short jobs may complete between
   polls). Do not select AWS development planning for this check.
3. Submit a second unique request and local plan without restarting the worker.
   Verify it also completes. To check the idle-worker case, stop the worker,
   queue a local plan and verify status GETs remain HTTP 200/`queued`, then restart
   the worker and verify completion. A missing worker must not produce HTTP errors.
4. Open **Requests / History**, select a name and use **Refresh details**.
   Refresh the browser at `/requests/{id}` and verify the same record reloads.
   Verify creation date, job status, action
   counts, resource addresses/actions and the plan revision. There must be no raw
   state, logs, credentials or access tokens in the history payload or page.
5. Request approval from the current intact completed job in history. Verify
   the pending state and requested audit event. Older or revisionless jobs must
   not offer Request Approval. In the new-request form, use **New Request / Reset**;
   history must remain. Sign out and confirm Keycloak logout, close/reopen the portal,
   sign back in as the same engineer and confirm history remains. Restart FastAPI
   against the same SQLite database and confirm again.
6. Click Sign In in the same browser. Verify Keycloak asks for credentials and
   permits `cloudops-approver` rather than reusing the engineer's SSO identity.
   Open **Approvals**, then **Pending reviews**, then **View request and plan
   history** before deciding. Use **Approve reviewed plan** or **Reject reviewed
   plan** in details, then open **My decisions** on the Approvals screen
   and confirm the outcome, revision, decision time and audit event. Refreshing
   or signing in again must retain the decision. No Terraform execution follows.
7. Sign back in as the engineer and refresh the history/details. Confirm the
   decision and audit appear. A newly generated plan is a separate job/revision
   and needs its own approval; the previous record remains in job history.
8. With more than 20 genuine saved requests/reviews, use Next/Previous page to
   verify newest-first browsing. Do not seed artificial production history.
9. Stop the API and refresh history to check an error/retry state. Restart it and
   refresh again. An expired API token should require signing in again; polling
   stops on HTTP 401 instead of reporting a perpetual retry. HTTP 403 means access
   denied, 404 means the configured API database has no such job, and 502 means
   the BFF could not reach FastAPI. Never paste bearer tokens when reporting errors.

If an earlier job reports `failed`, the public error is deliberately redacted.
Check the worker's safe phase/reason or exception-class log, executable PATH and
fixed lab setup. Hard process termination can leave `planning`; there is no
automatic requeue or crash recovery yet. Do not manually replay a job until an
operator confirms the previous process has stopped. A supervised container/OS
service and recovery policy are future work; neither is required for local use.

Backend tests cover cross-owner access, reviewer restrictions, unmapped roles,
missing authentication and no-self-approval. For an additional manual multi-user
check, configure another Keycloak engineer with a distinct stable subject and
server mapping; that user must not see the first engineer's requests. A subject
mapped to both engineer and approver must still receive HTTP 403 when deciding
its own approval. Use the authenticated BFF for these checks; do not put tokens
in browser storage or override authorization in application code.

## Deployment authorization verification (execution stays disabled)

In AWS request details the deployment panel reports the disabled switch. Approval
alone must never create a deployment attempt. Local/legacy plans cannot deploy.
The explicit deploy endpoint requires the engineer owner and exact artifact
digest/revision; a separate approver cannot initiate deployment on the owner's
behalf. Both worker and API enforce authorization independently of button state.
Do not enable apply to test confirmation UI: frontend tests use mocked eligibility
and requests without real AWS operations. Follow
[manual checks while disabled](APPROVED-PROVISIONING.md#apis-and-manual-verification-while-disabled).
