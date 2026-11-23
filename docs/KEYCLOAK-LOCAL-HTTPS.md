# Local Keycloak HTTPS cutover

Scope is Docker Desktop on this development machine. The target uses the existing
26.8.2 image, an external Docker volume, and HTTPS bound to loopback only. The
new issuer is `https://localhost:8443/realms/cloudops`. The checked-in realm
import is useful for initialization. The live database contains the current users,
clients, and signing keys and is the migration source.

## Before downtime

1. Pause CloudOps API, worker and frontend using their existing process controls.
   Keep all AWS execution flags disabled. If the launcher owns Keycloak and its
   stop action would also stop Keycloak, leave that step until downtime approval
   and start only the legacy Keycloak again before invoking the cutover.
2. Prepare a trusted local development CA and a PEM certificate with a
   `DNS:localhost` SAN. With an existing trusted mkcert installation, the
   following commands create an ignored directory and issue a leaf certificate
   and private key. They change local files only.

   ```powershell
   New-Item -ItemType Directory -Force .private/keycloak/tls | Out-Null
   mkcert -cert-file .private/keycloak/tls/localhost.pem -key-file .private/keycloak/tls/localhost-key.pem localhost
   ```

   If the CA is new, `mkcert -install` installs its root into the local trust
   store. Run this only with authorization for that trust-store change. Keep the
   CA signing key outside the repo and the leaf private key in the ignored
   directory with restricted access. Never print either key. The launcher
   readiness check and Python's default SSL context need to trust the CA.
   In the shell that starts Next.js, set `NODE_EXTRA_CA_CERTS` to the absolute
   path of the CA's public PEM before Node starts (for mkcert, use
   `$env:NODE_EXTRA_CA_CERTS = Join-Path (mkcert -CAROOT) 'rootCA.pem'`).
   This changes the shell environment only. Keep TLS verification enabled.
3. Confirm port 8443 is available. In the realm settings, check whether
   Frontend URL is set. A legacy HTTP override can prevent HTTPS discovery
   and must be reviewed before cutover. Preserve a private copy of the
   current CloudOps manifest and frontend issuer settings for rollback.
   Record the old JWKS key IDs and affected CloudOps request ownership for
   comparison without recording tokens or secrets.

## Authorized cutover

From the repo root, run:

```powershell
.\scripts\switch-keycloak-https.ps1
```

This changes local Docker state. It stops the legacy container, copies H2 after
shutdown into a new ignored backup directory, records SHA-256, and checks that the
volume seed has the same checksum. A separate Keycloak invocation with
networking disabled must successfully export both realms from that restored
database. The export stays in temporary container memory. Only then does the
script rename the old container and start the HTTPS one. A failed check triggers
rollback. It leaves the cold file and volume available for inspection. The old
running H2 copy and its trace file are excluded.

After HTTPS discovery passes, check JWKS key IDs against the old endpoint's
record, confirm browser login and logout, and confirm an API token issued through
HTTPS passes signature, issuer and audience validation. These checks read
configuration and authenticate to the restored realm. Update the private
manifest's issuer and its role-map keys to the exact HTTPS issuer while preserving
the Keycloak subject IDs. Align the frontend issuer and sign out of the old
session before signing in again. These are local configuration and session changes.

CloudOps stores identity as `issuer|subject`. Existing requests, approvals, and
owned resources remain tied to HTTP identities. Review active ownership and
pending work before restarting services, and create fresh requests and approvals
under HTTPS. A historical identity migration requires its own reviewed plan.

## Worker access

On the restored realm, configure a dedicated confidential OIDC client named
`cloudops-ec2-worker`. Enable client authentication and service accounts. Disable
interactive, password, device and other unused grants, and turn off full scope
allowed. In fine-grained admin permissions, grant its service-account user only
the Users `view` scope for the specific engineer and approver accounts. Use a
User policy selecting the service-account user. Leave broad realm-management
roles, user management, impersonation and role-mapping grants unassigned. Assign
the two human accounts the corresponding `engineer` and `approver` realm roles
and matching private manifest entries. These actions change the restored realm.
Provide the client credentials to the worker's existing private environment only.

For Keycloak 26.8.2, add the built-in `kc-admin-api` client scope as a default
scope for the worker. It supplies the required Admin API token audience. The
exact-user `view` permission controls which operations and users are accessible.
See the [26.8.2 upgrade guide](https://www.keycloak.org/docs/26.8.2/upgrading/).

Validate using client credentials with TLS verification enabled. Both
`GET /admin/realms/cloudops/users/{id}` and
`GET /admin/realms/cloudops/users/{id}/role-mappings/realm/composite` must return
200 for each authorized user. Check exact IDs, Boolean `enabled` and the expected
effective realm roles. Both reads must be denied for an out-of-scope account.
Check that write grants are absent, and exercise denied writes only on an isolated
throwaway restore. Keep AWS execution disabled for all checks. The worker requires
both the current realm role and the matching manifest assignment. A failed lookup,
disabled account, missing role or invalid certificate blocks execution.

### Read-only service-account verification

In a private PowerShell session with the two worker credential environment
variables already injected, set the three IDs from the restored admin console.
The commands send a client-credentials request and issue GET requests. Output
contains only a pass message. Use a real third user outside the allowed scope.

```powershell
$engineerId = '<engineer-subject-id>'
$approverId = '<approver-subject-id>'
$outsideId = '<out-of-scope-subject-id>'
$origin = 'https://localhost:8443'
$issuer = "$origin/realms/cloudops"
$admin = "$origin/admin/realms/cloudops/users"
$basic = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(
    "${env:CLOUDOPS_EC2_KEYCLOAK_CLIENT_ID}:${env:CLOUDOPS_EC2_KEYCLOAK_CLIENT_SECRET}"))
try {
    $token = Invoke-RestMethod -Method Post -Uri "$issuer/protocol/openid-connect/token" `
        -Headers @{ Authorization = "Basic $basic" } -MaximumRedirection 0 `
        -ContentType 'application/x-www-form-urlencoded' -Body @{ grant_type = 'client_credentials' }
    $headers = @{ Authorization = "Bearer $($token.access_token)" }
    $expected = @{}
    $expected[$engineerId] = 'engineer'
    $expected[$approverId] = 'approver'
    if ($expected.Count -ne 2 -or $expected.ContainsKey($outsideId)) {
        throw 'Use three distinct user IDs.'
    }
    foreach ($id in $expected.Keys) {
        $user = Invoke-RestMethod "$admin/$id" -Headers $headers -MaximumRedirection 0
        $roles = Invoke-RestMethod "$admin/$id/role-mappings/realm/composite" `
            -Headers $headers -MaximumRedirection 0
        if ($user.id -cne $id -or $user.enabled -isnot [bool] -or -not $user.enabled -or
            -not ($roles | Where-Object { $_.clientRole -eq $false -and $_.name -ceq $expected[$id] })) {
            throw 'Scoped account or role check failed.'
        }
    }
    foreach ($suffix in '', '/role-mappings/realm/composite') {
        $response = Invoke-WebRequest "$admin/$outsideId$suffix" -Headers $headers `
            -MaximumRedirection 0 -SkipHttpErrorCheck
        if ([int]$response.StatusCode -notin 403,404) {
            throw 'The out-of-scope read was not denied.'
        }
    }
    Write-Host 'Scoped account and role reads passed.'
} catch {
    throw 'Service-account verification failed. Check TLS trust and the restored realm permissions.'
} finally {
    Remove-Variable basic,token,headers,user,roles,response -ErrorAction SilentlyContinue
}
```


## Rollback

Pause the CloudOps services first. From the repo root, run:

```powershell
docker compose -p cloudops-keycloak-local -f config/keycloak.compose.yml down
docker rename cloudops-keycloak-http-rollback cloudops-keycloak
docker start cloudops-keycloak
```

These commands remove the HTTPS container, keep the external data volume, and
restore the original container and HTTP listener. Stop if a command fails.
Restore the saved private issuer and role-map settings, check HTTP discovery,
and restart CloudOps with AWS execution disabled. Changes made only in the HTTPS
realm remain on its volume. Keep the backup and original container until validation
and any ownership follow-up are complete.

[Keycloak hostname settings](https://www.keycloak.org/server/hostname) ·
[Offline export](https://www.keycloak.org/server/importExport) ·
[Fine-grained admin permissions](https://www.keycloak.org/docs/26.8.0/server_admin/#_fine_grained_permissions)
