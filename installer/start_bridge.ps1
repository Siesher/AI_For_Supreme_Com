# T044: Bridge launch script -- installer/start_bridge.ps1
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
$config = Get-Content $ConfigPath -Raw | ConvertFrom-Json

# ---------------------------------------------------------------------------
# Resolve engine (mirror server/engine_config.py defaults)
# ---------------------------------------------------------------------------
$engineName = if ($config.llm.engine) { $config.llm.engine } else { "ollama_native" }

$engineDefaults = @{
    koboldcpp     = @{ api_style = "openai"; base_url = "http://localhost:5001/v1" }
    ollama        = @{ api_style = "openai"; base_url = "http://localhost:11434/v1" }
    ollama_native = @{ api_style = "ollama"; base_url = "http://localhost:11434" }
    lmstudio      = @{ api_style = "openai"; base_url = "http://localhost:1234/v1" }
    vllm          = @{ api_style = "openai"; base_url = "http://localhost:8000/v1" }
    tabbyapi      = @{ api_style = "openai"; base_url = "http://localhost:5000/v1" }
}

if ($engineDefaults.ContainsKey($engineName)) {
    $apiStyle = $engineDefaults[$engineName].api_style
    $baseUrl  = $engineDefaults[$engineName].base_url
    # config.llm.engines.<name> overrides the built-in default if present
    if ($config.llm.engines -and $config.llm.engines.$engineName) {
        if ($config.llm.engines.$engineName.api_style) { $apiStyle = $config.llm.engines.$engineName.api_style }
        if ($config.llm.engines.$engineName.base_url)  { $baseUrl  = $config.llm.engines.$engineName.base_url }
    }
} else {
    $apiStyle = "ollama"; $baseUrl = "http://localhost:11434"
}

Write-Step "Inference engine: $engineName (api_style=$apiStyle, $baseUrl)"

# ---------------------------------------------------------------------------
# Engine setup / health check
# ---------------------------------------------------------------------------
if ($apiStyle -eq "ollama") {
    Write-Step "Configuring Ollama environment"
    $kvCacheType = if ($config.llm.kv_cache_type) { $config.llm.kv_cache_type } else { "q8_0" }
    $env:OLLAMA_FLASH_ATTENTION   = "1"
    $env:OLLAMA_KV_CACHE_TYPE     = $kvCacheType
    $env:OLLAMA_NUM_PARALLEL      = "1"
    $env:OLLAMA_GPU_OVERHEAD      = "3221225472"   # 3 GiB reserved for the game
    $env:OLLAMA_KEEP_ALIVE        = "30m"
    $env:OLLAMA_MAX_LOADED_MODELS = "1"
    Write-OK "Flash Attention ON, KV cache $kvCacheType, GPU overhead 3 GiB reserved"

    Write-Step "Check Ollama is running"
    try {
        Invoke-WebRequest -Uri "http://localhost:11434/" -TimeoutSec 3 -UseBasicParsing | Out-Null
        Write-OK "Ollama running"
    } catch {
        Write-Fail "Ollama not running. Start it first (ollama serve)."
        exit 1
    }
} else {
    # OpenAI-compatible engine (KoboldCpp / LM Studio / vLLM / TabbyAPI)
    Write-Step "Check $engineName is serving an OpenAI API"
    try {
        Invoke-WebRequest -Uri "$baseUrl/models" -TimeoutSec 3 -UseBasicParsing | Out-Null
        Write-OK "$engineName reachable at $baseUrl"
    } catch {
        Write-Fail "$engineName not reachable at $baseUrl/models."
        if ($engineName -eq "koboldcpp") {
            Write-Host "  Start it, e.g.:" -ForegroundColor Yellow
            Write-Host "    koboldcpp.exe --model <qwen3.5-14b-q4_k_m.gguf> --usecuda --contextsize 8192 --port 5001" -ForegroundColor Gray
        } elseif ($engineName -eq "lmstudio") {
            Write-Host "  In LM Studio: load a model and start the local server (port 1234), or run: lms server start" -ForegroundColor Yellow
        } elseif ($engineName -eq "vllm") {
            Write-Host "  Start vLLM's OpenAI server on port 8000 (WSL2 build for Blackwell)." -ForegroundColor Yellow
        }
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
$timeout   = 120
$elapsed   = 0

while ($elapsed -lt $timeout) {
    Start-Sleep -Seconds 1
    $elapsed++
    if ($proc.HasExited) {
        Write-Fail "Bridge server exited prematurely (exit code: $($proc.ExitCode))"
        exit 1
    }
    $pipe_exists = [System.IO.Directory]::GetFiles("\\.\pipe\") -contains $pipe_name.Replace("\\.\pipe\","")
    if ($pipe_exists) { break }
    $secs = "$elapsed"
    Write-Host "  Waiting... $secs seconds" -ForegroundColor Gray
}

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
Write-Host "`n===========================================" -ForegroundColor White
Write-Host " Bridge ready! " -ForegroundColor Green
Write-Host "===========================================" -ForegroundColor White
Write-Host " Bridge server PID: $($proc.Id)" -ForegroundColor Gray
Write-Host " Engine: $engineName (api_style=$apiStyle, $baseUrl)" -ForegroundColor Gray
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
