#Requires -Version 5.1
<#
.SYNOPSIS
  Produce runtime/opencode/opencode.exe for LocalWiki (R3.5 U8 / S0 D20).

  1) Prefer building pinned source (packages/opencode --single)
  2) If compile fails, download official Windows x64 zip for the same VERSION tag
#>
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Src = Join-Path $RepoRoot "third_party\opencode"
$OutDir = Join-Path $RepoRoot "runtime\opencode"
$Pkg = Join-Path $Src "packages\opencode"
$VersionFile = Join-Path $RepoRoot "third_party\opencode.VERSION"
$Dest = Join-Path $OutDir "opencode.exe"

function Get-PinnedTag {
  $tag = "v1.19.3"
  if (Test-Path $VersionFile) {
    Get-Content $VersionFile | ForEach-Object {
      if ($_ -match '^\s*tag\s*=\s*(.+)$') { $tag = $Matches[1].Trim() }
    }
  }
  return $tag
}

function Assert-Bun {
  $bun = Get-Command bun -ErrorAction SilentlyContinue
  if (-not $bun) {
    throw "Bun not found. Install from https://bun.sh then re-run."
  }
  Write-Host "[build_opencode] bun=$($bun.Source) version=$(bun --version)"
}

function Install-FromRelease([string]$Tag) {
  $url = "https://github.com/anomalyco/opencode/releases/download/$Tag/opencode-windows-x64.zip"
  $zip = Join-Path $env:TEMP "opencode-$Tag-windows-x64.zip"
  $extract = Join-Path $env:TEMP "opencode-$Tag-extract"
  Write-Host "[build_opencode] fallback: download $url"
  Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
  if (Test-Path $extract) { Remove-Item -Recurse -Force $extract }
  Expand-Archive -Path $zip -DestinationPath $extract -Force
  $bin = Get-ChildItem -Path $extract -Recurse -Include "opencode.exe","opencode" |
    Select-Object -First 1
  if (-not $bin) { throw "Release zip missing opencode binary" }
  New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
  Copy-Item -Force $bin.FullName $Dest
  Write-Host "[build_opencode] installed from release -> $Dest"
}

function Install-FromCompile {
  if (-not (Test-Path (Join-Path $Src "package.json"))) {
    & (Join-Path $PSScriptRoot "fetch_opencode_src.ps1")
  }
  if (-not (Test-Path (Join-Path $Pkg "package.json"))) {
    throw "OpenCode package missing at $Pkg"
  }

  # Ensure node-gyp.cmd is findable for native install scripts
  $npmBin = Join-Path (Split-Path (Get-Command npm).Source) ""
  $env:PATH = "$(npm root -g);$npmBin;$env:PATH"

  Push-Location $Src
  try {
    Write-Host "[build_opencode] bun install"
    bun install
    if ($LASTEXITCODE -ne 0) {
      Write-Warning "bun install failed; retry with --ignore-scripts"
      bun install --ignore-scripts
    }
    Write-Host "[build_opencode] bun run build --single --skip-embed-web-ui"
    bun run --cwd packages/opencode build -- --single --skip-embed-web-ui --skip-install
    if ($LASTEXITCODE -ne 0) { throw "compile exited $LASTEXITCODE" }
  } finally {
    Pop-Location
  }

  $candidates = @(
    (Join-Path $Pkg "dist\opencode-windows-x64\bin\opencode.exe"),
    (Join-Path $Pkg "dist\opencode-windows-x64\bin\opencode")
  )
  $found = $null
  foreach ($c in $candidates) {
    if (Test-Path $c) { $found = $c; break }
  }
  if (-not $found) {
    $found = Get-ChildItem -Path (Join-Path $Pkg "dist") -Recurse -Include "opencode.exe","opencode" -ErrorAction SilentlyContinue |
      Select-Object -First 1 -ExpandProperty FullName
  }
  if (-not $found) { throw "compile produced no binary under dist" }
  New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
  Copy-Item -Force $found $Dest
  Write-Host "[build_opencode] installed from compile -> $Dest"
}

Assert-Bun
$tag = Get-PinnedTag
$compiled = $false
try {
  Install-FromCompile
  $compiled = $true
} catch {
  Write-Warning "[build_opencode] compile failed: $($_.Exception.Message)"
  Install-FromRelease -Tag $tag
}

if (-not (Test-Path $Dest)) { throw "missing $Dest" }
Write-Host "[build_opencode] smoke: $Dest --version"
& $Dest --version
Write-Host "[build_opencode] done (compiled=$compiled tag=$tag)"
