#Requires -Version 5.1
<#
.SYNOPSIS
  Fetch pinned OpenCode source into third_party/opencode (R3.5 Q5A/Q6A).
#>
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$VersionFile = Join-Path $RepoRoot "third_party\opencode.VERSION"
$Dest = Join-Path $RepoRoot "third_party\opencode"

if (-not (Test-Path $VersionFile)) {
  throw "Missing $VersionFile — pin tag= and sha= first"
}

$tag = ""
$sha = ""
Get-Content $VersionFile | ForEach-Object {
  if ($_ -match '^\s*tag\s*=\s*(.+)$') { $tag = $Matches[1].Trim() }
  if ($_ -match '^\s*sha\s*=\s*(.+)$') { $sha = $Matches[1].Trim() }
}
if (-not $tag -or $tag -eq "PLACEHOLDER") {
  throw "Set a real tag= in third_party/opencode.VERSION before fetch"
}

$RepoUrl = "https://github.com/anomalyco/opencode.git"
Write-Host "[fetch_opencode] tag=$tag dest=$Dest"

if (-not (Test-Path (Join-Path $Dest ".git"))) {
  New-Item -ItemType Directory -Force -Path (Split-Path $Dest) | Out-Null
  if (Test-Path $Dest) { Remove-Item -Recurse -Force $Dest }
  git clone --depth 1 --branch $tag $RepoUrl $Dest
} else {
  Push-Location $Dest
  try {
    git fetch --tags --depth 1 origin $tag
    git checkout $tag
  } finally {
    Pop-Location
  }
}

if ($sha -and $sha -notmatch '^0+$') {
  Push-Location $Dest
  try {
    $head = (git rev-parse HEAD).Trim()
    if (-not $head.StartsWith($sha) -and $head -ne $sha) {
      Write-Warning "HEAD $head does not match pinned sha=$sha (continuing; update VERSION if intentional)"
    }
  } finally {
    Pop-Location
  }
}

Write-Host "[fetch_opencode] done"
