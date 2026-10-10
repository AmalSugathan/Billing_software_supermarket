param(
    [switch]$PromptForApiKey,
    [string]$ApiKeyFile = "",
    [int]$Port = 8000
)
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
if ($PromptForApiKey) {
    $secureKey = Read-Host "Gemini API key (hidden; kept only in this process)" -AsSecureString
    $keyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
    try { $env:GEMINI_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPointer) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPointer); $secureKey.Dispose() }
} elseif ($ApiKeyFile) {
    $env:GEMINI_API_KEY = [IO.File]::ReadAllText((Resolve-Path -LiteralPath $ApiKeyFile)).Trim()
}
$env:OCR_PROVIDER = "gemini"
if (-not $env:GEMINI_MODEL) { $env:GEMINI_MODEL = "gemini-3.5-flash" }
$env:OCR_TIMEOUT_SECONDS = "180"
if (-not $env:OCR_ENCRYPTION_KEY) {
    $env:OCR_ENCRYPTION_KEY = [IO.File]::ReadAllText((Join-Path $projectRoot ".tools/phase2/ocr.key")).Trim()
}
if (-not $env:DATABASE_URL) {
    $env:DATABASE_URL = "postgresql+psycopg://supermarket_app:local_app_only@127.0.0.1:55432/supermarket_dev"
}
$env:COOKIE_SECURE = "false"
if (-not $env:GEMINI_API_KEY) {
    Write-Host "Gemini key is missing. Invoice review remains available; extraction stays disabled."
    Write-Host "To enable: rerun with -PromptForApiKey, -ApiKeyFile, or a server GEMINI_API_KEY."
}
& (Join-Path $projectRoot ".venv/Scripts/python.exe") -m uvicorn supermarket.main:create_app --factory --host 127.0.0.1 --port $Port
exit $LASTEXITCODE
