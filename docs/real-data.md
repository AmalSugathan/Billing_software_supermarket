# Original supermarket invoices and stock evidence

Use original supplier purchase invoices and the store's actual opening-stock counts for development validation. Retail checkout receipts are not a substitute for supplier invoices. No original invoices or stock dataset have been supplied yet; no synthetic fixture is described as real data. Automated tests use explicit, isolated test records only.

## Intake
Provide a local source folder or a dataset URL with permission to use the documents. Keep raw documents under ignored `data/private/invoices/` and stock files under `data/private/opening-stock/`. Git ignores the entire `data/` directory; originals must never be attached to public issues, committed, or sent to an AI provider by default. No documents have been downloaded or imported by this increment.

For each source record retain a private provenance manifest: document ID, source, collection date, SHA-256 file hash, usage permission, document type, supplier/invoice identifiers, review status and redacted-copy ID. Keep originals unchanged; make reviewed derivative copies separately. Supplier GSTIN, phone/address, customer/staff data, bank details, signatures and payment identifiers need appropriate access restrictions and redaction before any shared fixture.

## Phase 1 validation
Manually review authentic supplier invoices against the product master before entering purchases. Cover packaged/weighted goods, barcode differences, discounts, tax-inclusive/exclusive values, free quantity, repeated invoice numbers, price changes, damaged stock, batches and expiry. Reconcile opening stock through recorded opening movements rather than overwriting balances. An invoice intake plan is not a completed purchase-import feature.

## Phase 2 OCR dataset
After permissions and approved storage are available, capture field-level ground truth including quantity/unit, purchase rate, discount, tax, batch, expiry, MRP and totals, alongside page/crop evidence. Include readable and difficult scans. Separate training/validation/test sets by supplier/invoice family and collection period; deduplicate hashes and near-duplicate scans across splits. Record review uncertainty rather than inventing missing labels.

Evaluate extraction accuracy, matching accuracy, confidence calibration, total/tax consistency, duplicate detection and mandatory-review routing. Human-review thresholds must gate purchase posting. Original evidence, OCR output and approved structured records are distinct versions with provenance. Stock updates occur only through reviewed, transactional purchase posting.

Private storage encryption, controlled access, malware-safe handling, retention and backup policy must be configured before using real documents in a deployed service. This repository currently exposes no upload or OCR integration.
