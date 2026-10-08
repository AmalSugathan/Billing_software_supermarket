"""Hostile/corrupt invoice safety and encrypted evidence boundaries, synthetic bytes only."""

import base64
import io

import pytest
from cryptography.exceptions import InvalidTag
from PIL import Image
from pypdf import PdfWriter

from supermarket.document_security import EvidenceVault, InvalidDocument, validate_document
from supermarket.ocr import pack_sizes


def image_bytes(mime="JPEG", color="white"):
    output = io.BytesIO()
    Image.new("RGB", (100, 120), color=color).save(output, format=mime)
    return output.getvalue()


def test_authenticated_evidence_encryption_and_scope_tampering():
    vault = EvidenceVault(base64.b64encode(b"0" * 32).decode())
    original = b"synthetic invoice only"
    encrypted = vault.encrypt(original, "business|store|document|original")
    assert original not in encrypted
    assert vault.decrypt(encrypted, "business|store|document|original") == original
    with pytest.raises(InvalidTag):
        vault.decrypt(encrypted, "different-business|store|document|original")
    with pytest.raises(InvalidTag):
        vault.decrypt(
            encrypted[:-1] + bytes([encrypted[-1] ^ 1]), "business|store|document|original"
        )
    for key in ("not-base64", base64.b64encode(b"short").decode()):
        with pytest.raises(ValueError):
            EvidenceVault(key)


def test_image_decode_derivative_and_rejection(monkeypatch):
    jpeg = image_bytes()
    validated = validate_document(jpeg, "image/jpeg")
    assert validated.page_count == 1 and validated.processing_mime == "image/jpeg"
    assert validate_document(image_bytes("PNG"), "image/png").processing_content.startswith(
        b"\xff\xd8"
    )
    for content, mime in (
        (b"", "image/jpeg"),
        (b"<svg onload='alert(1)'/>", "image/jpeg"),
        (jpeg[:-40], "image/jpeg"),
        (jpeg, "application/pdf"),
    ):
        with pytest.raises(InvalidDocument):
            validate_document(content, mime)
    monkeypatch.setattr("supermarket.document_security.MAX_PIXELS", 100)
    with pytest.raises(InvalidDocument):
        validate_document(jpeg, "image/jpeg")
    monkeypatch.setattr("supermarket.document_security.MAX_BYTES", 10)
    with pytest.raises(InvalidDocument):
        validate_document(jpeg, "image/jpeg")


def test_plain_pdf_rejects_active_content_encryption_and_page_limits():
    def pdf(pages=1, javascript=False, encrypted=False):
        writer = PdfWriter()
        for _ in range(pages):
            writer.add_blank_page(width=400, height=600)
        if javascript:
            writer.add_js("app.alert('synthetic hostile file')")
        if encrypted:
            writer.encrypt("synthetic-password")
        output = io.BytesIO()
        writer.write(output)
        return output.getvalue()

    assert validate_document(pdf(), "application/pdf").page_count == 1
    for content in (pdf(11), pdf(javascript=True), pdf(encrypted=True), b"%PDF-1.4 garbage"):
        with pytest.raises(InvalidDocument):
            validate_document(content, "application/pdf")
    assert pack_sizes("Milk 1 L") == pack_sizes("Milk 1000 ml")
    assert pack_sizes("Biscuit 100 g") != pack_sizes("Biscuit 200 g")
