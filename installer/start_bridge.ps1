# T044: Bridge launch script — installer/start_bridge.ps1
#
# 1. Check Ollama is running and both models are available
# 2. Start bridge_server.py with config path
# 3. Wait for pipe ready message
# 4. Print status

param(
    [string]$ConfigPath = "$PSScriptRoot\config.json",
    [string]$PythonExe  = "python"
)

$ErrorActionPreference = "Stop"

function Write-Step($msg) { Write-Host "`n[STEP] $msg" -ForegroundColor Cyan }
function Write-OK($msg)   { Write-Host "  [OK] $msg"  -ForegroundColor Green }
function Write-Fail($msg) { Write-Host "  [FAIL] $msg" -ForegroundColor Red }

# ---------------------------------------------------------------------------
# Load config
# ---------------------------------------------------------------------------
if (-not (Test-Path $ConfigPath)) {
    Write-Fail "Config not found: $ConfigPath. Run install.ps1 first."
    exit 1
}
$config = Get-Content $ConfigPath | ConvertFrom-Json

# ---------------------------------------------------------------------------
# Step 1: Check Ollama + models
# ---------------------------------------------------------------------------
Write-Step "Check Ollama is running"
try {
    Invoke-WebRequest -Uri "http://localhost:11434/" -TimeoutSec 3 -UseBasicParsing | Out-Null
    Write-OK "Ollama running"
} catch {
    Write-Fail "Ollama not running. Start it first."
    exit 1
}

Write-Step "Ping models via Ollama API"
$modelsToCheck = @($config.llm.model_fast)
if ($config.llm.model_deep -ne $config.llm.model_fast) {
    $modelsToCheck += $config.llm.model_deep
}
foreach ($model in $modelsToCheck) {
    try {
        $body    = @{ model = $model; prompt = "hi"; stream = $false } | ConvertTo-Json
        $resp    = Invoke-WebRequest -Uri "http://localhost:11434/api/generate" `
                       -Method Post -Body $body -ContentType "application/json" `
                       -TimeoutSec 30 -UseBasicParsing
        Write-OK "$model — responding"
    } catch {
        Write-Fail "$model failed to respond. Run: ollama pull $model"
        exit 1
    }
}

# ---------------------------------------------------------------------------
# Step 2: Start bridge_server.py
# ---------------------------------------------------------------------------
Write-Step "Starting bridge server"
$serverScript = Join-Path $PSScriptRoot "..\server\bridge_server.py"
if (-not (Test-Path $serverScript)) {
    Write-Fail "bridge_server.py not found at $serverScript"
    exit 1
}

Write-Host "  Command: $PythonExe $serverScript --config $ConfigPath" -ForegroundColor Gray

$proc = Start-Process -FilePath $PythonExe `
    -ArgumentList "$serverScript --config `"$ConfigPath`"" `
    -PassThru -NoNewWindow

Write-OK "Bridge server started (PID $($proc.Id))"

# ---------------------------------------------------------------------------
# Step 3: Wait for pipe ready
# ---------------------------------------------------------------------------
Write-Step "Waiting for pipe to become ready..."
$pipe_name = $config.bridge.pipe_name
$timeout   = 30  # seconds
$elapsed   = 0

while ($elapsed -lt $timeout) {
    Start-Sleep -Seconds 1
    $elapsed++
    # Check if process is still running
    if ($proc.HasExited) {
        Write-Fail "Bridge server exited prematurely (exit code: $($proc.ExitCode))"
        exit 1
    }
    # Check if named pipe exists
    $pipe_exists = [System.IO.Directory]::GetFiles("\\.\pipe\") -contains $pipe_name.Replace("\\.\pipe\","")
    if ($pipe_exists) { break }
    Write-Host "  Waiting... ($elapsed s)" -ForegroundColor Gray
}

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
Write-Host "`n===========================================" -ForegroundColor White
Write-Host " Bridge ready! " -ForegroundColor Green
Write-Host "===========================================" -ForegroundColor White
Write-Host " Bridge server PID: $($proc.Id)" -ForegroundColor Gray
Write-Host " Models: $($config.llm.model_fast) (fast) / $($config.llm.model_deep) (deep)" -ForegroundColor Gray
Write-Host " Pipe: $pipe_name" -ForegroundColor Gray
Write-Host "`n Start your game in FAF and select 'LLM AI Bot (UEF)' as the AI." -ForegroundColor White
Write-Host " Press Ctrl+C to stop the bridge server." -ForegroundColor Gray

# Keep alive until user stops
try {
    $proc.WaitForExit()
} catch {
    Write-Host "`nStopping bridge server..." -ForegroundColor Yellow
    if (-not $proc.HasExited) { $proc.Kill() }
}
