# API specification

## Implemented identity and setup contracts
GET /api/v1/health/live: process status; independent of database.
GET /api/v1/health/ready: verifies database connection and the known migration marker; 503 on unavailable/unmigrated database. It does not expose connection strings or raw exceptions.
GET /openapi.json: actual generated contract. Reviewed purchase endpoints are implemented below. POS, expenses, supplier payments and AI endpoints remain under development.

All routes below use `/api/v1`. UUID path values and strict request models are validated. Mutations require an allowed `Origin`; authenticated mutations additionally require `X-CSRF-Token` returned by session/login/registration. Requests use the HttpOnly session cookie. Session responses use `Cache-Control: no-store`.

| Method | Path | Behavior |
|---|---|---|
| POST | /auth/register | Email, password (12–128 characters), display_name; creates account/session, 201 |
| POST | /auth/login | Email/password; creates a fresh session |
| GET | /auth/session | Current user and CSRF token, or 401 |
| POST | /auth/logout | Revokes current session and clears cookie, 204 |
| GET | /businesses | Businesses available through current user's membership |
| POST | /businesses | name, store_name, optional store_address; creates business/first store/owner roles/audit atomically, 201 |
| GET/POST | /businesses/{business_id}/stores | Assigned stores; creation requires business-wide stores.manage |
| GET | /businesses/{business_id}/terminals | Terminals in permitted stores |
| POST | /businesses/{business_id}/stores/{store_id}/terminals | Terminal name; stores.manage and assigned-store scope required, 201 |
| GET/POST | /businesses/{business_id}/members | Business-wide staff.manage; POST grants an already-registered account a role and store_ids, 201 |
| GET | /businesses/{business_id}/audit | Business-wide audit.read; limit between 1 and 100 |

Business responses include role, capabilities and all_stores. Staff-grant requests cannot grant capabilities the actor lacks. OWNER, ADMIN and ACCOUNTANT memberships are business-wide; operational roles require explicit stores. Duplicate names/memberships return 409. Staff must register before a grant; no email invitation is sent.

## Implemented catalog and inventory
All paths below follow `/api/v1/businesses/{business_id}` and reuse the identity/Origin/CSRF boundary.

| Method | Path | Behavior |
|---|---|---|
| GET/POST | /categories, /brands | Named catalog groups; catalog.manage for creation, business.read for lookup |
| GET/POST | /suppliers | Supplier contact/GSTIN/payment terms; purchases.manage; no payment or purchase posting |
| GET/POST | /products | Search name/SKU/aliases with q; creation requires catalog.manage |
| GET | /products/matches?name=... | Up to 10 fuzzy candidates from latest 2,000 products; catalog.manage |
| GET | /products/resolve-barcode?value=... | Exact active-product lookup; business.read; missing returns 404 |
| POST | /products/{product_id}/barcodes | Adds distinct tenant barcode; catalog.manage |
| GET | /stores/{store_id}/stock | Assigned-store quantities and opening value; inventory.read |
| GET | /stores/{store_id}/stock-movements | Assigned-store immutable movement history; inventory.read |
| POST | /stores/{store_id}/opening-stock | Owner actions.approve, assigned store, explicit confirmation, mandatory Idempotency-Key UUID |

Product, supplier, stock and movement list APIs accept limit (1–200, default 50) and offset (0–100000). Categories/brands currently return at most 200. The frontend paginates products; supplier/inventory views state their display limits. Catalog pages fetch barcode/alias details in batches.

Money, percentages and quantities are non-negative decimal strings: money at most two decimal places, quantities three, no exponent/float/NaN/infinity values. Minimum selling price <= selling price <= MRP. Piece/pack counts must be whole numbers. GST/HSN fields store configured invoice values; this increment does not verify legal classification or filing readiness. Cashier product responses return null for purchase/landed costs and target margin rather than revealing costs or inventing zero values.

Product creation rejects existing tenant SKU, barcode or normalized name/unit. Similar names require reviewing `/products/matches` and explicit `confirm_distinct_product`; this cannot override exact uniqueness. Similarity is a deterministic name comparison, not calibrated AI confidence. Product creation never changes stock. Existing-price edits and deactivation are deferred.

Opening body: product_id, quantity, unit_cost, reason, confirmed=true, optional batch_number/expiry_date according to tracking flags. Positive counts are required. Different lots may have separate openings; a previously opened lot requires a future approved correction workflow. Identical request replay returns the original movement (201), changed details with the same key return 409. The response includes movement ID, actor, source, approval and timestamp. Stock, batch and audit commit together. No delete/update/correction endpoint is currently exposed.

## Planned contracts
| Module | Routes |
|---|---|
| Identity extensions | /api/v1/invitations, /memberships/{id}/revoke, /auth/recovery |
| Catalog | /api/v1/products, /products/resolve-barcode |
| POS | /api/v1/cash-sessions, /sales, /sales/{id}/returns |
| Inventory | /api/v1/stock, /stock-movements, /adjustment-proposals |
| Purchase | /api/v1/purchases, /purchases/{id}/post, /suppliers |
| Finance | /api/v1/expenses, /accounts, /reconciliations |
| OCR | /api/v1/documents, /documents/{id}/review |
| Owner/AI | /api/v1/dashboard, /briefings, /assistant/queries, /recommendations |
| Approval | /api/v1/approval-requests/{id}/approve |
| Offline | /api/v1/sync/push, /sync/pull |
| GST | /api/v1/gst/readiness, /gst/exports |

All business endpoints require server-resolved membership and capabilities. Use decimal strings, timezone-aware timestamps, schema validation, bounded pagination and structured errors. Do not accept tenant scope independently of authenticated membership. Operational APIs must reuse the implemented authentication, isolation and capability checks. Paginated catalog/transaction contracts will precede their implementation.

Financial commands require Idempotency-Key; identical replay returns original result, changed payload rejects conflict. Use row versions/If-Match for editable drafts and approval proposals. Posted records have reversal endpoints, no delete. Atomic posting includes stock, tax, payment, audit and outbox. Return quantities cannot exceed sold-minus-returned quantities.

Errors: 400 invalid command, 401 unauthenticated, 403 denied, 404 unavailable scoped resource, 409 version/idempotency/duplicate conflict, 422 validation, 429 rate limit, 503 external service unavailable. Responses must not leak another tenant's record existence or provider secrets.

Contract changes require API tests and frontend integration updates in the same increment. Generate frontend types from OpenAPI before adding operational endpoints.

## Implemented reviewed purchase contracts
All routes are under `/api/v1/businesses/{business_id}/stores/{store_id}/purchases`; require purchases.manage and assigned-store scope. Mutations require session/Origin/CSRF. POST /preview validates and calculates without writing stock, purchases or audit. POST to the base path requires confirmed=true and Idempotency-Key UUID; it commits purchase/items, linked stock and audit atomically. Identical retries return the original purchase (201); changed request/key combinations return 409. GET base lists up to 200 (default 50) with offset; GET /{purchase_id} returns a scoped posted record. Reads load item snapshots in batches.

Input: supplier_id, invoice_number, invoice_date, tax_mode (exclusive/inclusive), tax_kind (intra/inter), signed round_off (-1.00 to 1.00), printed invoice_total, review_reason, confirmed and 1-200 lines. Lines: existing product_id, supplier_description, purchase_unit, purchase_quantity, units_per_purchase, free_stock_quantity, conversion_evidence, unit_rate (up to six decimal places), discount (line amount), gst_rate, optional hsn/batch_number/expiry_date. All numerical money/rate/quantity inputs are strings. Integer purchase units and piece/pack stock reject fractional counts; conversion results cannot exceed three decimals. Source evidence and explicit review are required, not an automatic inferred carton multiplier.

Responses serialize decimal strings and show stock quantities, taxable amounts, split taxes, total and precise unit-cost basis. Posted responses also return retained source/conversion fields, supplier/product snapshots, line/batch/movement IDs and review/audit attribution. Price masters stay unchanged. Total mismatches reject posting; the supported calculation policy and limitations are in purchase-entry.md. Supplier payments are now linked through the commerce endpoints below. Purchase deletion/reversal remains unavailable.

## Implemented expense and cash contracts
Paths follow `/api/v1/businesses/{business_id}/stores/{store_id}`. All use authenticated tenant/store authorization; mutations require Origin/CSRF, confirmed=true and Idempotency-Key UUID. Replays check canonical payload/target/store hash and current authority. Cashier session/closing replays cannot reveal or take over another user's command. Conflicting keys or references return 409; validation/insufficient funds return 422. All document/movement/audit work commits together or rolls back.

| Method | Path | Behavior |
|---|---|---|
| GET/POST | /accounts | First 200 account metadata/balances; create requires actions.approve; cash requires same-store terminal and opening funds/reason |
| GET/POST | /expenses | expenses.manage; paid amount, date/category/description, account, unique store reference and cash_session_id for cash |
| POST | /expenses/{id}/reverse | actions.approve; full linked reversal/reason; cash reversal requires a currently open session on the same account |
| GET/POST | /money-movements | finance.read for listing; actions.approve for manual receipt/withdrawal; these are not POS sales/refunds |
| GET/POST | /cash-sessions | Own sessions for cashiers, broader visibility for expense/finance roles; opening requires cash.sessions, terminal drawer and matching opening count |
| POST | /cash-sessions/{id}/close | cash.sessions; own cashier or owner; expected_cash, actual_cash and reason; stale expectation returns 409 |
| POST | /accounts/{id}/opening-variance | actions.approve; closed cash drawer only; reviewed expected/actual/reason; difference kept separate from receipts/expenses |

Expense/movement/session lists use limit (1-200, default 50) and offset (0-100000). Cashier account responses hide noncash opening/balance values as null. Session records derive open expectation from recorded session movements; closed expectation/actual/variance remain snapshots. Noncash movements reject cash session IDs. Closed sessions cannot receive new movements. Money is decimal text with two places; expense/manual movement amounts must be positive. Internal movement signs determine recorded account direction. Supplier payment recording is now available below. Payment-provider validation remains unavailable.

## Implemented online POS and supplier payments
All routes share `/api/v1/businesses/{business_id}/stores/{store_id}` and tenant/store authorization. Mutations require Origin/CSRF. POST commands require Idempotency-Key UUID except the read-only preview; same-body retries return the original document, altered details return 409.

| Method | Path | Capability / behavior |
|---|---|---|
| POST | /sales/preview | sales.create; validated GST-inclusive quote without writing |
| GET/POST | /sales | sales.create; posted receipts or atomic checkout, confirmed=true |
| GET | /sales/{id} | sales.create; same-store immutable receipt for reprint |
| GET | /supplier-payables | purchases.manage, finance.read or actions.approve; invoice amount, paid and outstanding |
| GET | /supplier-payments | finance.read; recorded payment history |
| POST | /supplier-payments | actions.approve; explicit invoice-linked payment and source debit |

Sale input: terminal_id, 1-200 distinct product lines (product_id, quantity, expected_price, absolute discount), bill_discount, up to 10 distinct account payments (account_id, cash_session_id if cash, method cash/upi/card, amount), confirmed. Decimal input strings are mandatory. Prices must match the current catalog; discounts must preserve minimum selling price. Split payments must equal the bill total. Cash account/session/terminal and cashier ownership must match; UPI/card account kinds must match. Insufficient nonexpired stock rejects the entire transaction. Receipts retain product/tax/discount/payment snapshots and do not expose unit costs. GST split is intra-state CGST/SGST for this local retail increment; interstate/customer credit remain deferred; owner-reviewed returns are available below. Receipt identifiers are unique, with statutory invoice-format review still required before live use.

Supplier payment input: purchase_id, account_id, optional cash_session_id, positive amount, reference, reason, confirmed=true. Payments allocate to one invoice, supporting partial settlement. Amount above outstanding returns 409; insufficient recorded funds returns 422; duplicate normalized supplier/reference returns 409. The API records an already-made payment; it never initiates a transfer. Owner-reviewed reversal endpoints are documented below; posted records have no delete endpoint. Lists use bounded limit/offset.

## Implemented corrections and offline pilot
All paths below share `/api/v1/businesses/{business_id}/stores/{store_id}`. Resolve authenticated tenant/store permissions before accessing UUIDs. Mutations require Origin/CSRF, explicit confirmation and UUID Idempotency-Key; preview has no idempotency key or writes.

| Method | Path | Contract |
|---|---|---|
| POST | /sales/{id}/credits/preview | Owner actions.approve; CreditInput with kind return/cancel, reason, confirmed, product/quantity/restock-or-discard lines; returns exact original-value credit lines; payments ignored in preview |
| GET/POST | /sales/{id}/credits | Owner approval; POST requires refund tenders equal calculated total, with sufficient funds/open session for cash |
| GET | /corrections | Owner-approved correction history, bounded pagination |
| POST | /supplier-payments/{id}/reverse | Owner; reason/confirmed and currently open original-account session when cash |
| POST | /purchases/{id}/reverse | Owner; reason/confirmed; requires no net payments and no consumed original stock |
| GET | /stock-batches | inventory.read; first 200 batch balances, decimal-string quantities |
| POST | /stock-batches/{id}/adjust | Owner; expected_count/actual_count, added_unit_cost for increase, reason/confirmed; stale count/reserved stock conflicts rejected |
| GET/POST | /offline-leases | sales.create; prepare own terminal/open cash session, distinct product quotas, reason/confirmed; reserve stock/cost without sale |
| POST | /offline-leases/{id}/sales | Persisted receipt ID equals request key; sequence/time, decimal quantity/expected_price, total/received cash, reason/confirmed; no discounts; owner recovery_reason for unusual clock/operator |
| POST | /offline-leases/{id}/finalize | Explicit last_sequence/reason/confirmed; release unused quotas only after journal count matches |

Offline responses expose frozen catalog, remaining quota, expiry, sealed flag and synced_sequence; no cost data. Expiry prevents new local sales, not recovery of previously saved receipts. Same financial receipt replays with original ID; altered financial fields are rejected. Recovery approval is audited separately and does not alter its financial fingerprint. Unsealed leases block cash closing. Finalization is locked in the local journal before sending, retaining its command across reload/response loss. No API deletes a pending receipt.

## Phase 2 development decision
The user deferred physical stock, staff and hardware acceptance to the final build and authorized Phase 2. See [Phase 2 architecture and increment plan](phase2-ocr.md) for encrypted document intake, real PaddleOCR provider boundaries, confidence/evidence review and purchase-posting gates. Physical acceptance remains required before live use. OCR and document uploads never silently create products, change prices or update stock.

### Private invoice intake API (increment 1)
Root `/api/v1/businesses/{business_id}/stores/{store_id}/ocr-documents`:

| Method | Suffix | Contract |
|---|---|---|
| GET | (root) | Metadata only; limit 1-200, offset 0-100000, newest first |
| GET | /provider | Upload/encryption availability, declared model, configuration; no successful-health claim |
| POST | (root) | Raw JPEG/PNG/PDF bytes, filename and data_origin query, CSRF/Origin and UUID Idempotency-Key; maximum 10 MiB/10 PDF pages |
| GET | /{id} | Status plus decrypted latest OCR evidence only within authorized scope |
| GET | /{id}/content | Authenticated safe image derivative; `original=true` downloads preserved original; PDF attachment only |
| POST | /{id}/process | Review reason plus idempotency key; unavailable=503, concurrent attempt=409; returns review_required/failed; no financial posting |
| GET | /{id}/matches | q and optional supplier_id; advisory existing-product candidates; match_score is not calibrated confidence |
| POST | /{id}/mappings | Owner actions.approve, supplier/product/description/reason and confirmed=true; append-only approved alias, no stock mutation |

All routes require purchases.manage except mapping approval, and verify store assignment before evidence access. API responses are no-store/nosniff. Duplicate original content within a store returns 409; original-key retry returns the stored document. Unknown OCR confidence is JSON null with review_required, not fabricated certainty. Failed/expired attempts remain immutable; explicit retry records interrupted-worker resolution in AI-source audit. Structured invoice field extraction/review and linked purchase posting remain pending.


### Genuine private OCR and reviewed purchase posting

`POST .../ocr-documents/{document_id}/process` now accepts an idempotent request and
returns HTTP 202 with `processing`. Poll document detail for `review_required`,
`failed` or `retry_due`; background inference holds no database connection.
`GET .../ocr-documents/provider` verifies the pinned model/version at the configured
loopback worker; configuration alone is not readiness.
`GET .../ocr-documents/{document_id}/draft` derives unreviewed source-linked header
and HTML/Markdown table proposals from retained genuine evidence. Missing or
ambiguous fields stay null. Similarity and confidence remain distinct.

Purchase preview and posting accept optional paired `source_document_id` and
`source_attempt_id`. The latest attempt must have completed evidence in the same
store and must not already be posted. Posting requires the existing explicit
`confirmed=true`, reviewed product IDs, pack conversions and decimal tax/total
validation. Its transaction also inserts the immutable approval/source link.
Exact idempotent replays return the original purchase; a second purchase for the
same original is rejected. Existing manual purchases remain supported. Original
OCR values can be corrected during human review; the source evidence remains
immutable and the reviewed payload is audited. This is an approved purchase and
supplier payable, not evidence of payment or current physical opening stock.


## Gemini structured extraction

The existing /ocr-documents/provider response now identifies the selected model/provider and external_processing. /process still returns HTTP 202; Gemini receives bounded validated bytes. /draft returns optional per-row extraction matching the owner pack schema and proposal fields for purchase quantity/unit/conversion. No direct stock-write endpoint is added. See [Gemini extraction](gemini-extraction.md).
