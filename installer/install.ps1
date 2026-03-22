# T042/T043: Guided installer — installer/install.ps1
#
# Steps:
#   1. Verify game path exists
#   2. Check FAF client installed
#   3. Check Ollama running
#   4. Pull qwen3:8b + qwen3:4b-instruct-2507-q4_K_M (with VRAM check)
#   5. Copy mod to %APPDATA%\FAForever\mods\supcom-llm-ai-bot\
#   6. Copy DLL to mod bin folder
#   7. Write config.json
#
# Run from the project root:
#   powershell -ExecutionPolicy Bypass -File installer\install.ps1

param(
    [string]$ConfigPath = "$PSScriptRoot\config.json",
    [switch]$Force
)

$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

function Write-Step($msg) {
    Write-Host "`n[STEP] $msg" -ForegroundColor Cyan
}

function Write-OK($msg) {
    Write-Host "  [OK] $msg" -ForegroundColor Green
}

function Write-Warn($msg) {
    Write-Host "  [WARN] $msg" -ForegroundColor Yellow
}

function Write-Fail($msg) {
    Write-Host "  [FAIL] $msg" -ForegroundColor Red
}

# ---------------------------------------------------------------------------
# T043: GPU VRAM detection
# ---------------------------------------------------------------------------
function Get-GpuVramMB {
    try {
        $result = nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>$null
        if ($result) {
            return [int]($result.Trim().Split([char]0x0a)[0].Trim())
        }
    } catch {}
    return 0
}

# ---------------------------------------------------------------------------
# Load config
# ---------------------------------------------------------------------------

if (-not (Test-Path $ConfigPath)) {
    Write-Fail "Config not found: $ConfigPath"
    exit 1
}
$config = Get-Content $ConfigPath | ConvertFrom-Json
Write-Host "`nSupCom LLM AI Bot Installer v$($config.version)" -ForegroundColor White

# ---------------------------------------------------------------------------
# Step 1: Verify game path
# ---------------------------------------------------------------------------
Write-Step "Verify Supreme Commander game path"
$gamePath = [System.Environment]::ExpandEnvironmentVariables($config.game.game_path)
if (Test-Path "$gamePath\SupremeCommander.exe") {
    Write-OK "Found SupremeCommander.exe at $gamePath"
} elseif (Test-Path "$gamePath\ForgedAlliance.exe") {
    Write-OK "Found ForgedAlliance.exe at $gamePath"
} else {
    Write-Fail "Game not found at: $gamePath"
    Write-Host "  Edit game_path in $ConfigPath and rerun." -ForegroundColor Yellow
    exit 1
}

# ---------------------------------------------------------------------------
# Step 2: Check FAF client
# ---------------------------------------------------------------------------
Write-Step "Check FAF (Forged Alliance Forever) client"
$fafModsPath = [System.Environment]::ExpandEnvironmentVariables($config.game.faf_mods_path)
if (Test-Path $fafModsPath) {
    Write-OK "FAF mods folder found: $fafModsPath"
} else {
    Write-Warn "FAF mods folder not found: $fafModsPath"
    Write-Host "  Is FAF client installed? Attempting to create folder..." -ForegroundColor Yellow
    New-Item -ItemType Directory -Force -Path $fafModsPath | Out-Null
    Write-OK "Created: $fafModsPath"
}

# ---------------------------------------------------------------------------
# Step 3: Check Ollama
# ---------------------------------------------------------------------------
Write-Step "Check Ollama (local LLM server)"
try {
    $ollamaResp = Invoke-WebRequest -Uri "http://localhost:11434/" -TimeoutSec 3 -UseBasicParsing
    Write-OK "Ollama is running on localhost:11434"
} catch {
    Write-Fail "Ollama not running at localhost:11434"
    Write-Host "  Please start Ollama: https://ollama.ai" -ForegroundColor Yellow
    Write-Host "  Then rerun this installer." -ForegroundColor Yellow
    exit 1
}

# ---------------------------------------------------------------------------
# Step 4: VRAM check + pull models
# ---------------------------------------------------------------------------
Write-Step "Detect GPU VRAM and pull LLM models"

$vram_mb = Get-GpuVramMB
$modelDeep = $config.llm.model_deep
$modelFast = $config.llm.model_fast

if ($vram_mb -gt 0) {
    Write-OK "GPU VRAM: ${vram_mb} MB detected"
    if ($vram_mb -lt 6144) {
        Write-Warn "VRAM < 6 GB — disabling deep model (qwen3:8b). Using fast model only."
        $modelDeep = $modelFast
        # Update config
        $config.llm.model_deep = $modelFast
        $config.bot.poll_interval_fast_s = 30
        $config.bot.poll_interval_deep_s = 120
    }
} else {
    Write-Warn "No NVIDIA GPU detected (nvidia-smi not found)."
    Write-Host "  Increasing poll intervals for CPU inference." -ForegroundColor Yellow
    $modelDeep = $modelFast
    $config.llm.model_deep = $modelFast
    $config.bot.poll_interval_fast_s = 30
    $config.bot.poll_interval_deep_s = 120
}

Write-Host "  Pulling $modelFast (fast model, ~2.9 GB VRAM)..." -ForegroundColor Gray
ollama pull $modelFast
Write-OK "Pulled $modelFast"

if ($modelDeep -ne $modelFast) {
    Write-Host "  Pulling $modelDeep (deep model, ~5.0 GB VRAM)..." -ForegroundColor Gray
    ollama pull $modelDeep
    Write-OK "Pulled $modelDeep"
}

# ---------------------------------------------------------------------------
# Step 5: Copy mod to FAF mods folder
# ---------------------------------------------------------------------------
Write-Step "Install mod to FAF mods folder"

$srcMod  = Join-Path $PSScriptRoot "..\mod"
$dstMod  = Join-Path $fafModsPath "supcom-llm-ai-bot"

if (Test-Path $dstMod) {
    if ($Force) {
        Remove-Item $dstMod -Recurse -Force
    } else {
        Write-Warn "Mod already installed at $dstMod. Use -Force to reinstall."
    }
}

Copy-Item -Path $srcMod -Destination $dstMod -Recurse -Force
Write-OK "Mod installed to $dstMod"

# ---------------------------------------------------------------------------
# Step 6: Copy DLL to mod bin folder
# ---------------------------------------------------------------------------
Write-Step "Copy DLL bridge"

$dllSrc = Join-Path $PSScriptRoot "..\bridge\build\supcom_llm_bridge.dll"
$dllDst = Join-Path $dstMod "bin\supcom_llm_bridge.dll"

New-Item -ItemType Directory -Force -Path (Split-Path $dllDst) | Out-Null

if (Test-Path $dllSrc) {
    Copy-Item $dllSrc $dllDst -Force
    Write-OK "DLL copied to $dllDst"
} else {
    Write-Warn "DLL not found at $dllSrc"
    Write-Host "  Build the DLL first: cmake -B bridge/build_cmake -A x64 && cmake --build bridge/build_cmake --config Release" -ForegroundColor Yellow
}

# ---------------------------------------------------------------------------
# Step 7: Write updated config.json
# ---------------------------------------------------------------------------
Write-Step "Write final configuration"

$config | ConvertTo-Json -Depth 10 | Set-Content $ConfigPath -Encoding UTF8
Write-OK "config.json written to $ConfigPath"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
Write-Host "`n===========================================" -ForegroundColor White
Write-Host " Installation complete!" -ForegroundColor Green
Write-Host "===========================================" -ForegroundColor White
Write-Host " Next steps:" -ForegroundColor White
Write-Host "   1. Run: powershell -File installer\start_bridge.ps1" -ForegroundColor Gray
Write-Host "   2. Launch FAF and start a game with 'LLM AI Bot (UEF)' as the AI" -ForegroundColor Gray
Write-Host "   3. Check logs at: $($config.debug.log_path)" -ForegroundColor Gray
