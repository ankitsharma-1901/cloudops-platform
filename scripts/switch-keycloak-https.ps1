[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$compose = Join-Path $root 'config/keycloak.compose.yml'
$image = 'quay.io/keycloak/keycloak:26.8.2'
$name = 'cloudops-keycloak'
$volume = 'cloudops-keycloak-data-v1'
$original = 'cloudops-keycloak-http-rollback'
$issuer = 'https://localhost:8443/realms/cloudops'

function Invoke-Docker {
    & docker.exe @args
    if ($LASTEXITCODE -ne 0) { throw "Docker failed during $($args[0])." }
}

if ((Invoke-Docker inspect -f '{{.State.Running}} {{.Image}}' $name) -ne
    "true $(Invoke-Docker image inspect -f '{{.Id}}' $image)") {
    throw 'Expected a running legacy container and its locally installed 26.8.2 image.'
}
if ((Invoke-Docker inspect -f '{{json .Config.Cmd}}' $name) -ne '["start-dev","--import-realm"]') {
    throw 'The current startup command does not match the legacy setup.'
}
$mounts = Invoke-Docker inspect -f '{{json .Mounts}}' $name | ConvertFrom-Json
if (@($mounts).Count -ne 1 -or $mounts.Destination -ne '/opt/keycloak/data/import' -or $mounts.RW) {
    throw 'The current mounts do not match the legacy setup.'
}
if ((Invoke-Docker ps -a --format '{{.Names}}') -contains $original) {
    throw 'The rollback container name is already in use.'
}
if ((Invoke-Docker volume ls --format '{{.Name}}') -contains $volume) {
    throw 'The target volume already exists. Review the prior attempt before proceeding.'
}
if (Invoke-Docker ps -a --filter 'label=com.docker.compose.project=cloudops-keycloak-local' --format '{{.Names}}') {
    throw 'An existing container belongs to the cutover project. Review it before proceeding.'
}
foreach ($file in 'localhost.pem', 'localhost-key.pem') {
    if (-not (Test-Path -LiteralPath (Join-Path $root ".private/keycloak/tls/$file") -PathType Leaf)) {
        throw 'Install the local HTTPS certificate and private key before changing Keycloak.'
    }
}
Invoke-Docker compose -p cloudops-keycloak-local -f $compose config --quiet
$backup = Join-Path $root ('.private/backups/keycloak-cold-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $backup | Out-Null
$legacyId = Invoke-Docker inspect -f '{{.Id}}' $name
$renamed = $false

try {
    Write-Host 'Stopping the legacy server cleanly before copying H2.'
    Invoke-Docker stop --time 90 $legacyId | Out-Null
    if ((Invoke-Docker inspect -f '{{.State.Status}} {{.State.ExitCode}}' $legacyId) -notin @('exited 0', 'exited 143')) {
        throw 'Keycloak did not report a normal shutdown. Backup aborted.'
    }

    Write-Host 'Copying the stopped database and saving its SHA-256.'
    $db = Join-Path $backup 'keycloakdb.mv.db'
    Invoke-Docker cp "${legacyId}:/opt/keycloak/data/h2/keycloakdb.mv.db" $db
    if ((Get-Item -LiteralPath $db).Length -le 0) { throw 'Database copy is empty.' }
    $digest = (Get-FileHash -Algorithm SHA256 -LiteralPath $db).Hash
    Set-Content -LiteralPath (Join-Path $backup 'keycloakdb.sha256') -Value $digest -Encoding ASCII

    Write-Host 'Seeding the persistent volume and verifying a restore with Keycloak offline.'
    Invoke-Docker volume create $volume | Out-Null
    $seedCommand = @'
mkdir -p /opt/keycloak/data/h2
cp /backup/keycloakdb.mv.db /opt/keycloak/data/h2/
cd /backup
sha256sum keycloakdb.mv.db > /tmp/keycloak-seed.sha256
cd /opt/keycloak/data/h2
sha256sum -c /tmp/keycloak-seed.sha256 >/dev/null
chown -R 1000:0 /opt/keycloak/data
'@
    $restoreCommand = @'
/opt/keycloak/bin/kc.sh export --db=dev-file --dir=/verify --users=realm_file >/verify/export.log 2>&1
test -s /verify/master-realm.json
test -s /verify/cloudops-realm.json
'@
    Invoke-Docker run --rm --pull never --network none --user 0 --entrypoint /bin/bash `
        --mount "type=bind,source=$backup,target=/backup,readonly" `
        --mount "type=volume,source=$volume,target=/opt/keycloak/data" $image `
        -ec ($seedCommand -replace '\r', '')
    Invoke-Docker run --rm --pull never --network none --entrypoint /bin/bash `
        --tmpfs '/verify:rw,mode=1777' `
        --mount "type=volume,source=$volume,target=/opt/keycloak/data" $image `
        -ec ($restoreCommand -replace '\r', '')
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $db).Hash -ne $digest) {
        throw 'Backup checksum changed.'
    }

    Write-Host 'Verified cold backup. Preserving the legacy container and starting HTTPS.'
    Invoke-Docker rename $legacyId $original
    $renamed = $true
    Invoke-Docker compose -p cloudops-keycloak-local -f $compose up -d
    for ($attempt = 0; $attempt -lt 120; $attempt++) {
        try {
            $discovery = Invoke-RestMethod -Uri "$issuer/.well-known/openid-configuration" -TimeoutSec 3 -MaximumRedirection 0
            if ($discovery.issuer -eq $issuer -and $discovery.jwks_uri -eq "$issuer/protocol/openid-connect/certs") {
                $jwks = Invoke-RestMethod -Uri "$issuer/protocol/openid-connect/certs" -TimeoutSec 3 -MaximumRedirection 0
                if (-not ($jwks.keys | Where-Object { $_.kty -eq 'RSA' -and $_.use -eq 'sig' -and $_.kid })) {
                    throw 'No RSA signing key was returned.'
                }
                Write-Host "HTTPS discovery and signing-key endpoint verified. Cold backup: $backup"
                return
            }
        } catch {
            # The restored server can take time to start. Certificate checks stay enabled.
        }
        Start-Sleep -Seconds 2
    }
    throw 'HTTPS discovery did not return the expected issuer and JWKS URL.'
} catch {
    Write-Warning 'Restoring the legacy HTTP container. The backup and volume are retained for inspection.'
    if ($renamed) {
        Invoke-Docker compose -p cloudops-keycloak-local -f $compose down
        Invoke-Docker rename $legacyId $name
    }
    if ((Invoke-Docker inspect -f '{{.State.Running}}' $legacyId) -ne 'true') {
        Invoke-Docker start $legacyId | Out-Null
    }
    throw
}
