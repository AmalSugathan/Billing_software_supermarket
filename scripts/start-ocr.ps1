param([switch]$Install)
$ErrorActionPreference = 'Stop'
$invoiceRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $invoiceRoot
$invoicePython = Join-Path $invoiceRoot '.tools/phase2/python313/python.exe'
if (-not (Test-Path -LiteralPath $invoicePython)) { throw 'Install the signed official Python 3.13 runtime as documented in services/ocr/README.md first.' }
if ($Install) {
  & '.bootstrap/Scripts/uv.exe' --cache-dir .uv-cache pip install --python $invoicePython --target '.tools/phase2/python313/Lib/site-packages' -r 'services/ocr/requirements.lock'
  if ($LASTEXITCODE -ne 0) { throw 'Pinned OCR dependency installation failed.' }
}
$env:PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK = 'True'
$env:PADDLE_PDX_CACHE_HOME = Join-Path $invoiceRoot '.tools/phase2/models/paddlex'
$env:HF_HOME = Join-Path $invoiceRoot '.tools/phase2/models/huggingface'
$env:HF_HUB_DISABLE_TELEMETRY = '1'
$env:HF_HUB_DISABLE_XET = '1'
$env:OCR_CPU_THREADS = '4'
$env:OMP_NUM_THREADS = '4'
$env:MKL_NUM_THREADS = '4'
& $invoicePython -u (Join-Path $invoiceRoot 'services/ocr/server.py')
if ($LASTEXITCODE -ne 0) { throw 'Private PaddleOCR worker stopped with an error.' }
