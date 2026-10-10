"""Validated Gemini invoice proposals; model output never authorizes a posting."""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Literal, Self

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator


def decimal_input(value: object) -> object:
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError("Use decimal strings or exact JSON numbers, never binary floats")
    return value


Positive = Annotated[
    Decimal,
    BeforeValidator(decimal_input),
    Field(
        gt=0,
        le=999999999999,
        max_digits=18,
        decimal_places=6,
        description=(
            "A positive decimal string containing digits and decimal point only. "
            "No unit suffix or commas."
        ),
    ),
]
Nonnegative = Annotated[
    Decimal,
    BeforeValidator(decimal_input),
    Field(
        ge=0,
        le=999999999999,
        max_digits=18,
        decimal_places=6,
        description=(
            "A nonnegative decimal string. No currency symbol, unit suffix, percent sign or commas."
        ),
    ),
]
Unit = Literal["PCS", "PACK", "CARTON", "BAG", "KG", "G", "L", "ML"]
COUNT_UNITS = {"PCS", "PACK", "CARTON", "BAG"}
SCALES = {
    "KG": ("mass", Decimal(1)),
    "G": ("mass", Decimal("0.001")),
    "L": ("volume", Decimal(1)),
    "ML": ("volume", Decimal("0.001")),
}


def convert(size: Decimal, source: str, target: str) -> Decimal | None:
    if source == target:
        return size
    if source in SCALES and target in SCALES and SCALES[source][0] == SCALES[target][0]:
        return size * SCALES[source][1] / SCALES[target][1]
    return None


class ExtractionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)


class ExtractedItem(ExtractionModel):
    # Core fields intentionally follow the owner's requested schema.
    original_name: str = Field(min_length=1, max_length=250)
    normalized_name: str = Field(min_length=1, max_length=250)
    invoice_quantity: Positive | None
    invoice_unit: str | None = Field(min_length=1, max_length=30)
    detected_pack_size: Positive | None
    pack_unit: Unit | None
    purchase_unit_interpretation: Unit | None
    stock_unit: Unit | None
    stock_quantity: Positive | None
    unit_rate: Nonnegative | None
    cost_per_kg: Nonnegative | None
    requires_review: bool = Field(strict=True)
    review_reasons: list[str] = Field(default_factory=list, max_length=30)
    source_page: int = Field(default=1, ge=1, le=10)
    source_text: str = Field(default="", max_length=2000)
    free_stock_quantity: Nonnegative | None = None
    discount_amount: Nonnegative | None = None
    gst_rate: (
        Annotated[Decimal, BeforeValidator(decimal_input), Field(ge=0, le=100, decimal_places=2)]
        | None
    ) = None
    hsn: str | None = Field(default=None, pattern=r"^(?:[0-9]{4}|[0-9]{6}|[0-9]{8})$")
    line_total: Nonnegative | None = None
    batch: str | None = Field(default=None, max_length=100)
    expiry: date | None = None

    def conversion_factor(self) -> Decimal | None:
        purchase, stock = self.purchase_unit_interpretation, self.stock_unit
        if not purchase or not stock:
            return None
        if purchase == stock:
            return Decimal(1)
        if purchase in SCALES:
            return convert(Decimal(1), purchase, stock)
        if self.detected_pack_size is None or self.pack_unit is None:
            return None
        return convert(self.detected_pack_size, self.pack_unit, stock)

    @model_validator(mode="after")
    def validate_conversion(self) -> Self:
        reasons = list(self.review_reasons)
        printed_unit = self.invoice_unit.upper().rstrip(".") if self.invoice_unit else None
        unit_aliases = {
            "NOS": "PCS",
            "NO": "PCS",
            "PC": "PCS",
            "PIECE": "PCS",
            "PIECES": "PCS",
            "BTL": "PCS",
            "BOTTLE": "PCS",
            "KGS": "KG",
            "GRAM": "G",
            "GM": "G",
            "LTR": "L",
            "LITRE": "L",
            "CTN": "CARTON",
            "BAGS": "BAG",
        }
        printed_unit = unit_aliases.get(printed_unit, printed_unit) if printed_unit else None
        if printed_unit != self.purchase_unit_interpretation:
            reasons.append("Printed invoice unit differs from the interpreted purchase unit")
        if any(
            value is None
            for value in (
                self.invoice_quantity,
                self.invoice_unit,
                self.purchase_unit_interpretation,
                self.stock_unit,
                self.unit_rate,
            )
        ):
            reasons.append("Required quantity, unit or rate is missing")
        if not self.source_text:
            reasons.append("Source evidence for the pack interpretation is missing")
        for quantity in (self.invoice_quantity, self.free_stock_quantity):
            if quantity is not None and quantity != quantity.quantize(Decimal("0.001")):
                raise ValueError(
                    "Purchase and free quantities support at most three decimal places"
                )
        if self.stock_unit in COUNT_UNITS and self.free_stock_quantity is not None:
            if self.free_stock_quantity != self.free_stock_quantity.to_integral_value():
                raise ValueError("Free counted stock must contain whole units")
        factor = self.conversion_factor()
        if factor is not None and factor != factor.quantize(Decimal("0.001")):
            raise ValueError("Conversion exceeds supported stock precision")
        if factor is None:
            reasons.append("Pack-to-stock conversion cannot be established")
            if self.stock_quantity is not None:
                raise ValueError("Stock quantity supplied without a valid unit conversion")
        elif self.invoice_quantity is not None:
            expected = self.invoice_quantity * factor
            if expected != expected.quantize(Decimal("0.001")) or expected > Decimal(
                "999999999999.999"
            ):
                raise ValueError("Stock quantity exceeds supported precision or range")
            if self.stock_quantity is not None and self.stock_quantity != expected:
                raise ValueError("Stock quantity must equal invoice quantity times conversion")
            self.stock_quantity = expected
            if self.stock_unit in COUNT_UNITS and expected != expected.to_integral_value():
                raise ValueError("Counted stock must contain whole units")
        if self.purchase_unit_interpretation in COUNT_UNITS and self.invoice_quantity is not None:
            if self.invoice_quantity != self.invoice_quantity.to_integral_value():
                raise ValueError("Bag, carton, pack and piece purchases must be whole quantities")
        if self.stock_quantity is not None and self.invoice_quantity is None:
            raise ValueError("Stock quantity requires an invoice quantity")
        mass = (
            convert(factor, self.stock_unit, "KG")
            if factor is not None and self.stock_unit
            else None
        )
        if (
            mass is None
            and self.purchase_unit_interpretation == self.stock_unit
            and self.stock_unit in COUNT_UNITS
            and self.detected_pack_size is not None
            and self.pack_unit is not None
        ):
            # One counted retail packet can have a known mass without changing piece stock.
            mass = convert(self.detected_pack_size, self.pack_unit, "KG")
        if mass is not None and self.unit_rate is not None:
            expected_cost = (self.unit_rate / mass).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            if self.cost_per_kg is not None and self.cost_per_kg != expected_cost:
                raise ValueError("Cost per kg does not match rate and pack conversion")
            if expected_cost > Decimal("999999999999"):
                raise ValueError("Calculated cost per kg exceeds supported range")
            self.cost_per_kg = expected_cost
        elif self.cost_per_kg is not None:
            raise ValueError("Cost per kg requires a known mass conversion and rate")
        # A model's false flag cannot suppress application-detected ambiguity.
        self.review_reasons = list(dict.fromkeys(reasons))
        self.requires_review = self.requires_review or bool(self.review_reasons)
        return self


class ExtractedInvoice(ExtractionModel):
    schema_version: Literal["1"] = "1"
    page_count: int = Field(ge=1, le=10)
    supplier_name: str | None = Field(max_length=250)
    supplier_gstin: str | None = Field(max_length=15)
    invoice_number: str | None = Field(max_length=100)
    invoice_date: date | None = Field(
        description=(
            "Invoice date in ISO YYYY-MM-DD. "
            "Convert printed Indian DD-MM-YYYY dates; null if unreadable."
        )
    )
    invoice_total: Nonnegative | None
    tax_mode: Literal["inclusive", "exclusive"] | None = None
    tax_kind: Literal["intra", "inter"] | None = None
    round_off: (
        Annotated[Decimal, BeforeValidator(decimal_input), Field(ge=-1, le=1, decimal_places=2)]
        | None
    ) = None
    items: list[ExtractedItem] = Field(min_length=1, max_length=200)
    warnings: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_pages_and_totals(self) -> Self:
        if any(item.source_page > self.page_count for item in self.items):
            raise ValueError("Item refers to a page outside the invoice")
        if self.invoice_total is not None and all(
            item.line_total is not None for item in self.items
        ):
            total = sum(
                (item.line_total for item in self.items if item.line_total is not None), Decimal(0)
            )
            if total + (self.round_off or Decimal(0)) != self.invoice_total:
                warning = "Printed invoice total differs from extracted line totals and round-off"
                if warning not in self.warnings:
                    self.warnings.append(warning)
                for item in self.items:
                    item.requires_review = True
        return self
