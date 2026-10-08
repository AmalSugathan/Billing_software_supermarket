# Purchase-bill dataset assessment

Assessment date: 7 October 2026. Scope: visual review and SHA-256/file checks of the locally supplied Purchase_Bills_Demo folder, not OCR execution or financial posting. The user identifies these as real supplier bills. This assessment does not independently authenticate issuers or certify accounting/tax compliance.

## Decision
The current evidence is enough to start purchase-entry development and an initial OCR smoke test. It is not enough to close the whole Phase 1 release or establish production OCR accuracy. Phase 1 still needs purchase posting, expenses, POS/tenders/returns, cashier reconciliation, reviewed stock corrections and durable offline synchronization, plus the security/hardware/pilot acceptance gates already in the roadmap. OCR remains Phase 2.

## Inspected material
- Seven JPEG photographs, all 1200 x 1600 pixels, with seven distinct SHA-256 hashes: no byte-identical duplicates found.
- Seven visibly distinct invoices across six supplier/layout families; one supplier appears on two different invoices.
- Approximately 53 line-item rows, including repeated product rows within an invoice. These are not 53 verified distinct products.
- Single-page printed invoices with small dense tables, perspective distortion, folds, shadows, stamps, handwritten notes and payment QR codes.
- Visible fields include invoice identity/date, supplier/buyer details, HSN, quantities, rates, some MRPs, line/tax totals and final totals. Printed tax-rate groups include 0%, 5% and 18%; these are source observations, not claims about legally applicable rates.

## Consequences for Phase 1 implementation
1. Preserve supplier descriptions and original invoice units. Bag/carton/pack quantities need explicit purchase-unit to stock/selling-unit conversions. A column labelled UPC alongside case/piece quantities can mean units per case, not a product barcode. Never map this column to Barcode automatically. Physical product barcodes and pack conversion factors still need confirmation.
2. Store source rates and calculated unit costs at sufficient decimal precision. Some invoices print taxable values with three decimal places or unit prices rounded more coarsely than their line amounts. The existing two-decimal product-price/stock-cost fields must not be reused as the only precision available in a future purchase calculation engine. Keep two-decimal payable amounts and explicit reviewed rounding adjustments separately.
3. Preserve tax-inclusive versus exclusive rate semantics, scheme discounts, CGST/SGST breakdown and invoice round-off. Do not silently force line, summary and component totals to match. Record differences and require review using a documented tolerance policy.
4. Repeated SKU/product rows on one invoice are legitimate candidate rows, not necessarily duplicate invoices. Retain them; invoice deduplication uses supplier/invoice identity and evidence separately.
5. Handwritten amounts may differ from printed totals. Store annotation evidence without replacing the authoritative amount until the owner confirms its meaning.
6. Buyer descriptions vary between invoices. Confirm business identity/aliases before posting; an invoice buyer name is never permission to select another tenant.
7. Purchase history is not opening stock or current stock. Do not import these past invoices as opening movements; enter them only once through reviewed purchase posting after choosing a cutoff and reconciling the physical opening count.

## Coverage gaps
Not observed as exercised cases: nonzero free-quantity schemes, nonzero IGST supplies, real purchase returns/credit notes, multi-page invoices, batch/expiry columns, reliable product GTIN labels, fractional loose-goods invoice quantities, expense vouchers, POS sales/returns and actual cash-closing counts. Stock totals, payment proof and outstanding balances cannot be established from invoice headers alone. A displayed QR code is not evidence that payment occurred.

## Phase 1 evidence needed next
Use these seven originals as positive purchase cases with reviewed expected fields/totals. Obtain product barcodes, case/pack factors and actual opening-stock counts. Add expense, sales/refund and cash-closing examples when available; controlled, clearly labelled test scenarios should also exercise permissions, duplicate posting, reversals, concurrency and network failures. Original invoices stay unchanged and out of source control.

## PaddleOCR-VL-1.6 plan for Phase 2
The official model card confirms PaddleOCR-VL-1.6 and its document/table parsing support. Use the full document-parsing pipeline and retain page/block evidence before normalized invoice extraction, matching and validation. Its output is not an approved purchase transaction or a calibrated field-confidence score by itself. All seven originals need manually reviewed ground truth before measuring extraction accuracy.

Keep OCR behind a separate worker/provider interface. The official installation guide documents Python 3.9-3.13 as the verified range, while this API uses Python 3.14. A separate Python 3.12/3.13 environment or service avoids changing the working application runtime. Some accelerated inference frameworks require Linux/container deployment rather than native Windows. Hardware performance has not been measured here, and PaddleOCR has not been installed or run against these bills.

For a first pilot evaluation, a suggested collection target is 50-100 reviewed real invoices across suppliers, dates, layouts and image conditions. This is a proposed test target, not a guarantee or universal minimum. Keep a separate held-out set; do not distribute nearly identical invoices/photos across train and test sets. A pretrained-model smoke test needs no model training on these seven documents.

Sources reviewed:
- Official usage/environment requirements: https://www.paddleocr.ai/latest/en/version3.x/pipeline_usage/PaddleOCR-VL.html
- Official model card: https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6

## Privacy and current state
Originals contain supplier/buyer identifiers, contacts and bank/payment details. Purchase_Bills_Demo is excluded from Git and Docker contexts. This report contains no bank account numbers, GSTINs, personal contacts or invoice-level financial totals. Originals have not been sent to an external OCR endpoint, used for training, or imported into the development business database.

## User decision, 8 October 2026
For current development, use clearly labelled synthetic opening counts, expenses, sales/refunds and cash-closing examples in fixtures/demo. Barcode data and physical scanner checks are deferred. Invoice carton and pieces-per-carton details may establish conversions when explicit and reviewed; ambiguous units require confirmation. The synthetic pack examples are not extracted real-invoice mappings. This decision enables development without further data collection; live-store readiness still requires actual counts and operational acceptance checks. No fixture has been posted to the database.
