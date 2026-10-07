param([switch]$Database, [switch]$Audit)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Run scripts/bootstrap.ps1 first.' }

function Invoke-PythonCheck([string[]]$arguments) {
  & $pythonPath @arguments
  if ($LASTEXITCODE -ne 0) { throw "Python check failed: $arguments" }
}
function Invoke-NodeCheck([string[]]$arguments) {
  npm.cmd @arguments
  if ($LASTEXITCODE -ne 0) { throw "Frontend check failed: $arguments" }
}

if ($Audit) {
  & '.\.bootstrap\Scripts\uv.exe' --cache-dir .uv-cache export --locked --no-emit-project --no-hashes --output-file requirements-audit.txt --quiet
  if ($LASTEXITCODE -ne 0) { throw 'Dependency audit export failed.' }
}

Invoke-PythonCheck @('-m', 'ruff', 'check', '.')
Invoke-PythonCheck @('-m', 'ruff', 'format', '--check', '.')
if ($Database) {
  if (-not $env:TEST_DATABASE_URL) { throw 'Set TEST_DATABASE_URL to a dedicated development PostgreSQL database.' }
  $previousRequirement = $env:REQUIRE_POSTGRES_TESTS
  try {
    $env:REQUIRE_POSTGRES_TESTS = '1'
    Invoke-PythonCheck @('-m', 'pytest', '--cov=supermarket', '--cov-report=term-missing')
  } finally { $env:REQUIRE_POSTGRES_TESTS = $previousRequirement }
}
if (-not $Database) { Invoke-PythonCheck @('-m', 'pytest', '-m', 'not database') }
Push-Location -LiteralPath 'apps\web'
try {
  Invoke-NodeCheck @('run', 'typecheck:api')
  Invoke-NodeCheck @('run', 'lint')
  Invoke-NodeCheck @('run', 'typecheck')
  Invoke-NodeCheck @('test')
  Invoke-NodeCheck @('run', 'build')
  if ($Audit) { Invoke-NodeCheck @('audit', '--audit-level=high') }
} finally { Pop-Location }
if ($Audit) {
  Invoke-PythonCheck @('-m', 'pip_audit', '--disable-pip', '--no-deps', '--cache-dir', '.audit-cache', '-r', 'requirements-audit.txt')
}
Write-Output 'Selected checks passed. PostgreSQL/container/remote CI checks are separate when not selected.'
