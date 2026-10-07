# API specification

## Implemented identity and setup contracts
GET /api/v1/health/live: process status; independent of database.
GET /api/v1/health/ready: verifies database connection and the known migration marker; 503 on unavailable/unmigrated database. It does not expose connection strings or raw exceptions.
GET /openapi.json: actual generated contract. No financial, stock or AI endpoints are implemented yet.

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
