# Reviewed purchase entry: Phase 1 increment

## Behavior
In Store operations -> Purchases, select a store and supplier, enter the printed invoice identity/date/total and its rate/tax mode. Search existing products by name or SKU. Keep original descriptions; do not create duplicate products. Add each invoice row, including legitimate repeated products. Barcodes/scanner hardware are optional for this workflow and are deferred by the user.

Each line records purchase quantity, explicit stock units per purchase unit and free stock units. For two cartons of 24 pieces, enter quantity 2 and factor 24. If three additional pieces are paid, enter a separate piece row with factor 1 and its billed rate; free quantity is reserved for actual free stock. Conversion evidence must state the source of the factor. A carton is not a universal product unit, and the system does not infer a multiplier from an ambiguous column. Phase 1 entry is manual and reviewed; invoice OCR is Phase 2.

Review purchase totals shows incoming stock and taxes. Confirmation is cleared when details change. Posting creates immutable purchase/header, line/tax/cost/conversion snapshots, batches as needed, one linked purchase movement per row and an audit record in one database transaction. Existing opening lots are reused. Product selling/purchase prices are not changed. Source rates accept up to six decimal places. No supplier payment is implied or recorded.

## Supported decimal policy
All calculations use Decimal with explicit ROUND_HALF_UP; no monetary float arithmetic. Stock = purchase quantity x conversion + free stock units, exact to at most three decimals. Piece/pack stock must be whole. The same purchase and stock unit requires factor 1.

Gross line amount = purchase quantity x source unit rate, rounded to two decimals. Subtract the monetary line discount before tax. For exclusive rates, tax is rounded taxable value x configured rate / 100. For inclusive rates, taxable value = discounted amount / (1 + configured rate / 100), rounded to two decimals, and tax is the remainder. Inter split records tax as IGST. Intra split rounds half of tax to CGST and records the remainder as SGST so component sums are exact. These are implemented arithmetic conventions using reviewed invoice settings, not legal tax-classification advice.

Header amounts sum the line snapshots. Explicit signed invoice round-off of at most INR 1.00 requires review notes; the calculated total must equal the entered printed total. A mismatch blocks posting. Do not manipulate round-off to hide a quantity/rate/tax problem. Unit cost is tax-exclusive taxable value / received stock, rounded to six decimals, with overflow checks. It is a cost basis snapshot, not final landed cost, tax-credit eligibility, profit or a complete valuation engine.

## Current limitations
No invoice-level discounts/extra charge allocation, supplier payment, purchase return/reversal, correction, automated source-field extraction or calibrated OCR confidence. Invoice totals with tax-component conventions different from this policy need separate reviewed support; do not force their source amounts to match by guessing rates. Real invoices containing three-decimal printed tax amounts retain their private originals; the current document money policy is two decimals. No claim that every supplied invoice can already be represented exactly.

Posted documents cannot be edited/deleted. Corrections and supplier returns are a next increment before live-store use. The page retains a request key for lost-response retries while mounted, and supplier/invoice uniqueness prevents double stock on a fresh retry key. It is not offline purchase entry or durable POS. Use a separate DEMO business for synthetic records. No dataset is loaded automatically and no real purchase bill has been posted during development.

## Validation
Real PostgreSQL tests exercise forced RLS, role/store boundaries, snapshot retention, pack conversion, repeated rows, lot reuse, financial-year duplicate protection, concurrent retries, immutable records, and audit-failure rollback/retry. Decimal tests cover weighted quantities, free units, inclusive/exclusive and configured split taxes, rounding, unsupported precision and overflow. UI tests verify review-before-confirmation, invalidation after edits, unchanged retry keys and success retention on a failed audit refresh. The full Phase 1 release still requires expense/cash/POS, reversals/corrections, offline durability and pilot acceptance.
