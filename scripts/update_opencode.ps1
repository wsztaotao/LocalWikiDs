# Explicitly pin / refresh OpenCode (R3.8). Never silent @latest.
param(
  [Parameter(Mandatory = $true)][string]$Tag,
  [string]$Sha = ""
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$VersionFile = Join-Path $Root "third_party\opencode.VERSION"
Write-Host "[LocalWiki] Pin OpenCode tag=$Tag sha=$Sha"
Write-Host "[LocalWiki] Prefer: scripts\build_opencode.ps1 for this tag, then edit VERSION."
@"
# OpenCode pin for LocalWiki same-package (R3.8).
# Never track @latest — bump deliberately.
tag=$Tag
sha=$Sha
note=Updated via scripts/update_opencode.ps1; rebuild runtime with build_opencode.ps1
"@ | Set-Content -Path $VersionFile -Encoding utf8
Write-Host "[LocalWiki] Wrote $VersionFile"
Write-Host "[LocalWiki] Next: .\scripts\build_opencode.ps1  (or download official zip for $Tag)"
