# Gemini Flash invoice and pack extraction

The active provider is Gemini Flash (`gemini-3.5-flash`), replacing local CPU Paddle inference. The owner explicitly selected hosted Gemini processing. Validated invoice bytes are sent directly to Google's fixed HTTPS API; no local Paddle model, OCR HTML table parsing, agent tools or database access is involved in this provider.

## Local setup

Run from the workspace root:

```powershell
./scripts/start-gemini-api.ps1 -PromptForApiKey
```

The prompt hides the key. Alternatively supply `GEMINI_API_KEY` in the server environment or use `-ApiKeyFile` with a private file. The local private key is in ignored `.tools/phase2/gemini.key` with restricted Windows ACLs. Never put it in the browser, Vite variables, command-line arguments, logs, fixtures or Git. Stop the existing API instance on port 8000 before starting another. This script uses the existing encryption key and local development database; production must use its own secret manager and secure deployment settings.

Settings: `OCR_PROVIDER=gemini`, `GEMINI_MODEL=gemini-3.5-flash`, `OCR_TIMEOUT_SECONDS=180`. Model metadata readiness verifies key/model access but does not guarantee quota for the next generation. There is no automatic fallback to a different provider or model. Optional legacy `OCR_PROVIDER=paddle` remains explicit; saved historical Paddle evidence remains readable.

## Schema and validation

`apps/api/src/supermarket/invoice_extraction.py` defines Pydantic `ExtractedItem` and `ExtractedInvoice`. Each item carries the owner's fields: original_name, normalized_name, invoice_quantity, invoice_unit, detected_pack_size, pack_unit, purchase_unit_interpretation, stock_unit, stock_quantity, unit_rate, cost_per_kg and requires_review. Nullable fields preserve missing data. Supporting source text/page, review reasons, free stock, discounts, GST, HSN and batch/expiry support the reviewed purchase.

The envelope contains supplier/invoice header, page count, items, printed total and optional tax basis/round-off. A projection of its generated JSON Schema is sent to Gemini: field names, types, enums, nesting and required fields, with references inlined. The request asks for null in stock_quantity and cost_per_kg; the server fills these derived values with Decimal arithmetic from validated quantity, units and pack content. Inbound non-null derived values are still checked and rejected if inconsistent. The full constrained Pydantic schema was rejected by the live API; range/precision/date/arithmetic checks remain enforced by server-side Pydantic. The complete STOP response is parsed with Decimal JSON numbers and validated again on the server. Decimal values serialize as strings to clients. Unknown fields, invalid types, negative quantities, invalid dates/units, non-finite values, inconsistent arithmetic and unsupported precision are rejected. No binary floating-point monetary calculations occur.

Examples:

- 2 BAG x 50 KG = 100 KG; INR 2275/BAG / 50 KG = INR 45.50/KG.
- If the printed unit is KG but interpreted as BAG, retain both and force review, regardless of the model's false flag.
- 2 CARTON x 24 PCS = 48 PCS, only when carton content is supported by source evidence.
- 2 PCS of a 500 G retail pack stay 2 PCS when stock is managed by piece.
- Loose 2 KG stays 2 KG even if the description mentions a 50 KG bag.
- Unknown or nested pack content stays unresolved. Never guess a carton size.

`stock_quantity` represents paid stock only; `free_stock_quantity` is additional stock in the same stock unit. `unit_rate` is per interpreted purchase unit in the printed tax basis before discount. `cost_per_kg` follows that rate basis, not landed cost, margin or a proposed selling price. A counted retail packet with explicit mass content can have cost_per_kg without changing piece stock. Volume alone never establishes kg cost. Count units must be whole; stock/conversion precision is three decimals. No calibrated field confidence is claimed. `requires_review=false` means no detected extraction ambiguity, never financial approval.

## UI and posting

Invoice inbox -> Extract invoice -> automatic compact quantity table -> Review as purchase. Original source and raw details remain accessible. The purchase form prefills validated quantity, unit and conversion. Owner selects existing catalog products/supplier, checks tax/free/discount values, previews Decimal totals and explicitly confirms posting. Stock units must match the selected product. Existing purchase idempotency, source linkage, tenant isolation and immutable stock/account ledgers remain authoritative. A payable is not a cash/bank payment.

The seven existing bills can be re-extracted without overwriting old evidence:

```powershell
.venv/Scripts/python.exe scripts/process_private_bills.py --email amalsugathan123@gmail.com --use-current-provider
```

The script prompts for portal password, skips completed results from the current model, retains old attempts and never posts financial changes. Use `--limit 1` for the first real bill. A quota response stops the batch instead of repeatedly calling Gemini; incomplete runs exit with status 2 and preserve all earlier results. HTTP 202 background work retains the existing interruption/retry limitation; this is not a durable distributed queue.

## Persistence and testing

No relational migration is necessary: migration 0010 already stores encrypted versioned evidence and provider identity per immutable attempt. The structured payload adds optional `extraction` data with schema_version=1; previous results remain compatible. Each new extraction is a new audited attempt. Pydantic schema validation is separate from semantic accuracy and human posting authorization.

Synthetic contract tests cover malformed/truncated/blocked responses, quota/authentication errors, units, conversions, rounding, ambiguity and encrypted PostgreSQL draft persistence with no financial side effects. UI tests cover automatic compact results and purchase prefill/mismatched catalog units. Synthetic tests are not evidence of model accuracy. Real bill timings and results are recorded after execution; no latency guarantee is assumed.

Official references: [Gemini Flash](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash), [structured output](https://ai.google.dev/gemini-api/docs/generate-content/structured-output).


## Local real-bill validation, 2026-10-10

The Gemini key is configured privately and Gemini 3.5 Flash is the active provider. Three original bills produced validated, encrypted drafts (5, 1 and 4 proposed rows). Measured successful attempt times were 24.9, 17.8 and 23.0 seconds, respectively, on this connection. These are observations, not a latency or extraction-accuracy guarantee.

Four remaining originals are pending: initial requests hit quota limits; subsequent requests returned provider service errors. A one-bill comparison with Gemini 2.5 Flash returned model unavailable for generation, so no fallback was enabled. No partial/invalid response was promoted to a draft. Originals and earlier Paddle attempts are preserved. No original purchase, inventory movement, supplier payable or payment has been posted by this revision.

The active UI is available at http://127.0.0.1:8080 in the Phase 2 original-bill review business, under Invoice inbox. Validate the original source, catalog match, unit conversion and purchase totals before posting. A larger accuracy benchmark and reliable provider capacity remain required for a production OCR rollout.
