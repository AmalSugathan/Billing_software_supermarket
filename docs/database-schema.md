# Database schema

## Design invariants
Every business record carries business_id. Store records carry store_id; composite tenant foreign keys prevent cross-business references. PostgreSQL RLS uses trusted request context, default deny and a non-owner runtime role without BYPASSRLS. FORCE RLS is required on tenant tables. No tenant identity comes directly from an unchecked header/body.

Use UUID identifiers, UTC timestamps, business timezone for reporting, bounded NUMERIC for money/rates and quantity precision appropriate for weighed goods. Serialize decimals as strings. Persist invoice description/price/cost/tax snapshots. Foreign-key columns and tenant/store/date access paths are indexed.

## Planned relational model
| Domain | Entities |
|---|---|
| Identity | Business, Store, Terminal, User, Membership, Role, Permission, RolePermission, MembershipStore |
| Catalog | Product, Category, Brand, Unit, Barcode, ProductAlias, TaxRate, HSN |
| Purchasing | Supplier, SupplierProductMapping, Purchase, PurchaseItem, PurchaseReturn, PurchaseReturnItem |
| Sales | Customer, SalesInvoice, SalesItem, SalesReturn, SalesReturnItem, Payment, PaymentAllocation |
| Inventory | StockBatch, StockMovement, StockAdjustment, StockTransfer, StockTransferItem, InventoryBalance |
| Finance | Expense, FinancialAccount, JournalEntry, JournalLine, CashSession, CashCount, SettlementAllocation |
| GST | GSTRecord, TaxException, TaxExport |
| AI | OCRDocument, OCRLineItem, OCRFieldEvidence, AIAction, AIRecommendation, ApprovalRequest |
| Operations | Notification, AuditLog, OutboxEvent, IdempotencyRecord, SyncReceipt |

User is global; membership and business-scoped capabilities govern access. Business -> Store -> Terminal describes operations, while Membership -> User describes identity.

## Core constraints
Unique (business_id, sku), (business_id, barcode), supplier invoice identity/financial year, issued invoice series/financial year/number and terminal transaction sequence. Products may have multiple barcodes; batch identity includes product/store/supplier batch/expiry. Posted stock movements and journal lines are append-only with linked compensating entries. Audit/outbox changes occur in the posting transaction. Payment allocations cannot exceed settlement or outstanding amount.

Stock valuation policy must be decided before purchase/sale posting. Suggested weighted-average costs require cost snapshots and deterministic treatment of backdated purchases/returns. Never recompute historical sale costs using today's product purchase price.

Revenue excludes output tax. Gross profit = net revenue - recorded COGS; expenses and net profit are separate. Cash flow derives from financial account movements, including credit settlements and payments, not just invoice totals.

## Deployed migrations
0001_pipeline_baseline creates deployment_metadata with a schema marker. 0002_identity_tenancy creates user_account, auth_session, auth_rate_limit, security_event, business, store, terminal, permission, role, role_permission, membership, membership_store and audit_log. 0003_catalog_stock adds category, brand, supplier, product, barcode, product_alias, stock_batch and stock_movement. Financial documents remain planned.

User email is normalized lowercase and unique. Sessions persist token digests, indexed expiry and user references. Rate counters persist hashed keys and timestamps. Roles and membership grants are business-scoped; terminal/membership-store references use composite tenant foreign keys. Store names are unique within a business; terminal names are unique within a store. Audit rows have actor, source, resource, before/after JSON and timestamp, indexed by business/time. Audit and security-event changes are rejected by append-only triggers.

Eight tenant tables use ENABLE/FORCE RLS, default-deny context and a non-owner runtime role. Actor-only SELECT policies permit finding the user's memberships/businesses before choosing a tenant. Other tables are global identity/configuration records, never exposed through generic table/query APIs. The immutable migration snapshot lives in schema_v0002.py; future model changes must use a new migration snapshot rather than altering historical imports.

The eight catalog/inventory tables also force RLS. Products use NUMERIC(14,2) prices, NUMERIC(15,3) reorder quantities and NUMERIC(5,2) percentages. SKU is normalized uppercase and unique per business; normalized name plus unit is unique. Multiple barcodes and aliases reference the product through tenant foreign keys. Categories, brands and preferred supplier references cannot cross businesses.

Opening counts create immutable stock_batch and stock_movement rows. Lot identity includes business/store/product/batch number/expiry; a unique lot index and partial unique opening index prohibit repeat openings for a lot. Movement identity has a tenant-unique idempotency UUID, canonical request hash, signed decimal quantity, unit-cost snapshot, reason, actor, source and human approval. Opening rows require positive quantity, human source and approval at the database level. No endpoint directly overwrites stock quantity; inventory sums movements, including products with zero stock. Reserved movement kinds are not implemented posting workflows.

schema_v0003.py is a frozen historical snapshot, separate from live catalog_models.py. Before purchases/sales, add transactional financial documents, cost policy and a tested balance projection if measured scale requires it. Opening-value reporting is explicitly restricted to opening movements and is separate from valuation, cash flow and profit.

## Migration policy
Versioned, reviewed migrations; never ORM create_all in application startup. CI tests an empty database upgrade and downgrade/re-upgrade on an isolated database. Add upgrade fixtures once historical application data exists. Production reversal is forward correction; destructive downgrades require an explicit recovery plan.

## Reviewed purchases: 0004_purchases
purchase and purchase_item are tenant-scoped with FORCE RLS and immutable UPDATE/DELETE triggers. Headers snapshot supplier identity, invoice number/date, normalized supplier invoice identity and April-to-March financial-year start, chosen tax mode/split, amounts, review reason, actor and human approval. The uniqueness key includes business/supplier/financial year/invoice identity across stores. Payment activity is not inferred from a bill or its QR code.

Items preserve supplier descriptions, purchase units/quantities, product/SKU/stock-unit snapshots, conversion factors/evidence, free stock quantity, unit rates, discounts, HSN/GST values, tax components, precise tax-exclusive unit-cost basis and batch/movement links. Composite keys enforce business/store/product alignment. NUMERIC(18,6) stores source rates and movement unit costs; document money is NUMERIC(14,2), stock quantities NUMERIC(15,3). The migration preserves earlier two-decimal costs and rejects a downgrade that would round precise values. It is frozen in schema_v0004.py, separate from the live model.

Purchase cost snapshots are tax-exclusive allocations over received stock, including free units. They are not a finished stock valuation or a statement that input GST is recoverable. Freight, nonrecoverable tax/landed cost allocations and POS cost-of-goods policy still require a later increment. No profit is calculated from these snapshots. Purchase documents and their movements/audit are one transaction; payment/journal/outbox models remain planned.

## Expense/cash schema: 0005_finance_cash
Six new append-only tenant/store tables: financial_account, financial_movement, expense, expense_reversal, cash_session and cash_closing. All force RLS and reject UPDATE/DELETE through triggers plus revoked runtime privileges. Frozen schema_v0005.py does not import live model definitions. A new terminal tenant/store/ID unique key supports drawer account references, and cash.sessions is added to existing OWNER/STORE_MANAGER/CASHIER roles without changing historical migrations.

Account balance is the sum of signed NUMERIC(14,2) movements. Opening funds occur once per account; they are not revenue. Accounts connect cash drawers to terminals, and composite keys align store/account/session/movement/expense references. Expenses retain paid-date evidence and their account/movement; references are unique per store. A full reversal records a compensating movement and original link rather than updating/deleting the expense. Unpaid expenses are deferred; invoice-linked supplier payments are added in migration 0006 below.

Session headers and unique closing records are append-only. Opening guards allow only one unclosed drawer per account and one per cashier/business and require matching counted opening cash. Account advisory locks are shared by API and database insert guards; append-only accounts retain revoked UPDATE privileges. Movement guards reject insufficient funds, overflow, noncash session links and closed drawer activity. Owner-approved opening_variance is restricted to closed cash drawers. Closing variance adjusts the ledger to the confirmed actual count, while expectation/actual/variance/reason remain immutable evidence. Count differences are neither operating expenses nor revenue. Current expense dates describe the source expense; movement created_at records when its paid entry was posted, with no retroactive session edits. Double-entry journals and COGS/valuation remain planned.

## Commerce schema: 0006_pos_payments
Five append-only FORCE-RLS tables: sales_invoice, sales_item, sales_stock_allocation, sales_payment and supplier_payment. Composite foreign keys bind business/store/product/account/movement scope. Header request keys are tenant-unique; receipts have tenant-unique identifiers, source actor/approval and amount/tax snapshots. Stock allocations retain batch, exact quantity and six-decimal ledger unit cost. Supplier payments reference a reviewed purchase and its supplier; normalized payment reference is unique per business/supplier. Indexes support store/date receipt lookup and purchase payment totals.

Shared product locks and batch guards prevent stock overselling. Supplier payment guards share invoice advisory locks with the API and verify the linked negative money movement and supplier/outstanding amount. A deferred receipt guard checks payment and item totals, allocation quantities, stock movement costs/signs and source money linkage at commit. Runtime cannot update/delete these records. Posted commerce movements block downgrade to the previous schema. Frozen schema_v0006.py retains migration definitions independently of live commerce models.

Batch cost snapshots use the moving weighted-average signed ledger value divided by remaining batch quantity, with six-decimal rounding. They are an estimate; landed-cost adjustments/input-tax eligibility, journals and profit reporting require further accounting work. Receipt value is revenue; incoming money is payment; supplier payment is a cash outflow, not an additional purchase expense or automatic profit calculation.

## Corrections and prepared offline tills (0007 / 0008)
Append-only credit_note/items/refunds/source allocations preserve original quantities, cost and exact tax components. Supplier-payment reversal restores original account/outstanding; purchase reversal and item movements preserve original invoice. Stock adjustment stores expected/actual counts and signed movement. All eight correction tables force tenant RLS and composite references; runtime UPDATE/DELETE is revoked. Source/refund/linkage and cumulative return guards reject invalid postings. Data-bearing downgrades are blocked.

Five offline tables hold leases, reserved batch quantity/cost/catalog snapshots, terminal sequence/receipt IDs, consumed quota/movement links and finalization seals. Reservations do not change stock; unsealed unused quota is subtracted from available stock and value for other withdrawals. Deferred consumption guards verify original batch/cost and posted sale linkage. Seals release remaining quota, and cash closing guards reject unsealed leases. Expiry never silently releases potentially sold but unsynchronized units. All tables force RLS/immutable guards and runtime privilege restrictions. Frozen schema_v0007/schema_v0008 retain migration definitions. Monetary and quantity fields remain PostgreSQL NUMERIC and API decimal strings.
