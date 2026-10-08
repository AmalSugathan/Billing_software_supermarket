# Development roadmap

The user's six-phase sequence is authoritative. Release each phase only after its acceptance gates.

| Phase | Scope | Gate |
|---|---|---|
| 1 Foundation | Identity/setup, products/barcodes, POS, inventory, purchases/suppliers, expenses and cashier cash | Decimal accuracy, tenant/store permissions, atomic posting/corrections, offline recovery and store pilot |
| 2 OCR | Invoice -> OCR -> product matching -> purchase -> inventory | Evidence/confidence, duplicate/mismatch rejection, human review and provider failure safety |
| 3 Owner intelligence | Dashboard, cash flow, profit and daily AI briefing | Recorded-data reconciliation, accounting definitions and grounded unavailable-data handling |
| 4 AI agents | Inventory, purchase, finance, pricing, expiry, supplier | Tool authorization, advisory recommendations, confidence and significant-action approval |
| 5 GST intelligence | Validation, reconciliation, preparation and integration | Accountant-reviewed fixtures and authorized integration before filing claims |
| 6 Predictive AI | Demand, reorder, pricing, expiry, suppliers, cash flow and anomalies | Backtesting, uncertainty, monitoring and data sufficiency |

## Phase 1 implementation
1. Authentication, business/store/terminal setup, roles/store assignments and audit.
2. Products, barcodes/SKU search, suppliers, duplicate review and opening stock ledger.
3. Reviewed purchases with invoice-specific pack conversions/free quantities; expenses and reversals.
4. Cash/bank accounts, opening/closing reconciliation, online cash/UPI/card/split billing and invoice-linked supplier payments.
5. Owner-approved sales returns/cancellation, supplier-payment reversal, safe unused-purchase reversal and physical-count deltas.
6. Prepared offline cash tills with encrypted IndexedDB journals, versioned service-worker shell, stock quotas, ordered replay-safe synchronization and recovery/finalization.
7. Automated synthetic pilot, regression/tenant/concurrency tests, browser outage/lost-response tests, checkout timing and private backup/isolated restore drill.

See [pilot plan](phase1-pilot.md) and [validation evidence](pilot-validation.md). Passing software checks is not physical store sign-off. Before live use, validate opening counts/cash, target browser/device crash/power-loss/storage recovery, staff workflows, thermal printing, invoice formatting/tax policy, production HTTPS/secrets/backups and retention. Barcode scanner hardware remains deferred at the user's request. Customer credit, customer-specific GST/interstate receipts and partial used-goods supplier returns remain unsupported; use this pilot for local cash/UPI/card retail operations only.

## Data and next phase
The owner can use the isolated **DEMO Phase 1 walkthrough** business. Its operational CSVs are explicitly synthetic, with blank barcodes. Original files in Purchase_Bills_Demo remain private and unimported. Phase 2 will evaluate the requested PaddleOCR-VL-1.6 provider and add upload -> preprocessing -> extraction/matching -> confidence/evidence review -> reviewed purchase -> inventory. Explicit invoice carton quantities/pieces determine product-specific conversion; never assume one universal carton size or auto-create duplicate products.

## Development method
Behavior/criteria -> migration -> backend -> validated API -> UI -> permissions/validation -> meaningful edge-case tests -> docs -> local gates -> push -> remote CI. No automatic deployment. Significant financial actions always require authorization and confirmation. Phase 3 profit reporting requires reconciliation of actual recorded costs; revenue is never labelled profit.
