# Private PaddleOCR-VL-1.6 runtime

This is the full local document pipeline, including orientation, unwarping, PP-DocLayoutV3 and PaddleOCR-VL-1.6-0.9B recognition. It has no database or financial tools. The application owns authentication, tenant isolation, encrypted evidence and reviewed purchases. No bill is sent to a hosted model service.

## Windows setup
The application uses Python 3.14; this worker uses a separate official signed Python 3.13.16 runtime. The managed unsigned Python download and the latest scikit-learn Windows native module were rejected by Windows Application Control. The signed Python runtime and official scikit-learn 1.7.2 build passed native imports without changing Windows policy.

The verified official embeddable ZIP URL is https://www.python.org/ftp/python/3.13.16/python-3.13.16-embed-amd64.zip . Verify SHA-256 `97dae5274cc54867065e8d5a3226e48c35017ed332a0fdb0e27d5b5821961297` and a valid Python Software Foundation executable signature, extract to ignored `.tools/phase2/python313`, and add `Lib/site-packages` plus `import site` to its `python313._pth`. Do not disable Windows security controls.

```powershell
./scripts/start-ocr.ps1 -Install
```

Dependencies are pinned in this project's uv.lock; requirements.lock is its generated hashed export for the embedded Windows interpreter. The installer uses the workspace uv cache and the worker's private site-packages. On a compatible Linux x64 host, use `uv sync --locked --project services/ocr --python 3.13`, set private model-cache paths, then run `services/ocr/.venv/bin/python services/ocr/server.py` from the repository. The main API need not install model dependencies.

The server binds only `127.0.0.1:8091`. Model initialization downloads official weights into `.tools/phase2/models`; startup is not ready until the genuine complete pipeline loads. Readiness endpoint `/health/ready` reports model, pipeline version, CPU device and busy state. The internal `/layout-parsing` interface accepts Base64 bytes only, rejects URL inputs, uses shared signature/size/pixel/PDF validation, serializes plain structured evidence, limits output and permits one inference at a time. Temporary PDF input is local and removed afterwards; original evidence remains encrypted in the application database.

The service uses batch size one, four native CPU threads, bounded 4,096-token recognition blocks and KV caching. A token bound can truncate recognition; always compare complete source rows/totals and never treat model success as financial approval. CPU latency must be measured on target hardware. This is a development worker, not production malware-isolation certification.

## Connect the application
Start the application with its existing private encryption key and runtime database role:

```powershell
$env:OCR_ENCRYPTION_KEY = [System.IO.File]::ReadAllText((Resolve-Path -LiteralPath '.tools/phase2/ocr.key'))
$env:OCR_SERVICE_URL = 'http://127.0.0.1:8091'
$env:OCR_TIMEOUT_SECONDS = '7200'
.venv/Scripts/python.exe -m uvicorn supermarket.main:create_app --factory --host 127.0.0.1 --port 8000
```

OCR runs as a background attempt, returns HTTP 202 and is polled in the UI. The database lease exceeds the configured bounded inference timeout. A crashed process retains the attempt and evidence; an explicit retry after lease expiry records an audited interruption. This initial local worker is not an external durable distributed job queue. Restarting the API during recognition may discard an in-flight result, but never posts stock or loses original evidence.

The encrypted source, latest evidence and advisory header/table draft appear in Invoice inbox. Drafts retain source document/hash/attempt, source row coordinates, unavailable confidence, missing values and total-mismatch warnings. They are not posted purchases. Supplier paid status, current physical stock and selling prices must never be inferred from an invoice total.

Official references: [PaddleOCR full-pipeline setup](https://www.paddleocr.ai/latest/en/version3.x/pipeline_usage/PaddleOCR-VL.html), [Python 3.13.16 release/checksums](https://www.python.org/downloads/release/python-31316/).

For the verified CPU setup, use a bounded 7,200-second application timeout; a
five-line photographed invoice took approximately 23 minutes. Larger or fused
tables can take longer. The portal remains usable while inference is in progress.
