# Development roadmap

The user's six-phase sequence is authoritative. Phases are released after their acceptance gates; do not implement everything simultaneously.

| Phase | Scope | Gate |
|---|---|---|
| 1 Foundation | Identity/setup, products/barcodes, POS, inventory, purchases/suppliers, expenses and cashier cash | Decimal accuracy, permissions/tenant isolation, atomic posting/reversal, offline replay/crash and hardware validation |
| 2 OCR | Invoice -> OCR -> product matching -> purchase -> inventory | Evidence/confidence, duplicate/mismatch rejection, human review and scanner/provider failure safety |
| 3 Owner intelligence | Dashboard, cash flow, profit and daily AI briefing | Recorded-data reconciliation, clear accounting definitions, unavailable-data handling and grounded briefing |
| 4 AI agents | Inventory, purchase, finance, pricing, expiry, supplier | Tool authorization, advisory recommendations, confidence and explicit significant-action approval |
| 5 GST intelligence | Validation, reconciliation, preparation and integration | Accountant-reviewed fixtures and current rules; authorized real integration before filing claims |
| 6 Predictive AI | Demand, reorder, pricing, expiry, suppliers, cash flow and anomalies | Backtesting, uncertainty, monitoring, data sufficiency and decision safety |

## Current increment: Phase 1 catalog and opening inventory
Identity/setup is complete as a development increment and its remote CI passed. The second increment adds categories/brands, supplier records, product fields/aliases, multiple barcodes, search, barcode lookup, fuzzy duplicate review, owner-confirmed opening counts, immutable batches/movements and assigned-store inventory views. Money and weighted quantities use decimal strings and PostgreSQL NUMERIC. No AI matching or OCR is claimed before Phase 2.

Opening retries serialize on a tenant/request key; identical replay returns the original movement and changed data is rejected. Duplicate product SKU/barcode/name creation rolls back atomically. Inventory reads and ledger postings enforce business/store permissions independently of the UI. Opening value is recorded opening quantity times cost, not profit or a completed inventory valuation policy. Purchase posting, expenses, corrections, cash, POS and offline reliability remain outstanding; Phase 1 is not ready for a live store.

## Phase 1 increments
1. Identity, business/store/terminal setup, membership/capabilities, tenant isolation and audit foundations.
2. Catalog/barcodes/suppliers and immutable stock ledger.
3. Purchases/expenses and atomic ledger postings.
4. Decimal/tax policy, cashier sessions, POS/tender/returns and receipt hardware.
5. Durable local POS, sync conflict reconciliation, pilot security/backup/performance acceptance.

## Per-feature workflow
Explain behavior and acceptance criteria -> migration -> domain/backend -> validated API -> frontend -> permission checks -> meaningful tests/edge cases -> documentation -> review. Every feature must demonstrate real end-to-end behavior. External adapters explicitly unavailable until configured.

CI gates on PR and main: backend lint/format/types/tests, frontend lint/types/tests/build, PostgreSQL migration/integration tests, packaging build and dependency audit. The aggregate gate must fail if any job fails or is cancelled. Production deployment is not part of this initial pipeline.
