"""Real private PaddleOCR adapter. No fake provider is installed in the runtime."""

import base64
import json
from typing import Literal, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from pydantic import BaseModel, Field, ValidationError

from supermarket.invoice_extraction import ExtractedInvoice

MODEL = "PaddleOCR-VL-1.6"
MAX_OUTPUT = 8 * 1024 * 1024


class ProviderFailure(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__("OCR processing failed; retained original can be retried after review")


class EvidenceBlock(BaseModel):
    label: str = Field(max_length=100)
    text: str = Field(max_length=250000)
    bbox: list[float] | None = Field(default=None, max_length=4)
    confidence: None = None
    confidence_method: Literal["not_supplied"] = "not_supplied"
    requires_review: Literal[True] = True


class EvidencePage(BaseModel):
    page_number: int
    markdown: str = Field(max_length=250000)
    blocks: list[EvidenceBlock] = Field(max_length=1000)


class OcrEvidence(BaseModel):
    provider_model: str = MODEL
    document_class: Literal["unclassified"] = "unclassified"
    classification_confidence: None = None
    pages: list[EvidencePage] = Field(min_length=1, max_length=10)
    review_required: Literal[True] = True
    extraction: ExtractedInvoice | None = None


class ReadyHealth(BaseModel):
    status: Literal["ready"]
    provider_model: Literal["PaddleOCR-VL-1.6"]
    pipeline_version: Literal["v1.6"]


class RawBlock(BaseModel):
    model_config = {"allow_inf_nan": False}
    block_label: str = Field(default="unknown", max_length=100)
    block_content: str = Field(default="", max_length=250000)
    block_bbox: list[float] | None = Field(default=None, max_length=4)


class RawPruned(BaseModel):
    parsing_res_list: list[RawBlock] = Field(
        default_factory=lambda: list[RawBlock](), max_length=1000
    )


class RawMarkdown(BaseModel):
    text: str = Field(default="", max_length=250000)


class RawPage(BaseModel):
    prunedResult: RawPruned
    markdown: RawMarkdown


class RawResult(BaseModel):
    layoutParsingResults: list[RawPage] = Field(min_length=1, max_length=10)


class RawResponse(BaseModel):
    errorCode: Literal[0]
    result: RawResult


class OcrProvider(Protocol):
    def infer(self, content: bytes, mime: str) -> OcrEvidence: ...


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Request, fp: object, code: int, msg: str, headers: object, newurl: str
    ) -> None:
        raise ProviderFailure("redirect_rejected")


class PaddleLayoutProvider:
    def __init__(self, service_url: str, timeout_seconds: int = 180):
        address = urlparse(service_url)
        if (
            address.scheme != "http"
            or address.hostname not in {"localhost", "127.0.0.1", "::1"}
            or address.username
            or address.password
            or address.query
            or address.fragment
            or address.path not in {"", "/"}
        ):
            raise ValueError("This first OCR adapter requires a trusted loopback HTTP service root")
        if not 30 <= timeout_seconds <= 7200:
            raise ValueError("OCR timeout must be between 30 and 7200 seconds")
        self.timeout_seconds = timeout_seconds
        self.url = service_url.rstrip("/") + "/layout-parsing"

    def health(self) -> bool:
        try:
            request = Request(  # noqa: S310 -- same validated private loopback service
                self.url.removesuffix("/layout-parsing") + "/health/ready", method="GET"
            )
            with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=2) as response:  # noqa: S310
                body = response.read(4097)
            if len(body) > 4096:
                return False
            ReadyHealth.model_validate_json(body)
            return True
        except ProviderFailure, HTTPError, URLError, TimeoutError, OSError, ValueError:
            return False

    def infer(self, content: bytes, mime: str) -> OcrEvidence:
        payload = {
            "file": base64.b64encode(content).decode("ascii"),
            "fileType": 0 if mime == "application/pdf" else 1,
            "useDocOrientationClassify": True,
            "useDocUnwarping": True,
            "returnMarkdownImages": False,
            "visualize": False,
        }
        request = Request(  # noqa: S310 -- validated loopback service; redirects rejected
            self.url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )  # noqa: S310 -- fixed validated loopback endpoint
        try:
            with build_opener(ProxyHandler({}), NoRedirect()).open(
                request, timeout=self.timeout_seconds
            ) as response:  # noqa: S310
                body = response.read(MAX_OUTPUT + 1)
            if len(body) > MAX_OUTPUT:
                raise ProviderFailure("output_limit")
            parsed = RawResponse.model_validate_json(body)
            pages: list[EvidencePage] = []
            for number, page in enumerate(parsed.result.layoutParsingResults, 1):
                blocks = [
                    EvidenceBlock(
                        label=block.block_label, text=block.block_content, bbox=block.block_bbox
                    )
                    for block in page.prunedResult.parsing_res_list
                ]
                if not blocks and not page.markdown.text:
                    raise ProviderFailure("no_text")
                pages.append(
                    EvidencePage(page_number=number, markdown=page.markdown.text, blocks=blocks)
                )
            return OcrEvidence(pages=pages)
        except ProviderFailure:
            raise
        except (HTTPError, URLError, TimeoutError, OSError) as error:
            raise ProviderFailure("service_unavailable") from error
        except (ValueError, TypeError, KeyError, ValidationError) as error:
            raise ProviderFailure("invalid_response") from error
