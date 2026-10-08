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

Responses serialize decimal strings and show stock quantities, taxable amounts, split taxes, total and precise unit-cost basis. Posted responses also return retained source/conversion fields, supplier/product snapshots, line/batch/movement IDs and review/audit attribution. Price masters stay unchanged. Total mismatches reject posting; the supported calculation policy and limitations are in purchase-entry.md. No delete, payment or reversal endpoint exists in this increment.
