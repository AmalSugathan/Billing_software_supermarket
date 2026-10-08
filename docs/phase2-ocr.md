# Phase 2 - reviewed invoice intelligence

## Scope and deferred acceptance
The user explicitly deferred physical opening-stock checks, staff acceptance and hardware acceptance until the final build. Preserve those gates; they do not prevent Phase 2 development. Phase 1's automated pilot and remote CI passed at f5716e5. No real-store deployment is implied.

Phase 2 pipeline: private upload -> signature/size/decode/page validation -> preserved original and safe derivative -> OCR preprocessing/layout parsing -> document/supplier identification -> invoice/line fields with evidence and confidence availability -> matching existing products -> arithmetic/source/duplicate validation -> human review -> existing transactional purchase posting -> immutable inventory/audit. No OCR output is a purchase authorization.

## First implementation increment
Build the private supplier-bill inbox, encrypted original/derivative storage, immutable processing attempts/results, PaddleOCR-VL-1.6 adapter, evidence review and safe matching candidates. JPEG/PNG and restricted PDF intake need bounded file bytes/pixels/pages and authenticated tenant/store permissions; extension/client MIME alone is insufficient. Keep source hashes immutable and deduplicate within a business/store. No public evidence URLs and no service-worker/API caching. Never fetch an invoice URL supplied by a user/model.

Store raw evidence separately from reviewed field values. Provider unavailable/failure must be explicit and may never be replaced by a fake successful extraction. Support original-bill visibility while OCR runtime configuration remains outstanding. Financial posting from the inbox is a later reviewed increment using the same purchase domain service, not a second inventory implementation. Manual transcription is clearly labelled human review, never OCR output.

## Database/API architecture
Migration 0009: tenant/store-scoped OCRDocument with encrypted original/derivative blobs, content identity and upload provenance; append-only OCRAttempt and OCRResult linked to document/request/operator/provider, status/error/evidence; normalized SupplierProductMapping with reviewed source and product reference. Forced RLS/composite FKs/immutable guards and runtime privilege revocation match existing modules. Status derives from attempts/results; crash recovery retains prior attempts and permits explicitly reviewed retry after a bounded lease.

API routes under business/store: document list/upload/detail/preview; provider availability; process/retry and match candidates. Authenticate/authorize purchases.manage before reading bytes/doc IDs; CSRF/Origin on upload/process. Upload has bounded raw binary body and UUID idempotency key; no filename determines storage paths. Original evidence download is authenticated and audited. Processing does not hold a database connection during remote inference. Results are plain text/structured JSON; never execute/render raw model HTML.

Frontend: Invoice inbox tab; photo/PDF selection with limits, uploaded private evidence list, safe source preview, real provider status/process action and extraction/evidence display. Keep original upload/process retry keys; failed/uncertain attempts remain visible. Proposed matches are advisory and never automatically create products or change prices/stock.

## Provider and confidence policy
Use a separate trusted self-hosted PaddleOCR-VL-1.6 full document parsing service. The official /layout-parsing request accepts Base64 content plus fileType 0(PDF)/1(image); parse layoutParsingResults/prunedResult/markdown. Disable returned images/URLs where unnecessary. Restrict outbound host to configured service; no arbitrary URLs, redirects or cloud endpoints. Original bills are not sent to a hosted service without the user's explicit selection/configuration.

The application runtime stays Python 3.14; official PaddleOCR docs list Python 3.9-3.13 as verified. Local hardware is an RTX 3050 Laptop GPU with about 4 GB VRAM; model/pipeline memory and throughput are not yet measured. A separate compatible Python/Linux/container runtime must be configured and checked. This machine currently has no Docker and WSL reports installation failure. Do not alter Windows Application Control or pretend inference is installed.

Paddle layout output is not calibrated invoice-field confidence. Every proposed field records source evidence, confidence value or explicitly unavailable status, confidence method and review requirement. Do not invent 96%/99% certainty. Rule-based matching scores are labelled scores, not probabilities; barcode/SKU/verified supplier aliases, normalized text and consistent pack/unit evidence rank candidates. Semantic matching is a separate unavailable capability until a real provider/evaluation exists. Every invoice requires human review in this phase.

## Subsequent increments and validation
1. Configure and run the actual provider on compatible hardware; retain all seven private bills unchanged and establish reviewed field ground truth.
2. Parse headers/tables with field/crop/page evidence, distinguish carton/UPC conversions from barcode and preserve repeated invoice rows.
3. Review/mapping UI, exact purchase preview, source tax/total mismatch review, duplicate source invoice checks, approved mapping learning and linked atomic purchase posting.
4. Measure held-out invoice extraction/matching/calibration and review time; add hostile/corrupt/oversize/multipage/provider-timeout/permission/replay tests.

Seven original photographs are an initial smoke/evidence set, not proof of general OCR accuracy. Do not treat historical purchase quantities as opening/current stock. Real financial import requires a reviewed stock cutoff; a separate isolated original-bill review business may contain documents without stock movements. Production scanner/printing/staff and physical counts remain deferred to final build.

## Official references checked 8 October 2026
- https://www.paddleocr.ai/latest/en/version3.x/pipeline_usage/PaddleOCR-VL.html
- https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6

## Local use and recovery
Open http://127.0.0.1:8080 (built portal) or http://127.0.0.1:5173 (development). Sign in with the existing owner account, choose **Phase 2 original-bill review** in Business, then open **Invoice inbox**. Seven authentic photographs are stored in this separate review business. Select a bill to see the safe source image or download the original. Its stock and purchase lists remain empty. DEMO Phase 1 walkthrough remains a separate synthetic operational business.

The local encryption key is in ignored `.tools/phase2/ocr.key`, with Windows inheritance removed and access limited to the current Windows user and SYSTEM. Never publish it. Before restarting the API in PowerShell:

```powershell
$env:OCR_ENCRYPTION_KEY = [System.IO.File]::ReadAllText((Resolve-Path -LiteralPath '.tools/phase2/ocr.key'))
# Use the existing non-owner runtime DATABASE_URL, COOKIE_SECURE and allowed origins.
.venv/Scripts/python.exe -m uvicorn supermarket.main:create_app --factory --host 127.0.0.1 --port 8000
```

The environment example is not auto-loaded. Keep `OCR_SERVICE_URL` unset until a trusted full-document Paddle server is running in the API's loopback network namespace. A Docker API's loopback points inside that container; an external host is not accepted by this initial adapter. Set the URL to the server root (not `/layout-parsing`); HTTP redirects and environment proxies are disabled. A configured URL is not a successful model health check, and the reported model is the deployment contract, not independently verified checkpoint identity.

Originals, image derivatives and OCR evidence are AES-256-GCM encrypted in PostgreSQL, with business/store/resource/purpose bound as authenticated data. Back up the database **and separately protect the exact encryption key**; a database restore alone cannot decrypt evidence. Do not rotate/delete this key without a reviewed re-encryption migration. Document metadata and approved supplier-description mappings remain normal tenant-protected records; browser recovery stores only upload identity or pending command details, never invoice image bytes. Processing original evidence must remain on private infrastructure.

Re-run local private intake without posting finances:

```powershell
.venv/Scripts/python.exe scripts/import_private_bills.py --source Purchase_Bills_Demo --email amalsugathan123@gmail.com
```

The helper prompts for the portal password, uses a separate session and skips original content hashes already present. Do not commit password/environment values or private bills. File validation rejects unsupported/active/encrypted PDF content but is not antivirus certification; production ingestion still requires an isolated resource-limited parsing/inference worker and retention policy.

## Increment 1 validation
Real PostgreSQL tests cover encrypted originals, immutable records, duplicate/idempotent uploads, cross-business denial, provider failure and reviewed pack-aware matching. Synthetic localhost HTTP tests verify the official request/response contract, bounded output, missing text and redirect rejection; they do not claim the real Paddle model has run. UI tests cover unavailable provider, plain-text model evidence and explicit mapping approval. The real Edge browser pilot verifies a committed upload with a lost response, same-key replay, decoded image preview, original download, no API caching and no purchase/stock changes. Phase 1 offline checkout/refund/closing browser validation also passes.
