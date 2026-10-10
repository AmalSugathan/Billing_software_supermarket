# Phase 3: owner intelligence

## Approved transition
The owner authorized Phase 3 on 2026-10-10. Phase 2 acceptance remains open: process the four pending originals, assess field/product/pack accuracy, reconcile an approved original purchase to stock and payables, validate provider capacity/recovery, and establish confidence reporting. Paid API setup is deferred; it does not itself establish accuracy or posting correctness. Physical stock, staff and hardware acceptance also remain final-build gates.

## First increment
Deliver a read-only, store-scoped dashboard and automatic data-derived briefing from existing posted ledgers. No external AI request is needed. The briefing is explicitly rules-based; generative narration, scheduled delivery, natural-language questions and predictive agents are future increments, not simulated features.

GET /api/v1/businesses/{business_id}/stores/{store_id}/insights?start=YYYY-MM-DD&end=YYYY-MM-DD requires finance.read and assigned-store access. Dates are inclusive Asia/Kolkata calendar days, at most 366 days, no future end date. Reads use a repeatable-read, read-only transaction, tenant RLS and explicit business/store filters. The response includes source cutoff, posting basis, Decimal-string metrics, daily revenue/cash-flow series, attention counts and briefing items. Store/date changes cancel stale UI requests. Financial failures never display invented zero totals.

## Accounting definitions
- Net sales excluding GST: posted sales taxable amounts minus posted credit-note taxable amounts, including cancellation credits.
- Net billed including GST: sales invoice totals minus credit totals. Sale counts and credit counts are separate.
- Net cost of goods sold: negative signed stock value for sale and sales_return movements, using immutable movement costs, not current product prices.
- Estimated gross profit: net sales excluding GST minus net recorded cost of goods sold. Recorded purchase costs are the application's cost basis, not independently verified tax/landed-cost policy.
- Paid expenses: expense movements minus expense reversal movements, by posting date. This is not accrued expenses.
- Stock loss: recorded damage/wastage cost, separate from gross profit. Physical-count adjustments are excluded from earnings estimates.
- After recorded expenses/loss: estimated gross profit minus net paid expenses and recorded stock loss. This is not net profit: accruals, depreciation, financing and other unrecorded costs may be absent.
- Money in/out: positive/negative financial movements excluding opening funds and cash count variances. Supplier payments are money out, not a second cost-of-goods expense. Cash received is not automatically revenue.
- Net cash flow: money in minus money out across recorded cash/bank/UPI/card accounts. Manually recorded bank entries are not bank-verified.
- Opening funds and cash variances are shown separately. Recorded closing balance includes all movements through the selected end date.
- Supplier outstanding: cumulative purchases minus purchase reversals minus supplier payments plus payment reversals through the selected end date.
- Inventory value: signed recorded movement quantity times recorded movement cost through the selected end date. Low-stock checks cover active products with stock history in that store; configured reorder levels only. Expiry checks cover positive batch stock through the following 30 days and already expired stock.

All time-series and period totals use server posting timestamps, including late offline synchronization and corrections; printed invoice/expense dates and original offline sale times are not the reporting basis. Pending unsynchronized offline sales are absent. Zero means no recorded activity for this scope, not proof that no physical business occurred. Rounded display amounts use Decimal ROUND_HALF_UP; cash-flow and revenue series remain exact paise. No profitability projection, GST filing or automatic financial action is introduced.

## Storage and verification
No new persisted entity or migration is needed for this read model; existing indexed tenant/store ledgers remain authoritative. Future persisted/scheduled briefings will require an audited schema change. Verify timezone boundaries, refunds/cancellations, reversal effects, opening-vs-flow separation, cost snapshots, read-only behavior, missing data, role/store/tenant isolation, stale UI response cancellation, desktop/mobile navigation, and existing offline/invoice flows.

## First increment validation (2026-10-10)

Implemented the read-only reporting endpoint, permission-aware default Overview, selectable store/date filters, cash-flow chart and exact daily table, earnings definitions, stock/payable snapshots and prioritized data-derived briefing. No original bill was posted or reprocessed. Backend regression: 136 passed, 96.78% total coverage; new reporting module has 100% statement coverage. Frontend: 40 passed; TypeScript, Pyright, Ruff, ESLint and production build passed. Edge: 4 scenarios passed, including real synthetic ledger reconciliation and desktop/390px/768px dashboard checks, offline recovery/refunds, private invoice intake and workspace navigation. Remote CI is separately recorded by the commit workflow. Generative narration and scheduled delivery remain pending; this is the first Phase 3 increment, not full Phase 3 acceptance.
