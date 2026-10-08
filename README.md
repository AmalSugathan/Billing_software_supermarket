# Supermarket operating system

AI-native supermarket platform for Indian retail, developed in six phases. Phase 1 currently implements accounts, business/store/terminal setup, staff permissions, products/barcodes, suppliers and immutable opening stock against PostgreSQL. Reviewed purchase entry with pack conversions, tax snapshots and atomic incoming stock is now implemented. Paid expenses, money accounts and cashier reconciliation are now implemented. Online POS checkout, receipt reprinting and owner-confirmed invoice-linked supplier payments are implemented. Offline billing, returns/corrections and pilot release gates remain; the application is not ready for live billing.

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

Business setup, terminals, staff grants and audit events are under the setup panel. Staff must register before receiving access. Cashiers can search/scan products but cannot see purchase costs or create products. Inventory managers see only assigned-store inventory; opening entries require owner approval. Reviewed purchases, paid expenses, cash management, online POS and supplier payments are available in Store operations. Price editing and transaction/stock corrections remain deferred.

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

Current Phase 1 work includes operational posting, controlled corrections and the automated offline pilot. See [pilot validation](docs/pilot-validation.md) for evidence and remaining store/production gates. Original bills are privately uploaded into a separate Phase 2 review inbox; original stock and financial posting remain pending. Synthetic references are in fixtures/demo and the isolated walkthrough is available in the UI.

## Online billing and invoice payments
Open **POS billing** in Store operations, choose a terminal, search/add products, review quantities/discounts, add cash/UPI/card payment splits and confirm checkout. Cash needs your own open drawer. Receipts can be reprinted with browser printing; thermal hardware has not been verified. **Supplier payments** lists reviewed invoices with paid/outstanding amounts and lets owners explicitly approve a payment against an invoice. These flows use real PostgreSQL stock/money ledgers, not UI mock results. See [POS and payments](docs/pos-payments.md).

A separate synthetic walkthrough can be created locally with `.venv/Scripts/python.exe scripts/seed_phase1_demo.py --email YOUR_EMAIL`. It asks for the portal password without saving it, refuses to add another identically named demo business, and never touches an existing shop's stock. Select **DEMO Phase 1 walkthrough** after refreshing the portal. This example has a INR 305 split-payment receipt, two paid expenses, a 51-piece carton purchase and a partial supplier payment. It intentionally does not seed refunds/closing because those reference CSVs describe a broader future acceptance scenario. Original bills are not imported by this script.

## Corrections and offline pilot
Owners can open **Corrections & returns** to calculate/refund returned goods, cancel full receipts, reverse payments/unused purchases and approve physical-count deltas. Original documents remain immutable.

For offline use, open the built app at http://127.0.0.1:8080 (or Docker Compose), prepare your open cash till through **Offline preparation**, then open `/offline-pos`. New offline sales use fixed prepared prices, cash and reserved quotas. Protect the device passphrase/export encrypted journals; synchronize/finalize before closing. Port 5173 is the development UI and does not provide a production offline shell. Physical hardware, true crash/power-loss acceptance and production security/backup configuration remain release gates.

Run `npm.cmd --prefix apps/web run test:e2e` against the running built app/API; Windows can set `PILOT_BROWSER_CHANNEL=msedge`. Remote CI adds **Offline browser pilot** to its required gate. No live deployment occurs.

## Phase 2 invoice intake

The first OCR increment adds an encrypted private invoice inbox and the real PaddleOCR-VL-1.6 HTTP adapter. See [Phase 2 implementation and setup](docs/phase2-ocr.md). Original bills can be reviewed without importing stock or payments. Actual inference and structured invoice-to-purchase posting remain pending; an unavailable provider is shown explicitly.
