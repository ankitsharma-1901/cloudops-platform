[CmdletBinding()]
param(
    [Parameter(Position=0, Mandatory=$true)]
    [ValidateSet('start','stop','restart','status','logs','clean-logs','daily-checkpoint')]
    [string]$Action,
    [Parameter(Position=1)]
    [ValidateSet('api','worker','frontend','keycloak')]
    [string]$Service
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$logRoot = Join-Path $root '.cloudops-logs'
$python = Join-Path $root 'backend\.venv\Scripts\python.exe'
$supervisor = Join-Path $PSScriptRoot 'cloudops_supervisor.py'
$nodeDir = Join-Path $root '.tools\node-v26.13.1-win-x64'
$services = @('api','worker','frontend')
$mutex = New-Object System.Threading.Mutex($false, 'Local\CloudOpsLauncher')
if (-not $mutex.WaitOne(10000)) { throw 'Another CloudOps launcher operation is in progress.' }

function Write-ProgressMessage($level, $message) {
    $colors = @{ INFO = 'Cyan'; CHECK = 'Cyan'; START = 'Yellow'; OK = 'Green'; WARN = 'Yellow'; ERROR = 'Red'; READY = 'Green' }
    Write-Host ("{0} [{1}] {2}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $level, $message) -ForegroundColor $colors[$level]
}
function Write-StartupError {
    $hints = @{
        'Existing services' = 'Run status and stop existing managed services before retrying.'
        'Private manifest and AWS safety switches' = 'Check the private manifest and keep both AWS mutation switches false.'
        'Local prerequisites' = 'Check frontend/.env.local and whether ports 8000 and 3000 are available.'
        'Docker' = 'Check that the Docker CLI is installed and on PATH.'
        'Keycloak' = 'Check Docker, the existing container, and logs keycloak.'
        'FastAPI' = 'Check logs api and the backend configuration.'
        'Worker' = 'Check logs worker and the backend service agreement.'
        'Next.js frontend' = 'Check logs frontend and local frontend prerequisites.'
        'Final process verification' = 'Run status and check service logs for an exited process.'
    }
    Write-ProgressMessage ERROR ("{0} startup failed. {1}" -f $script:startupPhase, $hints[$script:startupPhase])
}
function Get-ManagedProcess($name) {
    $path = Join-Path $logRoot "$name\process.json"
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    try {
        $identity = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
        $process = Get-Process -Id ([int]$identity.pid) -ErrorAction Stop
        if ($process.StartTime.ToUniversalTime().Ticks -ne [long]$identity.started) { return $null }
        return $process
    } catch { return $null }
}
function Wait-Http($url, $seconds, $name) {
    Write-Verbose "$name readiness check has a $seconds-second timeout."
    $deadline = (Get-Date).AddSeconds($seconds)
    do {
        try { $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2 -Verbose:$false; if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 400) { return } } catch { }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)
    throw "$name did not become ready within $seconds seconds. Check its logs."
}
function Start-ServiceProcess($name, $workingDirectory, $arguments) {
    $directory = Join-Path $logRoot $name
    New-Item -ItemType Directory -Force -Path $directory | Out-Null
    foreach ($stream in @('stdout.log','stderr.log')) {
        $base = Join-Path $directory $stream
        $oldest = "$base.2"; $older = "$base.1"
        if (Test-Path -LiteralPath $oldest) { Remove-Item -LiteralPath $oldest -Force }
        if (Test-Path -LiteralPath $older) { Move-Item -LiteralPath $older -Destination $oldest -Force }
        if (Test-Path -LiteralPath $base) { Move-Item -LiteralPath $base -Destination $older -Force }
    }
    $stop = Join-Path $directory 'stop.request'
    if (Test-Path -LiteralPath $stop) { Remove-Item -LiteralPath $stop -Force }
    $argv = @('"' + $supervisor + '"', '"' + $directory + '"') + $arguments
    $process = Start-Process -FilePath $python -ArgumentList ($argv -join ' ') -WorkingDirectory $workingDirectory -WindowStyle Hidden -PassThru
    $identity = @{ pid = $process.Id; started = $process.StartTime.ToUniversalTime().Ticks }
    $identity | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $directory 'process.json') -Encoding ASCII
}
function Stop-All {
    foreach ($name in @('frontend','worker','api')) {
        $process = Get-ManagedProcess $name
        if ($process) {
            Set-Content -LiteralPath (Join-Path $logRoot "$name\stop.request") -Value 'stop' -Encoding ASCII
            if (-not $process.WaitForExit(35000)) { throw "$name did not stop; inspect its process before retrying." }
            Write-Host "$name stopped"
        }
    }
    $marker = Join-Path $logRoot 'keycloak.started'
    if (Test-Path -LiteralPath $marker) {
        $identity = Get-Content -LiteralPath $marker -Raw
        $current = & docker inspect --format '{{.Id}} {{.State.StartedAt}}' cloudops-keycloak
        if ($LASTEXITCODE -ne 0 -or $current -ne $identity.Trim()) { throw 'Keycloak identity changed since startup; refusing to stop it.' }
        & docker stop --time 15 cloudops-keycloak | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Could not stop the Keycloak container.' }
        Remove-Item -LiteralPath $marker -Force
        Write-Host 'Keycloak stopped'
    }
}
function Start-All {
    $script:startupPhase = 'Existing services'
    Write-ProgressMessage INFO 'Starting CloudOps local services.'
    Write-ProgressMessage CHECK 'Checking for already managed services.'
    foreach ($name in $services) { if (Get-ManagedProcess $name) { throw "$name is already managed and running; use status or stop." } }
    $script:startupPhase = 'Private manifest and AWS safety switches'
    Write-ProgressMessage CHECK 'Checking the private manifest and AWS safety switches.'
    $configPath = Join-Path $root '.cloudops.local.json'
    $config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
    if ($config.enable_aws_apply -isnot [bool] -or $config.enable_aws_apply -or ($null -ne $config.enable_ec2_demo -and ($config.enable_ec2_demo -isnot [bool] -or $config.enable_ec2_demo))) { throw 'Both AWS apply and EC2 demo must be disabled in the private configuration.' }
    Write-ProgressMessage OK 'AWS apply and EC2 demo are disabled.'
    Write-Verbose 'Manifest safety flags checked; backend performs its own full validation.'
    $script:startupPhase = 'Local prerequisites'
    if (-not (Test-Path -LiteralPath (Join-Path $root 'frontend\.env.local'))) { throw 'Missing frontend/.env.local.' }
    foreach ($port in 8000,3000) { if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue -Verbose:$false) { throw "Port $port is already in use; stop the existing service first." } }
    $script:startupPhase = 'Docker'
    Write-ProgressMessage CHECK 'Checking Docker availability.'
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Docker CLI is unavailable; install or start Docker and ensure docker is on PATH.' }
    Write-ProgressMessage OK 'Docker CLI is available.'
    $script:startupOwned = $true
    New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
    $script:startupPhase = 'Keycloak'
    Write-ProgressMessage START 'Starting the existing Keycloak container if needed.'
    $state = & docker inspect --format '{{.State.Running}}' cloudops-keycloak 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'Docker or the existing cloudops-keycloak container is unavailable.' }
    if ($state -ne 'true') {
        & docker start cloudops-keycloak | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Could not start Keycloak.' }
        $identity = & docker inspect --format '{{.Id}} {{.State.StartedAt}}' cloudops-keycloak
        if ($LASTEXITCODE -ne 0) { throw 'Could not verify the Keycloak container identity.' }
        Set-Content -LiteralPath (Join-Path $logRoot 'keycloak.started') -Value $identity -Encoding ASCII
    }
    else { Write-Verbose 'Keycloak was already running and will not be stopped by this startup.' }
    Write-ProgressMessage CHECK 'Checking Keycloak readiness.'
    Wait-Http ($config.oidc_issuer + '/.well-known/openid-configuration') 90 'Keycloak'
    Write-ProgressMessage OK 'Keycloak is ready.'
    $saved = @{}
    foreach ($item in Get-ChildItem Env:) {
        if ($item.Name -match '^(AUTH_|CLOUDOPS_|OIDC_)' -or $item.Name -eq 'DATABASE_URL') {
            $saved[$item.Name] = $item.Value
            [Environment]::SetEnvironmentVariable($item.Name, $null, 'Process')
        }
    }
    $oldPath = $env:Path
    try {
        $env:Path = "$nodeDir;$oldPath"
        $backend = Join-Path $root 'backend'
        $backendArgs = @('"' + $python + '"', '-u', '-m', 'app.local_start', '--config', '"' + $configPath + '"')
        $script:startupPhase = 'FastAPI'
        Write-ProgressMessage START 'Starting FastAPI in the background.'
        Start-ServiceProcess 'api' $backend ($backendArgs + 'api')
        Write-ProgressMessage CHECK 'Checking API readiness.'
        Wait-Http 'http://127.0.0.1:8000/health' 45 'API'
        Write-ProgressMessage OK 'API health check passed.'
        $script:startupPhase = 'Worker'
        Write-ProgressMessage START 'Starting the worker in the background.'
        Start-ServiceProcess 'worker' $backend ($backendArgs + 'worker')
        Write-ProgressMessage CHECK 'Checking worker process and service-lock initialization.'
        Write-Verbose 'Waiting up to 30 seconds for the worker configuration and lock confirmation.'
        $deadline = (Get-Date).AddSeconds(30)
        $workerLog = Join-Path $logRoot 'worker\stderr.log'
        do {
            if (-not (Get-ManagedProcess 'worker')) { throw 'Worker exited during startup; check its logs.' }
            if ((Test-Path -LiteralPath $workerLog) -and (Select-String -LiteralPath $workerLog -Pattern 'Validated service configuration fingerprint=' -Quiet)) { break }
            Start-Sleep -Milliseconds 500
        } while ((Get-Date) -lt $deadline)
        if ((Get-Date) -ge $deadline) { throw 'Worker did not complete configuration within 30 seconds; check its logs.' }
        Write-ProgressMessage OK 'Worker process is running and reported service-lock initialization.'
        $env:CLOUDOPS_ROLE_ASSIGNMENTS = $config.roles | ConvertTo-Json -Compress -Depth 10
        $env:AUTH_ISSUER = $config.oidc_issuer
        $env:AUTH_URL = 'http://localhost:3000'
        $env:AUTH_CLIENT_ID = 'cloudops-web'
        $env:CLOUDOPS_API_URL = 'http://127.0.0.1:8000'
        $node = Join-Path $nodeDir 'node.exe'
        $npm = Join-Path $nodeDir 'node_modules\npm\bin\npm-cli.js'
        $script:startupPhase = 'Next.js frontend'
        Write-ProgressMessage START 'Starting Next.js in the background.'
        Start-ServiceProcess 'frontend' (Join-Path $root 'frontend') @('"' + $node + '"', '"' + $npm + '"', 'run', 'dev')
        Write-ProgressMessage CHECK 'Checking frontend readiness.'
        Wait-Http 'http://localhost:3000' 90 'Frontend'
        Write-ProgressMessage OK 'Frontend HTTP check passed.'
        $script:startupPhase = 'Final process verification'
        foreach ($name in $services) { if (-not (Get-ManagedProcess $name)) { throw "$name exited during startup." } }
        Write-ProgressMessage OK 'API, worker and frontend supervisors are running; Keycloak readiness passed.'
        Write-ProgressMessage READY 'CloudOps startup checks passed. Website: http://localhost:3000'
    } finally {
        $env:Path = $oldPath
        foreach ($item in Get-ChildItem Env:) { if ($item.Name -match '^(AUTH_|CLOUDOPS_|OIDC_)' -or $item.Name -eq 'DATABASE_URL') { [Environment]::SetEnvironmentVariable($item.Name, $null, 'Process') } }
        foreach ($key in $saved.Keys) { [Environment]::SetEnvironmentVariable($key, $saved[$key], 'Process') }
    }
}
function Show-Status {
    foreach ($name in $services) { if (Get-ManagedProcess $name) { Write-Host "$name : running (supervisor)" } else { Write-Host "$name : stopped or unmanaged" } }
    if (Get-Command docker -ErrorAction SilentlyContinue) {
        & docker inspect --format 'keycloak : {{.State.Status}}' cloudops-keycloak
        if ($LASTEXITCODE -ne 0) { Write-Host 'keycloak : unavailable' }
    } else { Write-Host 'keycloak : Docker CLI unavailable' }
    foreach ($entry in @(@('API','http://127.0.0.1:8000/health'), @('Frontend','http://localhost:3000'))) {
        try { $response = Invoke-WebRequest -Uri $entry[1] -UseBasicParsing -TimeoutSec 3; Write-Host "$($entry[0]) HTTP: $($response.StatusCode)" } catch { Write-Host "$($entry[0]) HTTP: unavailable" }
    }
}
function Clean-Logs {
    foreach ($name in $services) { if (Get-ManagedProcess $name) { throw "Stop $name before cleaning logs." } }
    foreach ($name in $services) {
        $directory = Join-Path $logRoot $name
        if (Test-Path -LiteralPath $directory) { Get-ChildItem -LiteralPath $directory -File | Where-Object { $_.Name -match '^(stdout|stderr)\.log(\.[12])?$' } | Remove-Item -Force }
    }
    Write-Host 'Stopped-service logs cleaned. Docker manages Keycloak logs.'
}
try {
    switch ($Action) {
        'start' { try { Start-All } catch { Write-StartupError; if ($script:startupOwned) { Write-ProgressMessage WARN 'Stopping services started by this launcher.'; Stop-All }; throw 'CloudOps startup failed; see the service-specific message above.' } }
        'stop' { Stop-All }
        'restart' { Stop-All; try { Start-All } catch { Write-StartupError; if ($script:startupOwned) { Write-ProgressMessage WARN 'Stopping services started by this launcher.'; Stop-All }; throw 'CloudOps startup failed; see the service-specific message above.' } }
        'status' { Show-Status }
        'clean-logs' { Clean-Logs }
        'daily-checkpoint' { Stop-All; Clean-Logs; & git -C $root diff --check; if ($LASTEXITCODE -ne 0) { throw 'Git diff check failed.' }; & git -C $root status --short --branch }
        'logs' {
            if (-not $Service) { throw 'Specify api, worker, frontend or keycloak.' }
            $mutex.ReleaseMutex(); $mutex.Dispose(); $mutex = $null
            if ($Service -eq 'keycloak') { & docker logs --timestamps --follow cloudops-keycloak }
            else {
                $directory = Join-Path $logRoot $Service
                if (-not (Test-Path -LiteralPath $directory)) { throw 'No logs exist for that service.' }
                $files = @((Join-Path $directory 'stdout.log'), (Join-Path $directory 'stderr.log')) | Where-Object { Test-Path -LiteralPath $_ }
                if (-not $files) { throw 'No output has been written yet.' }
                Get-Content -Path $files -Tail 50 -Wait
            }
        }
    }
} finally { if ($mutex) { $mutex.ReleaseMutex(); $mutex.Dispose() } }
