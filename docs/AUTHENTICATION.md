# Local OIDC authentication

CloudOps uses Authorization Code with PKCE through Auth.js in the Next.js
server. The browser receives an encrypted, HttpOnly session cookie. A same-origin
Next.js BFF route reads the API access token on the server and adds it to the
FastAPI request. FastAPI validates the token again with PyJWT and the configured
JWKS, issuer, API audience, expiry, access-token type and fixed algorithms.

Roles are mapped from the verified `issuer|subject` in
`CLOUDOPS_ROLE_ASSIGNMENTS`. Email, display name, browser fields and token role
claims do not grant CloudOps permissions.

## Local Keycloak setup on Windows

Docker is not installed in the current development environment, so these steps
could not be executed here. Install Docker Desktop before running them. The
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

Open `http://localhost:3000`. Sign in as `cloudops-engineer`, create and plan a
request, then request approval. Sign out and sign in as `cloudops-approver` to
review it. The approver cannot create requests, and the engineer cannot approve
their own request. Neither decision executes Terraform.

Stop Keycloak with `docker stop cloudops-keycloak`. Because the container uses
`--rm`, the local realm and its credentials are removed when it stops.
