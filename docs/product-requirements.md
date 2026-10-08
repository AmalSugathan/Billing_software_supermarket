# Product requirements

Status: architecture baseline, 7 October 2026. See roadmap for implemented versus planned work.

## Vision
An AI manager beside the owner. Reduce manual entry and proactively explain problems, opportunities and next actions using recorded business data. Billing reliability, financial correctness, tenant isolation and auditability take priority over AI availability.

## Users
Owner: full business visibility and significant financial approvals. Store manager: assigned store operations. Cashier: own sales and cash sessions, bounded discounts. Inventory manager: counts, batches and adjustment proposals. Purchase manager: suppliers and purchase drafts/review. Accountant: financial reconciliation and GST preparation. Admin: business configuration and access administration; financial authority is separately granted.

Roles are capability templates. Memberships connect users to businesses and store scopes; users do not belong permanently to a terminal. Platform support access is separate, time-limited and audited.

## Phase 1 foundation
Authentication, business/store/terminal setup and RBAC precede operational endpoints. Product master includes SKU, barcode aliases, name aliases, category, brand, unit, purchase/landed/selling prices, MRP, tax rate, HSN, reorder settings, supplier mappings, batch/expiry flags, active state, minimum selling price and target margin.

POS supports keyboard/scanner search, touch-friendly checkout, quantities, line/bill discounts, tax, customers, cash/UPI/card/split tender, credit, returns, reversals, receipt reprints and cashier opening/closing. Offline completion requires durable local recording and idempotent synchronization. Unverified UPI/card payments remain pending.

Inventory quantities derive from immutable opening/purchase/sale/return/wastage/damage/adjustment movements. Purchase posting updates inventory atomically. Expenses, supplier balances and cash reconciliation are included. Every financial command records actor and reason where applicable.

## Phases 2–6
2: invoice upload, OCR, evidence/confidence, product matching, human review, purchase posting.
3: owner dashboard, cash flow, estimated gross profit and daily briefing.
4: inventory, purchase, finance, pricing, expiry and supplier agents through restricted tools.
5: GST validation, reconciliation, preparation and authorized integration.
6: forecasting, reorder recommendations, price optimization, expiry prediction, supplier recommendations, cash-flow forecasts and anomaly detection.

## Acceptance rules
- No automatic duplicate product creation; barcode/SKU/supplier aliases/fuzzy matching rank candidates.
- Monetary values use decimals, never binary floating point. Revenue, cash received, gross profit and cash flow are separate measures.
- Missing costs produce incomplete profit, not an invented number.
- Completed sales cannot disappear during network failure. Retries never duplicate postings.
- Posted financial/stock records are corrected by reversals, not overwrites/deletion.
- OCR low-confidence fields block posting; MVP OCR always requires review.
- Price changes, refunds, stock adjustments, supplier payments and tax submissions require authorized confirmation.
- Staff anomalies use "requires review", not a fraud accusation.
- Dashboard shows scope, period, data freshness and at most three prioritized actions initially.
- GST readiness measures data completeness, not legal compliance or filing success.

## Ambiguities and proposed defaults
INR and Asia/Kolkata; one pilot store with multi-store schema; Python API, React frontend and PostgreSQL; separate durable local POS runtime. Resolve valuation policy, inclusive/exclusive tax treatment, rounding and discounts, tax registration type, interstate supply, negative stock, offline authorization duration, invoice series, weighing scales/printers, languages, retention, hosting region and provider budget before dependent implementation. These are open decisions, not silently accepted fiscal policies.

## Performance and release
Provisional scan-to-cart p95 <100 ms and local commit p95 <500 ms on agreed hardware. Measure with realistic catalogs and concurrent terminals. Every phase has migrations, validated APIs, usable frontend, permission checks, meaningful tests, edge cases and updated docs. External integrations must report unavailable status until configured.

## Current foundation pilot boundary
The operational implementation now includes returns/cancellation, controlled reversals/count corrections and prepared offline cash tills. This pilot is fixed-price/local retail; customer credit, interstate/customer-tax receipts, automatic gateways and used-goods partial supplier returns are unsupported. Offline stock quotas, short-lived grants, device encryption, ordered receipt replay and explicit finalization are implemented. Browser reload/network outage recovery is tested; production device/power-loss durability remains an acceptance gate. Owner dashboards/profit/AI briefings remain Phase 3; original-bill OCR/matching remain Phase 2.
