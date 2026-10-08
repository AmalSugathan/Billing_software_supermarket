"""Advisory fields from real OCR evidence; never a purchase authorization."""

import re
from datetime import datetime
from decimal import Decimal
from html.parser import HTMLParser
from typing import Literal

from pydantic import BaseModel

from supermarket.ocr_provider import OcrEvidence


class ProposedField(BaseModel):
    value: str | None = None
    source: str = ""
    confidence: None = None
    requires_review: Literal[True] = True


class DraftRow(BaseModel):
    page: int
    block: int
    row: int
    headers: list[str]
    cells: list[str]
    fields: dict[str, ProposedField]


class InvoiceDraft(BaseModel):
    document_id: str
    attempt_id: str
    source_sha256: str
    supplier_candidates: list[str]
    fields: dict[str, ProposedField]
    rows: list[DraftRow]
    warnings: list[str]
    line_total_sum: str | None
    posting_allowed: Literal[False] = False
    review_required: Literal[True] = True


class TableText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self.lines: list[str] = []
        self.table: list[list[str]] | None = None
        self.row: list[str] | None = None
        self.cell: list[str] | None = None
        self.span = 1
        self.ignore = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.ignore += 1
        if tag == "table" and self.table is None:
            self.table = []
        elif tag == "tr" and self.table is not None:
            self.row = []
        elif tag in {"th", "td"} and self.row is not None:
            self.cell = []
            raw = dict(attrs).get("colspan", "1") or "1"
            self.span = min(20, max(1, int(raw))) if raw.isdigit() else 1
        if tag in {"br", "p", "tr"}:
            self.lines.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self.ignore = max(0, self.ignore - 1)
        if tag in {"td", "th"} and self.cell is not None and self.row is not None:
            self.row.extend([" ".join(" ".join(self.cell).split())] * self.span)
            self.cell = None
        elif tag == "tr" and self.row is not None and self.table is not None:
            self.table.append(self.row[:40])
            self.row = None
            self.lines.append("\n")
        elif tag == "table" and self.table is not None:
            self.tables.append(self.table[:201])
            self.table = None

    def handle_data(self, data: str) -> None:
        if not self.ignore:
            self.lines.append(data + " ")
            if self.cell is not None:
                self.cell.append(data)


def plain(text: str) -> tuple[str, list[list[list[str]]]]:
    parser = TableText()
    parser.feed(text)
    return "".join(parser.lines), parser.tables


def header_key(value: str) -> str:
    return re.sub(r"[^a-z0-9%]", "", value.casefold())


HEADERS = {
    "description": {
        "description",
        "goodsdescription",
        "descriptionofgoods",
        "product",
        "productname",
        "item",
        "itemname",
        "itemdescription",
        "slitemdescription",
        "particulars",
    },
    "hsn": {"hsn", "hsncode", "hsnsac"},
    "quantity": {"qty", "quantity", "billqty"},
    "free_quantity": {"free", "freeqty", "fre"},
    "purchase_unit": {"unit", "uom"},
    "mrp": {"mrp", "mrp%"},
    "unit_rate": {"rate", "price", "ptr", "purchaserate", "priceunit", "uprice"},
    "discount_percent": {"dis%", "disc%", "discount%"},
    "discount_amount": {"discount", "disamt", "discountamount", "discamt", "disc"},
    "gst_rate": {"gst%", "gstrate", "tax%"},
    "taxable_value": {"netamt", "taxable", "taxablevalue", "taxableamount", "netvalue"},
    "gst_amount": {"gstamt", "gstamount", "taxamount"},
    "cgst_amount": {"cgstamt", "cgstamount", "cgst"},
    "sgst_amount": {"sgstamt", "sgstamount", "sgst"},
    "igst_amount": {"igstamt", "igstamount", "igst"},
    "line_total": {"totalamt", "totalamount", "amount", "total", "value", "nettotal"},
    "batch": {"batch", "batchno"},
    "expiry": {"expiry", "exp", "expdate"},
}
NUMERIC = {
    "quantity",
    "free_quantity",
    "mrp",
    "unit_rate",
    "discount_percent",
    "discount_amount",
    "gst_rate",
    "taxable_value",
    "gst_amount",
    "cgst_amount",
    "sgst_amount",
    "igst_amount",
    "line_total",
}


def field(value: str, numeric: bool = False) -> ProposedField:
    trimmed = value.strip()
    candidate = trimmed.replace(",", "").replace("\u20b9", "").strip()
    if numeric and re.fullmatch(r"\d{1,12}(?:\.\d{1,6})?", candidate) is None:
        return ProposedField(source=trimmed)
    return ProposedField(value=candidate if numeric else trimmed or None, source=trimmed)


def unique_field(matches: list[str]) -> ProposedField:
    values = list(dict.fromkeys(value.strip() for value in matches if value.strip()))
    return ProposedField(
        value=values[0] if len(values) == 1 else None, source=" | ".join(values)[:2000]
    )


def propose(document_id: str, attempt_id: str, sha256: str, evidence: OcrEvidence) -> InvoiceDraft:
    rows: list[DraftRow] = []
    texts: list[str] = []
    table_totals: list[str] = []
    warnings = [
        "Provider confidence is unavailable. Verify every field, row, "
        "pack conversion and paid status against the original.",
        "Recognition is advisory; no inventory, prices or accounts have changed.",
    ]
    for page in evidence.pages:
        blocks = page.blocks or []
        content = [(index, block.text) for index, block in enumerate(blocks, 1)]
        if not content:
            content = [(1, page.markdown)]
        for block_number, text in content:
            cleaned, tables = plain(text)
            texts.append(cleaned)
            if not tables and "|" in text:
                markdown_rows = [
                    [cell.strip() for cell in line.strip().strip("|").split("|")]
                    for line in text.splitlines()
                    if "|" in line and not re.fullmatch(r"[| :\-]+", line.strip())
                ]
                if markdown_rows:
                    tables = [markdown_rows]
            for table in tables:
                header_index = next(
                    (
                        index
                        for index, cells in enumerate(table)
                        if any(header_key(cell) in HEADERS["description"] for cell in cells)
                    ),
                    None,
                )
                if header_index is None:
                    continue
                headers = table[header_index]
                indices = {
                    name: [
                        index for index, value in enumerate(headers) if header_key(value) in aliases
                    ]
                    for name, aliases in HEADERS.items()
                }
                for row_number, cells in enumerate(table[header_index + 1 :], header_index + 2):
                    mapped = {
                        name: field(cells[positions[0]], name in NUMERIC)
                        if len(positions) == 1 and positions[0] < len(cells)
                        else ProposedField()
                        for name, positions in indices.items()
                    }
                    description = mapped["description"].value
                    if description and header_key(description) in {
                        "total",
                        "grandtotal",
                        "subtotal",
                        "taxdetails",
                        "gst%",
                        "taxsummary",
                        "gstsummary",
                        "grosstotal",
                        "nettotal",
                        "cgst",
                        "sgst",
                        "igst",
                        "invoiceamountinwords",
                        "bankdetails",
                    }:
                        if header_key(description) in {"total", "grandtotal"}:
                            total = mapped["line_total"].value
                            if total is not None and len(cells) == len(headers):
                                table_totals.append(total)
                        # A fused HTML table can include the whole invoice footer. Those
                        # rows are not products, even if they occupy a description column.
                        break
                    if not description or header_key(description) in {
                        "description",
                        "goodsdescription",
                        "descriptionofgoods",
                    }:
                        continue
                    positions = indices["description"]
                    if (
                        len(positions) == 1
                        and header_key(headers[positions[0]]) == "slitemdescription"
                    ):
                        mapped["description"].value = re.sub(r"^\d+\s+", "", description)
                    hsn = mapped["hsn"]
                    if hsn.value:
                        candidate = re.sub(r"\s+", "", hsn.value)
                        hsn.value = (
                            candidate
                            if re.fullmatch(r"(?:[0-9]{4}|[0-9]{6}|[0-9]{8})", candidate)
                            else None
                        )
                    rates: list[tuple[str, Decimal, str]] = []
                    for kind in ("cgst", "sgst", "igst"):
                        positions = indices[kind + "_amount"]
                        if len(positions) != 1 or positions[0] >= len(cells):
                            continue
                        quote = cells[positions[0]]
                        combined = re.fullmatch(
                            r"\s*(?:\u20b9|INR|Rs\.?)?\s*([0-9,]+(?:\.[0-9]{1,2})?)"
                            r"\s*\(\s*([0-9]+(?:\.[0-9]{1,2})?)\s*%\s*\)\s*",
                            quote,
                        )
                        if combined:
                            mapped[kind + "_amount"] = field(combined.group(1), True)
                            mapped[kind + "_amount"].source = quote
                            rates.append((kind, Decimal(combined.group(2)), quote))
                    kinds = {kind for kind, _, _ in rates}
                    if mapped["gst_rate"].value is None and kinds in ({"cgst", "sgst"}, {"igst"}):
                        gst = sum((rate for _, rate, _ in rates), Decimal("0"))
                        if 0 <= gst <= 100 and (len(rates) == 1 or rates[0][1] == rates[1][1]):
                            mapped["gst_rate"] = ProposedField(
                                value=format(gst, "f"),
                                source=" + ".join(quote for _, _, quote in rates),
                            )
                    if len(cells) != len(headers):
                        warnings.append(
                            f"Page {page.page_number}, row {row_number}: column count differs; "
                            "do not post without correction."
                        )
                    rows.append(
                        DraftRow(
                            page=page.page_number,
                            block=block_number,
                            row=row_number,
                            headers=headers,
                            cells=cells,
                            fields=mapped,
                        )
                    )
    text = "\n".join(texts)
    invoice_numbers = re.findall(
        r"(?:invoice\s*(?:no\.?|number)|bill\s*no\.?)\s*[:#\-]?\s*([A-Za-z0-9][A-Za-z0-9/\-]{0,99})",
        text,
        re.I,
    )
    dates = re.findall(
        r"(?:invoice\s*date|bill\s*date|dated|\bdate)\s*[:\-]?\s*"
        r"(\d{1,2}[ ./\-]+(?:[A-Za-z]{3,9}|\d{1,2})[ ./\-]+\d{4})",
        text,
        re.I,
    )
    date_field = unique_field(dates)
    if date_field.value:
        parsed = None
        for pattern in ("%d %b %Y", "%d %B %Y", "%d %m %Y"):
            try:
                parsed = (
                    datetime.strptime(re.sub(r"[./\-]", " ", date_field.value), pattern)
                    .date()
                    .isoformat()
                )
                break
            except ValueError:
                pass
        date_field.value = parsed
    totals = re.findall(
        r"(?:invoice\s*(?:amount|total)|grand\s*total|net\s*payable)\s*[:\-]?\s*(?:Rs\.?|INR|\u20b9)?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)",
        text,
        re.I,
    )
    total_field = unique_field([value.replace(",", "") for value in totals] + table_totals)
    supplier_candidates = list(
        dict.fromkeys(
            line.strip()
            for line in text.splitlines()
            if re.search(
                r"(?:agency|distributors?|enterprises?|traders?|pvt|limited|ltd)\b", line, re.I
            )
            and 3 <= len(line.strip()) <= 150
        )
    )[:10]
    fields = {
        "invoice_number": unique_field(invoice_numbers),
        "invoice_date": date_field,
        "invoice_total": total_field,
        "supplier_gstin": unique_field(
            re.findall(r"\b[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][A-Z0-9]Z[A-Z0-9]\b", text)
        ),
        "supplier_name": unique_field(supplier_candidates),
    }
    line_sum = (
        sum((Decimal(row.fields["line_total"].value or "0") for row in rows), Decimal("0"))
        if rows and all(row.fields["line_total"].value is not None for row in rows)
        else None
    )
    if not rows:
        warnings.append(
            "No supported product table recognized. Manual source review is required; "
            "do not assume there were no products."
        )
    if line_sum is not None and total_field.value and line_sum != Decimal(total_field.value):
        warnings.append(
            "Recognized row totals do not match the proposed invoice total. "
            "Review missing rows, taxes, discounts and rounding."
        )
    if any(value.value is None for value in fields.values()):
        warnings.append(
            "One or more header fields are missing or ambiguous; "
            "resolve them before purchase review."
        )
    return InvoiceDraft(
        document_id=document_id,
        attempt_id=attempt_id,
        source_sha256=sha256,
        supplier_candidates=supplier_candidates,
        fields=fields,
        rows=rows[:200],
        warnings=list(dict.fromkeys(warnings)),
        line_total_sum=f"{line_sum:.2f}" if line_sum is not None else None,
    )
