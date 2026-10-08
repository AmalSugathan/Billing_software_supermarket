# Phase 1 pilot validation - 8 October 2026

Status: automated development pilot passed; live-store acceptance is pending. This is not a production deployment or certification.

## Implemented and exercised
Authentication/tenant setup, products/suppliers, reviewed purchase/carton/free-unit calculations, immutable stock, expenses, cash/bank/session reconciliation, online cash/UPI/card splits, invoice-linked supplier payments, controlled refunds/cancellation, payment/purchase reversals, count adjustments and prepared offline cash checkout use real APIs and PostgreSQL. All pilot business transactions are explicitly synthetic. Original bills remain private/unimported.

| Check | Observed result |
|---|---|
| Backend PostgreSQL regression | 68 passed; 96.91% statement coverage |
| Frontend/unit journal tests | 29 passed |
| API/frontend types, lint, format and build | Passed; versioned sw.js included in build |
| Workflow syntax | actionlint passed |
| Real Edge browser offline pilot | Passed against built preview and non-owner runtime API |
| Twenty synthetic online checkouts | Median 36 ms; p95 47 ms, one user on local API; not a production load benchmark |
| Consistent private backup / disposable restore | 49 public tables and 358 rows matched full-row digests; 1.09 s drill |
| Restored runtime isolation | No tenant context returned zero financial/stock/purchase/offline rows; non-superuser/non-bypass role verified |
| Dependency installation audit | npm reported zero vulnerabilities for updated locked dependencies; time-specific observation |

The full local gate ran through scripts/check.ps1 -Database. GitHub Actions adds a required Offline browser pilot job using Chromium against the real container stack; final remote job outcomes are recorded per commit in [repository Actions](https://github.com/AmalSugathan/Billing_software_supermarket/actions). This document records local evidence; do not infer a remote result before that commit's CI gate finishes.

## Browser scenario and reconciliation
1. Register an isolated synthetic pilot account/business, create one product/10 units, a terminal and INR 1000 opening drawer.
2. Prepare eight units online, encrypt the local till, disconnect and reload the cached application.
3. Unlock and complete two units at INR 25: receipt INR 50, tender INR 100, change INR 50. Reload while still offline; one pending receipt remains.
4. Reconnect; server commits synchronization but the test drops its response. Retry the original receipt: exactly one sale, stock eight, expected cash INR 1050.
5. Finalize; deliberately lose the committed finalization response. Reload/unlock: new sales remain locked, original finalization command survives and its retry confirms the server seal.
6. Review and approve a one-unit refund through the UI: exact INR 25 refund, stock nine, expected drawer INR 1025. Closing count INR 1025 records zero variance.
7. Open a new synthetic session, execute twenty one-unit checkout requests on separately stocked synthetic goods; final stock 80 and expected/actual drawer INR 1525 reconcile with zero variance.

The journal tests additionally exercise wrong passphrase, encrypted/tampered backups, import-overwrite rejection, competing tabs/quota exhaustion, expiration, blocked receipt retention, exact decimal calculations and locally locked finalization retries. Backend tests cover concurrent returns/sync, cross-tenant/role denials, stale stock counts, reserved stock isolation, used/paid purchase reversal rejection and tax/cash compensation. Existing suites cover duplicate invoices, rollback, immutable records, posting concurrency and migration downgrade/re-upgrade.

## Store operator acceptance still required
- Confirm actual opening stock and cash at a defined cutoff; review real product/HSN/GST/price/MRP and pack conversion evidence.
- Observe trained staff completing sale, split tender, partial return, cancellation, supplier payment, expense and explained closing variance workflows.
- Test the target device/browser under process crash, power loss, storage pressure, disk failure, passphrase recovery and encrypted journal restoration. Browser reload/network tests do not prove physical durability.
- Validate thermal print layout/hardware. Scanner remains deferred by user choice; search/SKU works without it.
- Review statutory receipt numbering/tax policy with the accountant, production HTTPS/session/secrets, backup encryption/off-device retention, monitoring and incident recovery.
- Decide when to add customer credit/interstate/customer-tax receipts and used-goods partial supplier returns; these are unsupported in this controlled pilot.

Use the existing **DEMO Phase 1 walkthrough** in the owner portal for practice. The automated pilot creates separate disposable synthetic accounts and does not alter the owner's shop or walkthrough. Private dumps/reports live under ignored .tools/pilot_backups; browser traces are ignored and excluded from container builds. Original bill upload/OCR/matching comes in Phase 2 after field ground truth, provider configuration, evidence/confidence review and stock-cutoff approval. No model/provider integration is represented as working yet.
