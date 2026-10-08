# Security

## Implemented identity foundation
Accounts use salted scrypt password hashes (N=131072, r=8, p=1); unknown accounts incur comparable verification work. Sessions use random 256-bit opaque tokens in HttpOnly/SameSite=Strict cookies, with only SHA-256 digests stored in PostgreSQL. Sessions expire after eight hours, are revoked on logout and reject inactive users. Secure cookies are the default; explicit local development HTTP is documented separately. Production configuration rejects insecure cookies and non-HTTPS or wildcard origins.

Mutations check an explicit allowed Origin and a session-bound CSRF token; login/registration also require an allowed Origin. Authentication attempts are throttled in committed database transactions by client address and normalized email. Configure a trusted reverse proxy before public deployment so address-based limits are meaningful, with edge request/body/concurrency limits to protect password hashing.

Each business request resolves authenticated membership and checks capabilities on the server. Restricted staff see assigned stores. Cross-business access returns unavailable; composite tenant foreign keys and forced PostgreSQL RLS reject invalid references. Transaction-local context is verified against concurrent pooled connections. The API rejects superuser, BYPASSRLS and business-table-owner runtime connections. Migration credentials never belong in the API.

Audit and security records reject UPDATE/DELETE through privileges and database triggers. Setup, terminal and staff-grant changes have human-source audit entries committed atomically with the change. Sessions and user accounts are global identity records accessible only through explicit identity services, not a general database endpoint.

This is a development increment, not a production security certification. TLS termination, encrypted disks/backups, field encryption/key management, MFA, email verification, password recovery, member revocation and administrative session management remain pilot prerequisites. No document upload/OCR endpoint is available yet.

## Catalog and opening-stock controls
Catalog, supplier, barcode, batch and movement tables force tenant RLS and composite foreign keys. Inventory views check assigned stores on every request. Supplier access requires purchase permissions; cashiers receive null cost/margin fields and cannot write the catalog. All writes enforce the shared authentication/CSRF boundary.

Opening stock requires the owner's actions.approve capability, explicit checked confirmation, a bounded reason and idempotency UUID. Transaction locks serialize replay and product/lot creation; mismatched retries and repeat lot openings fail. Database triggers and runtime privileges prohibit batch/movement UPDATE/DELETE. Approval, original quantity/cost and actor are recorded alongside atomic audit evidence. Direct opening is a human-approved command; no AI agent or background job can call an unrestricted stock-write endpoint.

Future adjustments, price edits, payments and reversals must add their own reviewed authorization/approval contracts rather than bypassing these records. Request keys in this browser flow protect retries during the current page session; this is not offline POS durability or completed-sale synchronization.

## Phase 1 prerequisites
Complete MFA for owner/admin, verified invitations, safe member-role changes/revocation, session/rate-counter cleanup and operational approval policies before a public pilot. Schema-scoped grants in production should allow only required operations; the development provisioning helper is not production automation. Health endpoints do not assert that security or financial workflows are production-ready.

Immutable financial/stock/audit records are enforced with privileges/triggers, not UI conventions. Approval transactions check current authority, versions and amount limits. Platform support access is explicitly granted, limited and audited. Secrets never enter logs, browser bundles or source control. Use TLS, encrypted storage/backups and restricted field encryption as appropriate.

## Upload/AI controls
Private quarantine, signature and type allowlist, size/page/decompression limits, malware scanning, sandboxed rendering/OCR, short-lived authorized downloads. Never trust filenames or supplied MIME. No arbitrary external URLs to prevent SSRF. See [OWASP upload guidance](https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html).

Invoice text and model outputs are untrusted. Tool allowlists, validation and server authorization prevent prompt injection from becoming a database action. Provider failures do not bypass confidence/review. Do not send unnecessary personal information to external models.

## CI/release
Least-privilege GitHub token, no production secrets on pull requests, lockfile installs, vulnerability checks and no automatic publishing/deployment. Configure protected main branch and require CI gate after repository creation. Container processes are non-root. CI test credentials are disposable, not production credentials.

Before pilot: secret scanning, tenant authorization/IDOR tests, upload hostile-file tests, security review, backup/restore drill, offline credential-loss/revocation policy and retention/accountant review. Before production: documented rollback/forward recovery, encryption/key rotation, monitoring and incident response.

## Reviewed purchase posting controls
Purchase routes resolve authenticated membership and purchases.manage before store authorization. Purchase and item tables force tenant RLS; composite foreign keys align store/product/batch/movement references. Immutable triggers and development/runtime privilege revocation reject UPDATE/DELETE. Explicit confirmation, documented rounding, invoice identity uniqueness and request hashing prevent silent changes or double posting. Product locks are acquired in UUID order to serialize lot creation without reversed-product deadlocks. Invoice, stock and audit rollback together on failure. UI retries retain the same key while mounted; this does not constitute offline durability. Unsettled invoice amounts must not be represented as completed supplier payments. Owner-reviewed unused-purchase reversals are now available; used goods need a separate reviewed supplier-return workflow.

## Expense and cash boundary
Owner-only actions.approve gates account openings, manual receipts/withdrawals, expense reversals and pre-opening count adjustments. expenses.manage gates ordinary confirmed paid expenses. cash.sessions gates own cashier opening/closing; owner may close another cashier's session with explanation. Existing roles receive the new capability through migration. Cashier reads hide noncash balances and expose only own sessions; UUID/key knowledge is not permission to replay another cashier's command.

Six forced-RLS tables use tenant/store/account/session foreign keys, immutable triggers and revoked runtime UPDATE/DELETE. Shared account advisory locks protect both API checks and DB movement/session guards without granting account UPDATE privileges. Duplicate/replayed commands and stale closing expectations are rejected; no partial expense or cash variance survives a failed audit. Opening balance, other receipts/withdrawals and count variance are distinct from sales/profit. This is local recorded finance, not bank verification or offline POS. Owner approval of cash count differences records the explanation and does not imply fraud.

## POS and supplier-payment controls
Online checkout requires sales.create and the assigned store. A cashier cannot use another person's cash session or another terminal's drawer. Supplier payments require actions.approve and explicit confirmation, with current authority checked on every retry. Product cost allocations are never included in cashier receipts. Forced RLS, scoped composite foreign keys, append-only triggers and revoked mutation privileges cover all five commerce tables. Database guards complement API locks and input validation. There are no deletion, price-change, automatic bank-transfer or supplier-payment reversal endpoints in this increment.

The browser persists the submitted command/key under a user/business/store/module key before sending it, blocks a new command while unresolved and retries the original payload after reload. It stores transaction data but no password, session cookie or CSRF token. Storage failure blocks submission. This recovery mechanism is not an offline ledger: multi-tab/device locking, durable offline transactional storage, crash reconciliation and conflict resolution remain acceptance work. Use a dedicated cashier profile/device until those gates pass. UPI/card receipt is manually verified, with no provider-settlement claim.

## Correction/offline pilot controls
Owner actions.approve and explicit confirmation gate refunds, count adjustments and reversals. Server-side tenant/store checks apply before replay; immutable compensating entries retain reasons/actor/audit and original records. PostgreSQL independently validates return stock/refund and offline-consumption links, held stock and cash closing.

Offline profiles/journals are AES-256-GCM encrypted with per-profile random salt and per-write IV, a non-extractable key derived through PBKDF2-SHA256 (310,000 iterations), and authenticated business/store/operator/version metadata. Device passphrase/key are not sent to the server or persisted in plaintext. Minimal profile labels/UUIDs remain visible on the local unlock list. Strict IndexedDB transaction completion precedes success/printing; compare-and-swap protects competing tabs. Unconfirmed finalization remains locally locked and recoverable. Service worker caches only the public shell/hashed assets, never API/auth responses; build hashes version its shell.

Offline grants last at most eight hours or the next India-day boundary, whichever comes first; fixed prices/cash/quota only. Revocation cannot instantly reach disconnected devices: protect the terminal, minimize quotas, sync before closing and review owner recovery. Idle lock is 15 minutes. Wrong passphrase, tampering and attempted backup overwrite are rejected. Device/browser storage deletion, forgotten passphrase and power failure remain deployment recovery risks; export encrypted journals and test target hardware. Do not rely on browser storage alone for unvalidated production durability.

`scripts/pilot_backup_restore.py` is a local-only admin drill: exported consistent snapshot, private ignored dump, unique newly created restore database, all-row digest equality, non-bypass runtime isolation checks, cleanup restricted to that generated database. Dump encryption, off-device retention, key custody, automatic scheduling and production restore runbook remain release configuration work. Browser traces/private bills/backups are excluded from Git and container context. No live payment gateway, OCR upload, legal filing or production deployment is claimed.
