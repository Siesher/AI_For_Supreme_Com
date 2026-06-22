#Requires -Version 5.1
<#
.SYNOPSIS
    Launch KoboldCpp for the SupCom LLM AI bot with FULL GPU offload.

.DESCRIPTION
    The bot's #1 latency killer is KoboldCpp loading most of the 14B model on the
    CPU (VRAM spill) -> ~0.8 tok/s -> every ReAct cycle times out into a noop.
    This script forces ALL layers onto the GPU (--usecublas --gpulayers 999) and
    routes tool calls through Qwen3's native template (--jinja --jinja_tools
    --jinjathink false) for reliable tool_calls with no 'thinking' tokens.

    Sanity target: a 14B Q4_K_M on an RTX 5070 Ti (16 GB) should occupy ~10-12 GB
    of VRAM and run ~30-50 tok/s. If VRAM used stays near ~6 GB and throughput is
    ~1 tok/s, layers are on the CPU -- re-run this script (it forces --gpulayers).

    NOTE: KoboldCpp 1.115.x enables flash attention by DEFAULT (only
    --noflashattention exists). Do NOT pass --flashattention -- argparse rejects
    the unknown flag and the launch fails.

    Pure-ASCII on purpose: Windows PowerShell 5.1 decodes a no-BOM script with the
    legacy ANSI/OEM codepage and mangles multibyte UTF-8, so keep this file ASCII.

.PARAMETER InstallDir   koboldcpp.exe + models live here. Default: C:\koboldcpp
.PARAMETER ContextSize  KV context length. Default: 8192 (drop to 4096 if VRAM tight)
.PARAMETER Port         OpenAI API port. Default: 5001
.PARAMETER Restart      Kill any running koboldcpp.exe before launching.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File installer/serve_koboldcpp.ps1 -Restart
#>
[CmdletBinding()]
param(
    [string]$InstallDir = "C:\koboldcpp",
    [int]$ContextSize   = 8192,
    [int]$Port          = 5001,
    [switch]$Restart
)

$ErrorActionPreference = "Stop"
$KoboldExe = Join-Path $InstallDir "koboldcpp.exe"
$ModelDir  = Join-Path $InstallDir "models"

if (-not (Test-Path $KoboldExe)) {
    throw "koboldcpp.exe not found at $KoboldExe. Run installer\download_koboldcpp.ps1 first."
}
$gguf = Get-ChildItem -Path $ModelDir -Recurse -Filter *.gguf -ErrorAction SilentlyContinue |
    Sort-Object Length -Descending | Select-Object -First 1
if (-not $gguf) {
    throw "No .gguf found under $ModelDir. Run installer\download_koboldcpp.ps1 first."
}

if ($Restart) {
    Get-Process koboldcpp -ErrorAction SilentlyContinue | ForEach-Object {
        Write-Host "==> Stopping existing koboldcpp PID $($_.Id)"
        Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
}

# --gpulayers 999 = offload every layer (model is ~40 layers); KoboldCpp clamps to
# the real count. --jinja_tools needs jinja templating, so --jinja is explicit.
$koboldArgs = @(
    "--model", $gguf.FullName,
    "--usecublas",
    "--gpulayers", "999",
    "--contextsize", "$ContextSize",
    "--jinja", "--jinja_tools", "--jinjathink", "false",
    "--skiplauncher", "--quiet",
    "--port", "$Port"
)

Write-Host "==> KoboldCpp : $KoboldExe"
Write-Host ("==> Model     : {0} ({1:N1} GB)" -f $gguf.FullName, ($gguf.Length / 1GB))
Write-Host ("==> Launching : {0} {1}" -f (Split-Path $KoboldExe -Leaf), ($koboldArgs -join " "))
$proc = Start-Process -FilePath $KoboldExe -ArgumentList $koboldArgs -PassThru
Write-Host "==> koboldcpp PID $($proc.Id) starting; waiting for :$Port/v1 (model load can take ~30-60s)..."

$base  = "http://localhost:$Port"
$ready = $false
for ($i = 0; $i -lt 120; $i++) {
    Start-Sleep -Seconds 2
    try {
        $v = Invoke-RestMethod -Uri "$base/api/extra/version" -TimeoutSec 3
        if ($v.llm) { $ready = $true; break }
    } catch { }
}

if ($ready) {
    Write-Host "==> READY: OpenAI API live at $base/v1 (model: $($gguf.Name))"
    Write-Host "    Next: powershell -File installer\start_bridge.ps1"
} else {
    Write-Host "==> WARNING: KoboldCpp did not report ready within 240s. Check its console window."
}
