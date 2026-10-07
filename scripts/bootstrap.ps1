param([switch]$SourceSqlAlchemy)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot

function Assert-Exit($label) {
  if ($LASTEXITCODE -ne 0) { throw "$label failed (exit $LASTEXITCODE)" }
}

if (-not (Test-Path -LiteralPath '.bootstrap\Scripts\python.exe')) {
  python -m venv .bootstrap
  Assert-Exit 'Bootstrap environment creation'
}
& '.\.bootstrap\Scripts\python.exe' -m pip install 'uv==0.12.23'
Assert-Exit 'uv installation'
if ($SourceSqlAlchemy) {
  $previousExtensionSetting = $env:DISABLE_SQLALCHEMY_CEXT
  try {
    $env:DISABLE_SQLALCHEMY_CEXT = '1'
    & '.\.bootstrap\Scripts\uv.exe' --cache-dir .uv-cache sync --locked --no-binary-package sqlalchemy --reinstall-package sqlalchemy
    Assert-Exit 'Backend source dependency installation'
  } finally { $env:DISABLE_SQLALCHEMY_CEXT = $previousExtensionSetting }
} else {
  & '.\.bootstrap\Scripts\uv.exe' --cache-dir .uv-cache sync --locked
  Assert-Exit 'Backend dependency installation'
}
Push-Location -LiteralPath 'apps\web'
try {
  npm.cmd ci --no-fund --cache "$projectRoot\.npm-cache"
  Assert-Exit 'Frontend dependency installation'
} finally { Pop-Location }
Write-Output 'Dependencies installed. See README.md for PostgreSQL and development commands.'
