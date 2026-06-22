#Requires -Version 5.1
<#
.SYNOPSIS
    Deploy the LLM AI Bot sim + UI mods from this repo into the FAF vault.

.DESCRIPTION
    FAF discovers mods under its vault's \mods\ folder. This copies:
        repo\mod\     -> <vault>\mods\supcom-llm-ai-bot\       (SIM mod, the AI)
        repo\mod-ui\  -> <vault>\mods\supcom-llm-ai-bot-ui\    (UI mod, lobby dropdown)
    using robocopy /MIR (mirror) so removed source files are cleaned up, but with
    /XF cmd_*.lua so the bridge's runtime command files (<sim mod>\ipc\cmd_NNNN.lua)
    are preserved. The ipc\ dir itself IS deployed (it ships a committed keep.lua):
    SupCom's VFS indexes a mod's dirs at mount time, so ipc\ must exist and be
    non-empty at mount or DiskGetFileInfo never sees the runtime command files.

    Vault MUST be an ASCII-only path NOT under OneDrive (the 2007 engine cannot
    handle Cyrillic or OneDrive reparse points). Default: C:\FAFData.

    This file is pure ASCII so Windows PowerShell 5.1 parses it under any codepage.

.PARAMETER VaultMods   The vault's mods folder. Default: C:\FAFData\mods
#>
[CmdletBinding()]
param(
    [string]$VaultMods = "C:\FAFData\mods"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot   # installer\ -> repo root

$pairs = @(
    @{ Src = (Join-Path $RepoRoot "mod");    Dst = (Join-Path $VaultMods "supcom-llm-ai-bot") },
    @{ Src = (Join-Path $RepoRoot "mod-ui"); Dst = (Join-Path $VaultMods "supcom-llm-ai-bot-ui") }
)

foreach ($p in $pairs) {
    if (-not (Test-Path $p.Src)) { throw "Source missing: $($p.Src)" }
    New-Item -ItemType Directory -Force -Path $p.Dst | Out-Null
    Write-Host "==> $($p.Src)  ->  $($p.Dst)"
    # /MIR = mirror; /XF cmd_*.lua keeps runtime File-IPC command files (excluded
    # from both copy and purge), so ipc\keep.lua deploys while cmd_NNNN.lua survive.
    robocopy $p.Src $p.Dst /MIR /XF cmd_*.lua /NFL /NDL /NJH /NJS /NP /R:1 /W:1 | Out-Null
    $rc = $LASTEXITCODE
    if ($rc -ge 8) { throw "robocopy failed (exit $rc) for $($p.Src)" }
    Write-Host "    robocopy OK (exit code $rc)"
}

Write-Host ""
Write-Host "Deployed. In FAF -> Create Game -> Mods: click Reload, then enable BOTH:"
Write-Host "  [SIM] LLM AI Bot (UEF)"
Write-Host "  [UI]  LLM AI Bot (UEF) - UI"
Write-Host "Then the AI appears in a player slot's dropdown."
