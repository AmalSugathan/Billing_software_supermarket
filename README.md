# Supermarket operating system

AI-native supermarket platform for Indian retail, developed in six phases. Phase 1 currently implements accounts, business/store/terminal setup, staff permissions, products/barcodes, suppliers and immutable opening stock against PostgreSQL. Reviewed purchase entry with pack conversions, tax snapshots and atomic incoming stock is now implemented. Paid expenses, money accounts and cashier reconciliation are now implemented. POS follows incrementally; the application is not ready for live billing.

## Prerequisites
Python 3.14, Node.js 24, npm and PostgreSQL 17. Docker Desktop with Linux containers is optional for local development and required for container verification. This workspace has portable PostgreSQL and Git under ignored `.tools/`. Use development accounts until the pilot security gates pass. See [real business data](docs/real-data.md) for handling original stock invoices.

## Windows setup
From PowerShell in this directory:
```powershell
.\scripts\bootstrap.ps1
.\scripts\check.ps1
```
On this machine, Windows Application Control blocks SQLAlchemy's optional native extensions. The supported source install worked without changing security settings:
```powershell
.\scripts\bootstrap.ps1 -SourceSqlAlchemy
```
This installs the same locked version. Backend type checking uses Pyright, which runs with Node and avoids native Python type-checker modules.
If PowerShell blocks scripts, invoke the commands shown inside the files individually under your organization's policy. No permanent execution-policy change is required by the project.

## Run frontend/backend without Docker
Use two terminals. The local portable PostgreSQL instance uses port 55432. Compose uses port 5432. Set runtime credentials, not the migration/admin credentials:
```powershell
# Terminal 1
$env:DATABASE_URL = 'postgresql+psycopg://supermarket_app:local_app_only@127.0.0.1:55432/supermarket_dev'
$env:APP_ENV = 'development'
$env:COOKIE_SECURE = 'false' # local loopback HTTP only
.\.venv\Scripts\python.exe -m uvicorn supermarket.main:create_app --factory --reload --host 127.0.0.1
# Terminal 2
Set-Location apps\web
npm.cmd run dev
```
Open http://127.0.0.1:5173. Create an account, then a business and its first store. Add your real products and barcodes; review similar names before creating a distinct product. Add suppliers, then record reviewed physical opening stock from Inventory. Owner confirmation creates an immutable movement with its cost, reason and batch/expiry evidence. Different batches can have separate opening counts; each lot has one opening entry. Retries use the same request key. No sales or AI metrics are fabricated.

Business setup, terminals, staff grants and audit events are under the setup panel. Staff must register before receiving access. Cashiers can search/scan products but cannot see purchase costs or create products. Inventory managers see only assigned-store inventory; opening entries require owner approval. Price editing, corrections and purchase/expense posting are not available yet.

## PostgreSQL and migrations
```powershell
docker compose up -d database
$env:DATABASE_URL = 'postgresql+psycopg://supermarket_dev:local_dev_only@127.0.0.1:5432/supermarket_dev'
.\.venv\Scripts\python.exe -m alembic upgrade head
$env:MIGRATION_DATABASE_URL = $env:DATABASE_URL
$env:APP_ENV = 'development'
.\.venv\Scripts\python.exe -m supermarket.dev_database
$env:TEST_DATABASE_URL = $env:DATABASE_URL
.\scripts\check.ps1 -Database
# Switch DATABASE_URL to supermarket_app before running the API.
```
Integration tests create and remove randomly named schemas and non-owner test roles; the test connection needs schema/role creation privileges in a development database. `.env.example` is documentation; environment variables must be set explicitly (it is not auto-loaded). The development role helper is explicitly development-only. Production role provisioning, credentials, TLS and encrypted storage require separate setup.

The portable server already has migrations and a runtime role configured. To restart it after reboot:
```powershell
.\.tools\postgres\pgsql\bin\pg_ctl.exe -D '.tools/postgres-data' -l '.tools/postgres-server.log' -o '-h 127.0.0.1 -p 55432' start
```
This command applies only to this machine's existing portable cluster; it does not install PostgreSQL or initialize another cluster.

For the complete local container stack:
```powershell
docker compose up --build --wait api web
```
Open http://127.0.0.1:8080. Compose uses development credentials and must not be deployed publicly. Migrations run in a separate one-shot service before API readiness.

## CI
GitHub Actions checks backend on Windows/Linux, frontend, real PostgreSQL migrations/integration, dependency vulnerabilities and container builds/HTTP smoke tests. No deployment or publishing occurs. After pushing to GitHub, configure branch protection to require **CI gate**; YAML alone cannot enable branch protection.

Dependencies are pinned by `uv.lock` and `apps/web/package-lock.json`. Update through reviewed dependency changes and run the full gate. `scripts/check.ps1 -Audit` exports the locked Python dependency list and queries vulnerability services; it needs network access.

## Documentation
- [Requirements](docs/product-requirements.md)
- [Architecture](docs/architecture.md)
- [Schema](docs/database-schema.md)
- [AI agents](docs/ai-agents.md)
- [API](docs/api-specification.md)
- [Security](docs/security.md)
- [Six-phase roadmap](docs/roadmap.md)
- [Development pipeline and verification](docs/development-pipeline.md)
- [Original invoices and dataset handling](docs/real-data.md)

Repository: https://github.com/AmalSugathan/Billing_software_supermarket. Local commit attribution uses `amalsugathan123@gmail.com`; GitHub authentication is separate from the commit email.

Next Phase 1 increment: POS/tenders/returns and supplier payments, followed by durable offline synchronization. Paid expense/cash account/session behavior is documented in docs/expense-cash.md. Purchase arithmetic and supported invoice limitations are documented in docs/purchase-entry.md. Final valuation/COGS and tax-credit treatment remain prerequisites before POS/profit reporting. OCR starts in Phase 2. Synthetic operational reference CSVs are in fixtures/demo; no fixture or real bill has been automatically imported.
