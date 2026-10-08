# Phase 1 completion and pilot validation

## Authorized development scope
1. Partial sales returns and full cancellation, original-tax/cost snapshots, explicit refund approval, immutable credit notes and dispositions.
2. Supplier payment reversal, safe full purchase reversal, physical-count stock corrections, reasons/approval/audit.
3. Offline cash POS: prepared terminal stock reservations, immutable authorization snapshots, durable IndexedDB sales, service-worker app shell, ordered idempotent synchronization, conflict visibility and recovery. Other terminals cannot consume reservations. Cash closing is blocked until offline mode is finalized. Product/price changes do not change already authorized offline receipts. Offline UPI/card are outside this first offline mode.
4. Automated pilot: ledger/tax/cash reconciliation, concurrent posting/reversal, tenant/store permissions, real-browser disconnect/reload/sync, local backup/isolated restore and measured checkout timing. Physical scanner/printer/count and staff acceptance remain operator checks; do not claim those passed automatically.

## Correction policy
Posted records are never edited or deleted. Owner-confirmed credit notes reference original items, quantities, discounts/taxes and stock costs. Partial refunds allocate remaining original tax components in paise, with final return restoring exact original totals. Resalable goods return to their original batch. Discarded goods create return and damage movements with zero net sellable stock. Full cancellation requires every original quantity and no prior return. Refund accounts require sufficient recorded funds; cash uses a currently open session. Supplier-payment reversal restores the original account and invoice outstanding; it does not invoke a bank. Purchase reversal requires payments reversed and original quantities unconsumed since purchase; otherwise a reviewed return workflow is required. Physical counts compare expected vs actual, append signed delta, and reject stale expectations.

## Offline policy
Prepare while authenticated and online, using an open own drawer and selected product quotas. Reserve nonexpired batches, freeze price/HSN/GST/minimum-price and source cost. Other stock withdrawals respect outstanding reservations. Prepared profiles expire for NEW local sales; already completed sales remain recoverable after expiry. Close only after uploading the full ordered journal and explicit reconciliation. Never delete a pending sale on failed sync or replace its key. A local device passphrase protects cached profiles and journals. Forgotten passphrase/device destruction is a recovery risk: export the encrypted journal and protect the device; no claim is made that browser storage is indestructible.

## Pilot gate
Automated checks and synthetic scenarios can validate software here. A live-store release still requires actual opening counts, cashier cash checks, trained operator sign-off, printer/scanner checks as scheduled, HTTPS/runtime configuration and a production backup/recovery plan. Original purchase documents stay private; Phase 2 adds reviewed OCR ingestion after stock-cutoff decisions.
