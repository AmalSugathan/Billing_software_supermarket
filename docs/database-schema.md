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
