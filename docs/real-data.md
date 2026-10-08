# Original supermarket invoices and stock evidence

Seven real supplier invoice photographs have been supplied in Purchase_Bills_Demo and visually assessed. On 8 October 2026 the user authorized synthetic opening-stock, expense, sales/refund and cash-closing examples for development, with barcode collection and physical scanner validation deferred. Clearly labelled reference CSVs are in fixtures/demo; no real opening-stock dataset has been supplied and no fixture has been imported. Use actual counts before a live-store rollout. Retail checkout receipts are not a substitute for supplier invoices. Synthetic fixtures are not described as real data. Automated tests use explicit, isolated test records only.

## Intake
Provide a local source folder or a dataset URL with permission to use the documents. Keep raw documents under ignored `data/private/invoices/` and stock files under `data/private/opening-stock/`. Git ignores the entire `data/` directory; originals must never be attached to public issues, committed, or sent to an AI provider by default. The supplied local photographs have been inspected but not uploaded to an OCR provider or imported as transactions. Purchase_Bills_Demo is excluded from Git and Docker build contexts; keep future originals in ignored private storage as well.

For each source record retain a private provenance manifest: document ID, source, collection date, SHA-256 file hash, usage permission, document type, supplier/invoice identifiers, review status and redacted-copy ID. Keep originals unchanged; make reviewed derivative copies separately. Supplier GSTIN, phone/address, customer/staff data, bank details, signatures and payment identifiers need appropriate access restrictions and redaction before any shared fixture.

## Phase 1 validation
Manually review authentic supplier invoices against the product master before entering purchases. Cover packaged/weighted goods, barcode differences, discounts, tax-inclusive/exclusive values, free quantity, repeated invoice numbers, price changes, damaged stock, batches and expiry. Reconcile opening stock through recorded opening movements rather than overwriting balances. An invoice intake plan is not a completed purchase-import feature.

## Phase 2 OCR dataset
After permissions and approved storage are available, capture field-level ground truth including quantity/unit, purchase rate, discount, tax, batch, expiry, MRP and totals, alongside page/crop evidence. Include readable and difficult scans. Separate training/validation/test sets by supplier/invoice family and collection period; deduplicate hashes and near-duplicate scans across splits. Record review uncertainty rather than inventing missing labels.

Evaluate extraction accuracy, matching accuracy, confidence calibration, total/tax consistency, duplicate detection and mandatory-review routing. Human-review thresholds must gate purchase posting. Original evidence, OCR output and approved structured records are distinct versions with provenance. Stock updates occur only through reviewed, transactional purchase posting.

Private storage encryption, controlled access, malware-safe handling, retention and backup policy must be configured before using real documents in a deployed service. This repository currently exposes no upload or OCR integration.

## Current development dataset decision
Use fixtures/demo for synthetic operational examples and keep authentic purchase bills separate. Product search/SKU supports development without a barcode scanner; existing barcode support is retained for later validation. Explicit invoice carton/piece details may supply product-specific pack conversions after review; ambiguous columns must not produce an automatic inventory update. Phase 1 uses manual reviewed purchase entry, while OCR extraction remains Phase 2. The CSVs currently define reference data only and do not enable unimplemented transaction modules.
