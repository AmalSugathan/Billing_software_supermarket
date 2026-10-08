"""Synthetic OCR rows: no fabricated values, missing rows or approval bypass."""

from supermarket.invoice_draft import propose
from supermarket.ocr_provider import EvidenceBlock, EvidencePage, OcrEvidence


def evidence(text):
    return OcrEvidence(
        pages=[
            EvidencePage(
                page_number=1, markdown=text, blocks=[EvidenceBlock(label="table", text=text)]
            )
        ]
    )


def test_draft_keeps_source_and_unknown_confidence_and_checks_decimal_total():
    text = """<p>DEMO Synthetic Trading Agency</p><p>Invoice No: SYN-001</p>
    <p>Invoice Date: 03 Oct 2026</p><p>GSTIN 32ABCDE1234F1Z5</p>
    <table><tr><th>Goods Description</th><th>Qty</th><th>MRP</th><th>Rate</th>
    <th>GST%</th><th>Total Amount</th></tr>
    <tr><td>DEMO biscuits 20 pcs</td><td>2</td><td>25.00</td><td>18.50</td>
    <td>5</td><td>38.85</td></tr>
    </table><p>Invoice Amount: 38.85</p>"""
    draft = propose("doc", "attempt", "hash", evidence(text))
    assert draft.fields["invoice_number"].value == "SYN-001"
    assert draft.fields["invoice_date"].value == "2026-10-03"
    assert draft.fields["invoice_total"].value == "38.85"
    assert draft.fields["supplier_name"].value == "DEMO Synthetic Trading Agency"
    assert draft.line_total_sum == "38.85"
    assert draft.rows[0].fields["unit_rate"].value == "18.50"
    assert draft.rows[0].fields["quantity"].confidence is None
    assert draft.rows[0].fields["discount_amount"].value is None
    assert draft.rows[0].fields["free_quantity"].value is None
    assert draft.rows[0].page == 1 and draft.rows[0].block == 1
    assert draft.posting_allowed is False and draft.review_required is True
    assert not any("do not match" in warning for warning in draft.warnings)


def test_ambiguous_headers_corrupt_numbers_script_and_total_mismatch_block_review():
    text = """Invoice No: SYN-A Invoice No: SYN-B Invoice Date: 31 Feb 2026
    <table><tr><th>Description</th><th>Qty</th><th>Amount</th></tr>
    <tr><td>DEMO<script>unsafe</script> milk</td><td>1O</td><td>10.00</td></tr>
    </table><p>Invoice Total: 12.00</p>"""
    draft = propose("doc", "attempt", "hash", evidence(text))
    assert draft.fields["invoice_number"].value is None
    assert draft.fields["invoice_date"].value is None
    assert draft.rows[0].fields["quantity"].value is None
    assert "unsafe" not in draft.rows[0].fields["description"].value
    assert any("do not match" in warning for warning in draft.warnings)
    assert draft.posting_allowed is False


def test_missing_table_and_unknown_layout_are_not_empty_purchases():
    draft = propose("doc", "attempt", "hash", evidence("DEMO unreadable image"))
    assert draft.rows == [] and draft.line_total_sum is None
    assert any("No supported product table" in warning for warning in draft.warnings)
    assert draft.fields["invoice_total"].value is None


def test_fused_invoice_footer_is_not_stock_and_compound_tax_remains_advisory():
    source = """Invoice No: SYN-FOOTER Date: 05-10-2026
    <table><tr><td colspan="10">DEMO Bill To and Invoice Details</td></tr>
    <tr><th>#</th><th>Item name</th><th>HSN/SAC</th><th>Quantity</th><th>Unit</th>
    <th>Taxable amount</th><th>CGST</th><th>SGST</th><th>Price/unit</th><th>Amount</th></tr>
    <tr><td>1</td><td>DEMO spice</td><td>091091 00</td><td>10</td><td>Nos</td>
    <td>100.00</td><td>INR 2.50 (2.5%)</td><td>INR 2.50 (2.5%)</td>
    <td>10.50</td><td>105.00</td></tr>
    <tr><td colspan="2">Total</td><td></td><td>10</td><td></td><td>100.00</td>
    <td>2.50</td><td>2.50</td><td></td><td>105.00</td></tr>
    <tr><td colspan="10">Bank Details</td></tr><tr><td colspan="10">NOT A PRODUCT</td></tr>
    </table>"""
    draft = propose("doc", "attempt", "hash", evidence(source))
    assert len(draft.rows) == 1
    fields = draft.rows[0].fields
    assert fields["hsn"].value == "09109100"
    assert fields["gst_rate"].value == "5.0"
    assert fields["gst_rate"].confidence is None and "2.5%" in fields["gst_rate"].source
    assert fields["unit_rate"].value == "10.50"
    assert draft.fields["invoice_date"].value == "2026-10-05"
    assert draft.fields["invoice_total"].value == "105.00"
    assert draft.line_total_sum == "105.00" and draft.posting_allowed is False


def test_invoice_preface_and_combined_serial_description_do_not_hide_product_rows():
    source = """<table>
    <tr><td colspan="8">DEMO preface</td></tr><tr><td colspan="8">DEMO customer</td></tr>
    <tr><td colspan="8">DEMO address</td></tr><tr><td colspan="8">DEMO date</td></tr>
    <tr><td colspan="8">DEMO invoice</td></tr>
    <tr><th>SL Item Description</th><th>HSN</th><th>MRP</th><th>Qty</th><th>U.Price</th>
    <th>GST%</th><th>Net Value</th><th>Net Total</th></tr>
    <tr><td>1 DEMO biscuit</td><td>19053100</td><td>25</td><td>2</td><td>10</td>
    <td>5</td><td>20</td><td>21</td></tr>
    <tr><th>GST%</th><th>Taxable Value</th><th>SGST Amt</th><th>CGST Amt</th>
    <th colspan="4">Gross Total</th></tr><tr><td colspan="8">NOT STOCK</td></tr>
    </table>"""
    draft = propose("doc", "attempt", "hash", evidence(source))
    assert len(draft.rows) == 1
    assert draft.rows[0].fields["description"].value == "DEMO biscuit"
    assert draft.rows[0].fields["description"].source == "1 DEMO biscuit"
    assert draft.rows[0].fields["unit_rate"].value == "10"
    assert draft.line_total_sum == "21.00"
    assert draft.posting_allowed is False
