# Development and CI pipeline

## Scope delivered
The seven prerequisite documents and development/CI scaffold are saved. Phase 1 now adds real account/session APIs, business/store/terminal setup, seven capability-based roles, scoped staff grants, PostgreSQL tenant isolation and append-only audit records, with a working React setup interface. The second increment adds products/barcodes/aliases, categories/brands, suppliers, fuzzy duplicate review and owner-confirmed immutable opening stock. Financial posting, POS and AI integrations remain subsequent increments.

## Pipeline
```
Feature acceptance criteria + phase
  -> migration + domain service + permission checks
  -> API + frontend + edge-case tests
  -> local checks
  -> pull request
  -> workflow lint | backend Windows/Linux | frontend
     | PostgreSQL migrations | dependency audit | container smoke tests
  -> CI gate
  -> human review + merge
```
Production deployment is deferred. A future release pipeline must promote reviewed immutable artifacts through staging, backup/migration preflight, smoke tests and production approval. The current workflow never publishes images or deploys.

## Reproducibility
Python 3.14, Node 24, uv 0.12.23. Python package versions/artifact hashes are in uv.lock; frontend and Pyright integrity hashes are in apps/web/package-lock.json. CI uses uv sync --locked and npm ci. Review dependency changes as code; do not use unreviewed npm audit fix --force.

Pyright provides strict Python type checks and runs via the frontend tooling package; it is a development dependency, not browser code. Ruff handles Python lint/format. ESLint/TypeScript/Vitest handle frontend checks. Pytest enforces at least 90% backend coverage in the full real-PostgreSQL job. Unit-only jobs validate their subset without claiming database coverage. Coverage does not establish production business correctness.

GitHub action major versions and container tags currently track their maintained releases; they are not immutable digest pins. Before production releases, pin reviewed action commit SHAs and base-image digests and add an automated update process.

## Local commands
Windows: scripts/bootstrap.ps1 followed by scripts/check.ps1. On this machine SQLAlchemy's optional native extensions are blocked by Application Control. scripts/bootstrap.ps1 -SourceSqlAlchemy installs the same locked version using its supported pure-Python build. This does not change OS security settings. See [SQLAlchemy source-install documentation](https://docs.sqlalchemy.org/en/20/intro.html).

PowerShell execution policy currently blocks unsigned project scripts. Run individual commands listed in the script, or explicitly permit a process-scoped invocation under your local policy:
```
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\check.ps1
```
No permanent execution-policy change is made. Linux/macOS: sh scripts/check.sh with uv installed. Optional database checks require TEST_DATABASE_URL; optional audits require internet access.

## Current verification evidence
Executed on Windows, 7 October 2026:
- Backend Ruff lint and formatting passed.
- Backend strict Pyright: zero errors/warnings.
- Backend/API/PostgreSQL tests: 34 passed; 98.67% coverage of 752 executable statements. Real PostgreSQL 17.11 exercised migrations, password/session validation, CSRF, throttling, permissions, store scope, tenant/composite-FK isolation, concurrent pooled contexts, append-only records, exact decimal posting, duplicate prevention, concurrent stock replay, audit-failure rollback and preservation of existing business data through the new migration.
- Frontend lint and TypeScript checks passed.
- Frontend tests: 11 passed (health failure/retry, unreachable/invalid API data, identity setup, cashier controls, decimal product entry, duplicate review, scanner Enter lookup, stock retry after a lost response and genuine duplicate-supplier failure).
- Frontend production bundle built successfully.
- Fresh audits of the updated dependency graphs passed: npm reported zero vulnerabilities and pip-audit reported no known vulnerabilities. Audit observations are time-specific, not guarantees.
- Combined scripts/check.ps1 -Database passed through an explicitly approved process-scoped invocation, including real PostgreSQL and frontend build.
- actionlint validated the workflow locally using a checksum-verified official binary.

## Pending checks and activation
Portable Git is installed locally; origin is https://github.com/AmalSugathan/Billing_software_supermarket.git. Local attribution uses the requested email. Portable PostgreSQL 17.11 runs on loopback port 55432 with a separately provisioned non-owner runtime role. Neither tool is installed system-wide. Docker is unavailable locally. The first increment passed every remote GitHub CI job, including container builds/runtime. Each new commit requires its own remote run; local tests do not substitute for remote verification.

CI definitions exercise real PostgreSQL upgrade/downgrade/re-upgrade, identity/RLS integration and HTTP readiness, plus container compose/migration/startup/proxy smoke checks. No SQLite substitute marks the PostgreSQL gate passed. TEST_DATABASE_URL tests create isolated random schemas/non-owner roles and clean up those resources. Use a development admin connection with schema/role-creation permissions.

GitHub is authenticated and the first increment was pushed successfully. Push each verified increment, review its remote CI results, resolve runner-specific failures, and protect main with the CI gate required. Container and all remote jobs must pass before remote verification is claimed. The workflow does not deploy the application. Replace development credentials and provision production least-privilege roles separately before deployment.

## Next authorized phase boundary
Identity/setup, capability authorization, tenant-isolation tests and audit form the first completed Phase 1 development increment. The second increment adds catalog/barcodes/suppliers and immutable opening movements. Next: purchase/expense postings, POS/cashier sessions and offline reliability. OCR remains Phase 2; owner intelligence Phase 3; agents Phase 4; GST intelligence Phase 5; prediction Phase 6.

## Current Phase 1 pilot pipeline
The latest increments add corrections (0007) and prepared offline tills (0008). Run `scripts/check.ps1 -Database` for lint/format, the real PostgreSQL regression suite/coverage, API/frontend types, frontend tests and production build. The new **Offline browser pilot** remote job starts the real container stack and uses Chromium to disconnect/reload a prepared till, retain a saved cash receipt, drop a committed sync response, replay once, finalize, refund, close and time twenty synthetic checkout requests. It is required by CI gate. Windows local validation uses installed Edge via PILOT_BROWSER_CHANNEL=msedge; Playwright is a dev-only dependency.

Run `npm.cmd --prefix apps/web run test:e2e` against built loopback port 8080 (preview proxies to API port 8000). Vite development port 5173 does not cache its HMR shell; the preparation UI directs users to the built app. Test data is isolated, explicitly synthetic and created through actual APIs. Traces stay private/ignored. See pilot-validation.md for measured evidence and remaining operator/production gates; no production load/hardware claim follows from a local synthetic test.
