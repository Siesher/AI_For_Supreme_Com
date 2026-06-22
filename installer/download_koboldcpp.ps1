#Requires -Version 5.1
<#
.SYNOPSIS
    Download KoboldCpp + a Qwen3-14B Q4_K_M GGUF for the SupCom LLM AI bot, using aria2c.

.DESCRIPTION
    Native-Windows inference path (no WSL2): KoboldCpp is a single .exe built on
    llama.cpp and serves an OpenAI-compatible API at http://localhost:5001/v1 --
    exactly what the bot's OpenAIClient (llm.engine = "koboldcpp", the default) uses.

    NOTE: KoboldCpp loads GGUF, NOT NVFP4. GGUF Q4_K_M is its 4-bit format. For a
    bot that decides once every 20-60 s, token throughput is not the bottleneck,
    so GGUF on native Windows beats the NVFP4/vLLM/WSL2 path on simplicity.

    Downloads (~9 GB GGUF + ~0.7 GB koboldcpp.exe) run through aria2c with 16
    parallel segments and resume (-c), so an interrupted run continues where it
    left off. The GGUF filename is resolved live from the HF API, so swapping
    -ModelRepo / -ModelGlob keeps working.

    This file is intentionally pure-ASCII and saved with a UTF-8 BOM. Windows
    PowerShell 5.1 decodes a no-BOM script with the legacy ANSI/OEM codepage,
    which mangles multibyte UTF-8 punctuation and breaks parsing -- so keep it ASCII.

.PARAMETER InstallDir   Where koboldcpp.exe + the model live. Default: C:\koboldcpp
.PARAMETER ModelRepo    HF GGUF repo. Default: unsloth/Qwen3-14B-GGUF (alt: bartowski/Qwen_Qwen3-14B-GGUF)
.PARAMETER ModelGlob    Quant file filter (-like). Default: *Q4_K_M*.gguf
.PARAMETER Port         KoboldCpp API port. Default: 5001
.PARAMETER BinaryOnly   Download only koboldcpp.exe (run with VPN ON). Default: off
.PARAMETER SkipBinary   Download only the GGUF from HuggingFace (run with VPN OFF). Default: off

.EXAMPLE
    # Default: koboldcpp.exe + the GGUF in one go.
    powershell -ExecutionPolicy Bypass -File installer/download_koboldcpp.ps1

.EXAMPLE
    # Behind an ISP that resets GitHub's release CDN (release-assets.githubusercontent.com):
    # grab the binary with a VPN on, then the model with the VPN off (HuggingFace is not blocked).
    #   VPN ON:
    powershell -ExecutionPolicy Bypass -File installer/download_koboldcpp.ps1 -BinaryOnly
    #   VPN OFF:
    powershell -ExecutionPolicy Bypass -File installer/download_koboldcpp.ps1 -SkipBinary
#>
[CmdletBinding()]
param(
    [string]$InstallDir = "C:\koboldcpp",
    [string]$ModelRepo  = "unsloth/Qwen3-14B-GGUF",
    [string]$ModelGlob  = "*Q4_K_M*.gguf",
    [int]$Port          = 5001,
    [switch]$BinaryOnly,    # download only koboldcpp.exe (run with VPN ON)
    [switch]$SkipBinary     # download only the GGUF from HF (run with VPN OFF)
)

$ErrorActionPreference = "Stop"
$ModelDir  = Join-Path $InstallDir "models"
$KoboldExe = Join-Path $InstallDir "koboldcpp.exe"

# --- Locate (or install) aria2c ------------------------------------------------
function Resolve-Aria2 {
    $c = (Get-Command aria2c -ErrorAction SilentlyContinue).Source
    if ($c) { return $c }

    Write-Host "==> aria2c not found - installing via winget..."
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install --id aria2.aria2 -e --silent `
            --accept-source-agreements --accept-package-agreements | Out-Null
    }
    $c = (Get-Command aria2c -ErrorAction SilentlyContinue).Source
    if ($c) { return $c }
    # winget puts a shim here but may not refresh PATH in this session:
    $shim = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links\aria2c.exe"
    if (Test-Path $shim) { return $shim }

    throw "aria2c unavailable. Install it (e.g. 'winget install aria2.aria2', 'scoop install aria2', or 'choco install aria2') and re-run."
}

# --- Download one URL with aria2c (16-way, resumable) --------------------------
function Invoke-Aria2 {
    param([string]$Url, [string]$Dir, [string]$OutName, [string[]]$ExtraArgs = @())
    $a = @(
        "-x16", "-s16", "-k1M", "-c",
        "--file-allocation=none", "--console-log-level=warn", "--summary-interval=10",
        "-d", $Dir, "-o", $OutName
    ) + $ExtraArgs + @($Url)
    & $script:Aria @a
    if ($LASTEXITCODE -ne 0) { throw "aria2c failed (exit $LASTEXITCODE) for $Url" }
}

$script:Aria = Resolve-Aria2
Write-Host "==> aria2c: $script:Aria"
Write-Host "==> Install dir: $InstallDir"
New-Item -ItemType Directory -Force -Path $ModelDir | Out-Null

# --- 1. KoboldCpp binary (latest CUDA build; supports Blackwell sm_120) --------
# GitHub serves release assets from release-assets.githubusercontent.com, which
# some ISPs (e.g. RU DPI) reset at the TLS handshake. If aria2c fails here, run
# this step with a VPN on: -BinaryOnly fetches just this ~700 MB file; then run
# -SkipBinary (VPN off) for the model, since HuggingFace is not blocked.
if ($SkipBinary) {
    Write-Host "==> -SkipBinary: leaving koboldcpp.exe alone."
} elseif (Test-Path $KoboldExe) {
    Write-Host "==> koboldcpp.exe already present - skipping."
} else {
    Write-Host "==> Downloading KoboldCpp (latest release)..."
    Invoke-Aria2 -Url "https://github.com/LostRuins/koboldcpp/releases/latest/download/koboldcpp.exe" `
                 -Dir $InstallDir -OutName "koboldcpp.exe"
}

if ($BinaryOnly) {
    Write-Host ""
    Write-Host "==> -BinaryOnly done. koboldcpp.exe: $KoboldExe"
    Write-Host "    Now turn the VPN OFF and fetch the model from HuggingFace:"
    Write-Host "      powershell -ExecutionPolicy Bypass -File installer\download_koboldcpp.ps1 -SkipBinary"
    return
}

# --- 2. Resolve the GGUF filename(s) from the HF API ---------------------------
Write-Host "==> Resolving '$ModelGlob' in $ModelRepo..."
$meta = Invoke-RestMethod -Uri "https://huggingface.co/api/models/$ModelRepo"
$targets = @($meta.siblings.rfilename | Where-Object { $_ -like $ModelGlob })
if ($targets.Count -eq 0) { throw "No file matching '$ModelGlob' in $ModelRepo." }
Write-Host ("==> Will fetch: {0}" -f ($targets -join ", "))

# Optional auth header - repo is ungated, but HF_TOKEN avoids anonymous rate limits.
$auth = @()
if ($env:HF_TOKEN) { $auth = @("--header=Authorization: Bearer $($env:HF_TOKEN)") }

# --- 3. Download each shard with aria2c ----------------------------------------
foreach ($f in $targets) {
    $url = "https://huggingface.co/$ModelRepo/resolve/main/$f`?download=true"
    $outName = Split-Path $f -Leaf
    $outDir  = $ModelDir
    if ($f -match "/") { $outDir = Join-Path $ModelDir (Split-Path $f -Parent) }
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null
    Write-Host "==> $f"
    Invoke-Aria2 -Url $url -Dir $outDir -OutName $outName -ExtraArgs $auth
}

# --- 4. Locate the GGUF and print the launch command ---------------------------
$gguf = Get-ChildItem -Path $ModelDir -Recurse -Filter *.gguf | Select-Object -First 1
if (-not $gguf) { throw "No .gguf found under $ModelDir after download." }

Write-Host ""
Write-Host "==> Done."
if (-not (Test-Path $KoboldExe)) {
    Write-Host "    NOTE: koboldcpp.exe not present yet - run with -BinaryOnly (VPN on) to fetch it."
}
Write-Host ("    KoboldCpp: {0}" -f $KoboldExe)
Write-Host ("    Model:     {0} ({1:N1} GB)" -f $gguf.FullName, ($gguf.Length / 1GB))
Write-Host ""
Write-Host "Start the engine (serves OpenAI API on :$Port/v1) -- forces FULL GPU offload:"
Write-Host "  powershell -ExecutionPolicy Bypass -File installer\serve_koboldcpp.ps1 -Restart"
Write-Host "  (equivalently, the raw command:)"
Write-Host ("  & `"{0}`" --model `"{1}`" --usecublas --gpulayers 999 --contextsize 4096 --jinja --jinja_tools --jinjathink false --skiplauncher --port {2}" -f $KoboldExe, $gguf.FullName, $Port)
Write-Host "  (--gpulayers 999 forces ALL layers onto the GPU = the bot's #1 latency fix;"
Write-Host "   --jinja_tools routes tool calls through Qwen3's native template = reliable tool_calls;"
Write-Host "   --jinjathink false disables Qwen3 'thinking'. Do NOT pass --flashattention -- it is"
Write-Host "   default-on in KoboldCpp 1.115.x and argparse rejects the unknown flag.)"
Write-Host ""
Write-Host "Then (config already defaults to engine=koboldcpp):"
Write-Host "  .\.venv\Scripts\python.exe tests\integration\test_openai_live.py   # smoke-test the client"
Write-Host "  powershell -File installer/start_bridge.ps1"
