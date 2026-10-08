# Phase 1 synthetic reference dataset

All records are invented and explicitly labelled synthetic. The seven original supplier bills remain private evidence in Purchase_Bills_Demo; none of their identifiers or amounts are copied here. These files are reference fixtures, not a completed import, purchase, POS, expense or cash-management feature. No records have been posted to a business database.

## Use
Start a separate development-only business named DEMO. Map local SKU/reference values to its real IDs when the corresponding APIs exist; CSV references are not API UUIDs. Do not load these files into the real store. Opening-stock figures are invented counts, not measurements. Barcode cells are intentionally empty at the user's request. Use product search/SKU during development and validate physical scanning later.

All monetary and quantity fields are decimal strings. The artificial zero-tax scenario isolates stock, split tender, refunds, expenses and cash arithmetic; real-invoice tax and rounding validation needs separate reviewed examples. It does not establish a legal tax rate for any product.

## Daily scenario
1. Record opening stock: biscuit 40 pieces, loose rice 20 kg and soap 30 pieces.
2. Sell 3 biscuits, 2.5 kg rice and 2 soap bars for INR 305.00 total; cash INR 200.00 plus UPI INR 105.00.
3. Refund one unopened biscuit for INR 25.00 cash and return it to stock. Expected final quantities are in expected-stock.csv.
4. Record delivery INR 50.00 cash and utilities INR 500.00 bank. Only the cash expense affects the drawer.
5. With opening cash INR 1,000.00 and no other receipts, expected closing cash is INR 1,125.00. A count of INR 1,115.00 gives a INR -10.00 variance requiring an explanation. This is a review scenario, not a claim of misconduct.

Gross sales INR 305.00, refunds INR 25.00 and net sales INR 280.00 are distinct from profit. UPI does not enter the drawer; bank expenses do not reduce drawer cash.

## Pack-conversion examples
pack-conversions.csv is a separate arithmetic example, not a purchase added to the daily scenario. Two cartons of 24 pieces plus 3 loose pieces yield 51 pieces. One carton of 12 pieces yields 12 pieces. Carton size is specific to product/pack and supplier evidence; never assume a universal multiplier. Read explicit carton, pieces-per-carton and loose-piece columns from real invoices, preserve source evidence, and review ambiguous units. These invented multipliers are not mappings extracted from the seven real bills. Automated invoice extraction remains Phase 2.

## Acceptance boundaries
Use these references as features are implemented. Also verify tenant/store permissions, repeated requests, conflicting retries, over-refunds, insufficient stock, rollback, currency precision, and offline crash/replay. Synthetic data supports development; it does not certify physical inventory, scanner compatibility, OCR accuracy or live-store readiness.
