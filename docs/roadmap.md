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

## Current increment: Phase 1 identity and setup
Delivered after the seven planning documents and development/CI baseline: real account registration/login/logout, business with first store, stores/terminals, seven default roles, scoped staff grants, forced PostgreSQL tenant isolation and immutable audit events. The frontend calls real validated APIs; failed requests do not advance setup. Staff grants require an already-registered account; no external email service is claimed.

Local checks now include real PostgreSQL migration round trips, role/tenant/store permission cases, concurrent pool isolation and immutable audit tests. See development-pipeline.md for evidence. Products, stock, purchases, expenses, cash and POS are still outstanding Phase 1 work; the phase is not complete or ready for a live store.

## Phase 1 increments
1. Identity, business/store/terminal setup, membership/capabilities, tenant isolation and audit foundations.
2. Catalog/barcodes/suppliers and immutable stock ledger.
3. Purchases/expenses and atomic ledger postings.
4. Decimal/tax policy, cashier sessions, POS/tender/returns and receipt hardware.
5. Durable local POS, sync conflict reconciliation, pilot security/backup/performance acceptance.

## Per-feature workflow
Explain behavior and acceptance criteria -> migration -> domain/backend -> validated API -> frontend -> permission checks -> meaningful tests/edge cases -> documentation -> review. Every feature must demonstrate real end-to-end behavior. External adapters explicitly unavailable until configured.

CI gates on PR and main: backend lint/format/types/tests, frontend lint/types/tests/build, PostgreSQL migration/integration tests, packaging build and dependency audit. The aggregate gate must fail if any job fails or is cancelled. Production deployment is not part of this initial pipeline.
