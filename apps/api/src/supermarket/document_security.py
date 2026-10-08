"""Bounded decode/validation and authenticated encryption of private invoice evidence."""

import base64
import binascii
import io
import os
import warnings
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from PIL import Image, ImageOps, UnidentifiedImageError
from pypdf import PdfReader
from pypdf.errors import PdfReadError
from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject

MAX_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 20_000_000
MAX_PAGES = 10


class InvalidDocument(ValueError):
    pass


@dataclass(frozen=True)
class ValidatedDocument:
    mime: str
    page_count: int
    processing_content: bytes
    processing_mime: str


class EvidenceVault:
    def __init__(self, encoded_key: str):
        try:
            key = base64.b64decode(encoded_key, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValueError("OCR encryption key must be Base64 for 32 random bytes") from error
        if len(key) != 32:
            raise ValueError("OCR encryption requires a 32-byte key")
        self.cipher = AESGCM(key)

    def encrypt(self, data: bytes, scope: str) -> bytes:
        nonce = os.urandom(12)
        return nonce + self.cipher.encrypt(nonce, data, scope.encode())

    def decrypt(self, data: bytes, scope: str) -> bytes:
        return self.cipher.decrypt(data[:12], data[12:], scope.encode())


def validate_pdf(content: bytes) -> int:
    try:
        reader = PdfReader(io.BytesIO(content), strict=True)
        if reader.is_encrypted:
            raise InvalidDocument("Encrypted PDFs are not supported; use a plain scan")
        seen: set[tuple[int, int]] = set()
        nodes = 0
        forbidden = {
            "/JavaScript",
            "/JS",
            "/Launch",
            "/EmbeddedFiles",
            "/EmbeddedFile",
            "/OpenAction",
            "/AA",
            "/AcroForm",
            "/XFA",
            "/RichMedia",
            "/GoToR",
            "/SubmitForm",
            "/URI",
            "/Filespec",
        }

        def inspect(value: object, depth: int = 0) -> None:
            nonlocal nodes
            nodes += 1
            if nodes > 10000 or depth > 40:
                raise InvalidDocument("PDF structure exceeds safe intake limits")
            if isinstance(value, IndirectObject):
                identity = (value.idnum, value.generation)
                if identity in seen:
                    return
                seen.add(identity)
                inspect(value.get_object(), depth + 1)
            elif isinstance(value, DictionaryObject):
                if forbidden.intersection(str(key) for key in value.keys()):
                    raise InvalidDocument(
                        "PDF contains actions, forms or attachments; upload a plain scan"
                    )
                for item in value.values():
                    inspect(item, depth + 1)
            elif isinstance(value, ArrayObject):
                for item in value:
                    inspect(item, depth + 1)

        inspect(reader.trailer)
        count = len(reader.pages)
        if not 1 <= count <= MAX_PAGES:
            raise InvalidDocument("PDF must contain between 1 and 10 pages")
        for page in reader.pages:
            if (
                not 0 < float(page.mediabox.width) <= 10000
                or not 0 < float(page.mediabox.height) <= 10000
            ):
                raise InvalidDocument("PDF page dimensions exceed intake limits")
        return count
    except InvalidDocument:
        raise
    except (PdfReadError, ValueError, TypeError, KeyError, RecursionError, OverflowError) as error:
        raise InvalidDocument("PDF cannot be validated; upload a readable scan") from error


def validate_document(content: bytes, supplied_mime: str) -> ValidatedDocument:
    if not content or len(content) > MAX_BYTES:
        raise InvalidDocument("Invoice must be nonempty and no larger than 10 MiB")
    if content.startswith(b"%PDF-"):
        mime = "application/pdf"
        pages = validate_pdf(content)
        result = ValidatedDocument(mime, pages, content, mime)
    elif content.startswith(b"\xff\xd8\xff") or content.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/jpeg" if content.startswith(b"\xff\xd8") else "image/png"
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(content)) as image:
                    if (
                        image.width * image.height > MAX_PIXELS
                        or getattr(image, "n_frames", 1) != 1
                    ):
                        raise InvalidDocument("Use a single image no larger than 20 megapixels")
                    image.verify()
                with Image.open(io.BytesIO(content)) as image:
                    normalized = ImageOps.exif_transpose(image).convert("RGB")
                    output = io.BytesIO()
                    normalized.save(output, format="JPEG", quality=95)
                    processing = output.getvalue()
                    if len(processing) > MAX_BYTES:
                        raise InvalidDocument("Decoded invoice exceeds processing size limits")
                    result = ValidatedDocument(mime, 1, processing, "image/jpeg")
        except InvalidDocument:
            raise
        except (
            UnidentifiedImageError,
            OSError,
            ValueError,
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
        ) as error:
            raise InvalidDocument("Image is corrupt, truncated or too large") from error
    else:
        raise InvalidDocument("Only genuine JPEG, PNG or plain PDF scans are supported")
    if supplied_mime not in {result.mime, "application/octet-stream"}:
        raise InvalidDocument("File contents do not match the declared document type")
    return result
