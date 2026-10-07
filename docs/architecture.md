# Architecture

## Decision
Use a modular monolith: Python/FastAPI, SQLAlchemy/Alembic, PostgreSQL, React/TypeScript/Vite. Python keeps later OCR/data/AI work in the same language as the backend; TypeScript supports the cashier and owner UI. One transaction commits financial documents, stock movements, audit events and outbox events. Background workers process outbox events independently of checkout.

The current Phase 1 increment implements identity, business/store/terminal setup, scoped staff permissions and append-only audit records. Operational posting modules are not implemented yet. PostgreSQL is the authoritative database; SQLite is only a possible local POS store and is not a replacement for PostgreSQL isolation tests.

Identity uses opaque, server-managed sessions with only token digests persisted, scrypt password hashing and explicit Origin/CSRF checks. Capability checks resolve authenticated membership before selecting a business. Transaction-local PostgreSQL context resets on commit/rollback, including pooled connections. A non-owner runtime database role is distinct from the migration administrator; the API rejects unsafe runtime roles. Store restrictions apply within an authorized business.

## Modules
Identity/tenancy, catalog, POS, inventory, purchasing, suppliers/customers, expenses/finance, GST, reporting, AI orchestration, notifications and audit. Each exposes application services and typed contracts; other modules cannot directly mutate its tables. Keep domain calculations independent of HTTP/database dependencies.

## Transactions and events
Posting services lock/version affected records and enforce permissions, idempotency and state transitions. Commit invoice/items/tax/tenders/stock/audit/outbox together. Consumers deduplicate event IDs; delivery is at least once. Never promise exactly-once transport. Rebuild stock projections and reconcile against the ledger.

## Offline design (Phase 1 release prerequisite)
Local terminal runtime and durable SQLite transactions commit sale + tenders + stock + audit + sync envelope before printing success. Unique terminal sequence and client transaction IDs enable replay-safe server posting. Retain acknowledged receipts. Product edits use version conflicts; completed sales are retained and reconciled, not overwritten. Offline payment verification, credential expiry, numbering and negative-stock conflicts require explicit policies. Browser storage alone cannot satisfy the durability claim without a validated deployment strategy.

## Layout
```
apps/api/src/supermarket/  # API and module/application boundaries
apps/api/migrations/      # reviewed Alembic migrations
apps/api/tests/           # API, domain, database integration tests
apps/web/src/             # owner/POS frontend, feature-oriented modules later
scripts/                  # reproducible bootstrap and validation
.github/workflows/        # CI gates, no automatic production deployment
docs/                     # requirements, decisions and phase evidence
```

## Development and CI
Pin Python 3.14 and Node 24. Commit hashed Python dependency lock and npm lock; CI installs those exact graphs. Validate lint/format, types, unit/API tests, frontend tests/build, PostgreSQL migrations and database constraints. Container builds validate packaging. Dependency audits run as a separate explicit gate. Branch protection must require the final CI gate; repository settings require authenticated administrative access to the supplied GitHub repository.

Release deployment is deferred. Future deployment uses immutable artifacts, migration preflight, backup/restore plan, staging smoke tests and explicit production approval. Never run destructive down-migrations automatically against financial data.

## References
- [FastAPI container guidance](https://fastapi.tiangolo.com/deployment/docker/)
- [PostgreSQL row-level security](https://www.postgresql.org/docs/current/ddl-rowsecurity.html)
- [GitHub PostgreSQL CI services](https://docs.github.com/en/enterprise-cloud@latest/actions/tutorials/using-containerized-services/creating-postgresql-service-containers)
