"""Private intake, real OCR attempts and advisory product matching; no stock posting."""

import hashlib
import json
import re
import unicodedata
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Annotated, Literal, cast
from urllib.parse import quote
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidTag
from fastapi import APIRouter, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import Connection, Engine, insert, select
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from supermarket.catalog_models import aliases, products, suppliers
from supermarket.commerce import lock
from supermarket.config import Settings
from supermarket.document_security import (
    MAX_BYTES,
    EvidenceVault,
    InvalidDocument,
    validate_document,
)
from supermarket.finance import row
from supermarket.identity import (
    Actor,
    IdentityAccess,
    InputModel,
    audit,
    permitted_store_ids,
    scope_for,
)
from supermarket.identity_models import audit_logs
from supermarket.ocr_models import attempts, documents, mappings, results
from supermarket.ocr_provider import MODEL, OcrEvidence, PaddleLayoutProvider, ProviderFailure


class ProcessInput(InputModel):
    reason: str = Field(min_length=3, max_length=500)


class MappingInput(ProcessInput):
    supplier_id: UUID
    product_id: UUID
    supplier_description: str = Field(min_length=2, max_length=250)
    confirmed: Literal[True]


class DocumentView(BaseModel):
    id: str
    store_id: str
    filename: str
    mime_type: str
    data_origin: str
    sha256: str
    byte_size: int
    page_count: int
    created_at: datetime
    status: str
    attempt_id: str | None = None
    error_code: str | None = None
    evidence: OcrEvidence | None = None


class Candidate(BaseModel):
    product_id: str
    name: str
    sku: str
    unit: str
    match_score: int
    method: str
    confidence: None = None
    requires_review: Literal[True] = True


def normalized(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", unicodedata.normalize("NFKC", value).casefold()))


def pack_sizes(value: str) -> set[str]:
    result: set[str] = set()
    for quantity, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(kg|gm|g|ml|l)\b", value.casefold()):
        base = "g" if unit in {"g", "gm", "kg"} else "ml"
        size = Decimal(quantity) * (1000 if unit in {"kg", "l"} else 1)
        result.add(str(size.normalize()) + base)
    return result


def ocr_router(engine: Engine | None, settings: Settings) -> APIRouter:
    router = APIRouter(
        prefix="/api/v1/businesses/{business_id}/stores/{store_id}/ocr-documents",
        tags=["Private invoice intelligence"],
    )
    access = IdentityAccess(engine, settings)
    vault = EvidenceVault(settings.ocr_encryption_key) if settings.ocr_encryption_key else None
    provider = PaddleLayoutProvider(settings.ocr_service_url) if settings.ocr_service_url else None

    def authorize(
        connection: Connection,
        actor: Actor,
        business: UUID,
        store: UUID,
        capability: str = "purchases.manage",
    ) -> None:
        scope = scope_for(connection, actor, str(business), capability)
        if str(store) not in permitted_store_ids(connection, str(business), scope):
            raise HTTPException(404, "Store unavailable")

    def private_row(connection: Connection, document: UUID, store: UUID) -> dict[str, object]:
        record = row(connection, documents, str(document))
        if record["store_id"] != str(store):
            raise HTTPException(404, "Invoice document unavailable")
        return record

    def encrypt(content: bytes, business: UUID, store: UUID, resource: str, kind: str) -> bytes:
        if vault is None:
            raise HTTPException(503, "Private document encryption is not configured")
        return vault.encrypt(content, f"{business}|{store}|{resource}|{kind}")

    def decrypt(content: bytes, business: UUID, store: UUID, resource: str, kind: str) -> bytes:
        if vault is None:
            raise HTTPException(503, "Private document encryption is not configured")
        try:
            return vault.decrypt(content, f"{business}|{store}|{resource}|{kind}")
        except (InvalidTag, ValueError) as error:
            raise HTTPException(
                503, "Stored evidence cannot be opened with the configured key"
            ) from error

    def presentation(
        connection: Connection, record: dict[str, object], detailed: bool = False
    ) -> DocumentView:
        attempt = (
            connection.execute(
                select(attempts)
                .where(attempts.c.document_id == record["id"])
                .order_by(attempts.c.created_at.desc(), attempts.c.id.desc())
                .limit(1)
            )
            .mappings()
            .first()
        )
        status, error_code, evidence = "uploaded", None, None
        if attempt:
            result = (
                connection.execute(select(results).where(results.c.attempt_id == attempt["id"]))
                .mappings()
                .first()
            )
            if result:
                status, error_code = str(result["status"]), result["error_code"]
                if detailed and result["evidence_ciphertext"] is not None:
                    evidence = OcrEvidence.model_validate_json(
                        decrypt(
                            result["evidence_ciphertext"],
                            UUID(str(record["business_id"])),
                            UUID(str(record["store_id"])),
                            str(result["id"]),
                            "ocr-result",
                        )
                    )
            else:
                status = (
                    "retry_due"
                    if cast(datetime, attempt["lease_expires_at"]) < datetime.now(UTC)
                    else "processing"
                )
        return DocumentView.model_validate(
            {
                **record,
                "status": status,
                "error_code": error_code,
                "attempt_id": str(attempt["id"]) if attempt else None,
                "evidence": evidence,
            }
        )

    @router.get("/provider")
    def availability(business_id: UUID, store_id: UUID, request: Request) -> dict[str, object]:
        actor = access.actor_for(request)
        with access.database().begin() as connection:
            authorize(connection, actor, business_id, store_id)
        return {
            "upload_enabled": vault is not None,
            "provider_configured": provider is not None,
            "provider_model": MODEL,
            "health_verified": False,
            "semantic_matching_available": False,
            "message": "Configured private service; run OCR to verify availability"
            if provider
            else "PaddleOCR service is not configured. Documents can be saved privately, "
            "but extraction is unavailable.",
        }

    @router.get("", response_model=list[DocumentView])
    def listing(
        business_id: UUID,
        store_id: UUID,
        request: Request,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0, le=100000),
    ) -> list[DocumentView]:
        actor = access.actor_for(request)
        with access.database().begin() as connection:
            authorize(connection, actor, business_id, store_id)
            records = connection.execute(
                select(documents)
                .where(documents.c.store_id == str(store_id))
                .order_by(documents.c.created_at.desc(), documents.c.id)
                .limit(limit)
                .offset(offset)
            ).mappings()
            return [presentation(connection, dict(record)) for record in records]

    @router.post("", response_model=DocumentView, status_code=201)
    async def upload(
        business_id: UUID,
        store_id: UUID,
        request: Request,
        idempotency_key: Annotated[UUID, Header()],
        filename: str = Query(min_length=1, max_length=120),
        data_origin: Literal["authentic", "synthetic", "unknown"] = "unknown",
    ) -> DocumentView:
        actor = await run_in_threadpool(access.actor_for, request, mutation=True)

        def check() -> None:
            with access.database().begin() as connection:
                authorize(connection, actor, business_id, store_id)

        await run_in_threadpool(check)
        if vault is None:
            raise HTTPException(503, "Private document encryption is not configured")
        if any(character in filename for character in "/\\") or any(
            ord(character) < 32 or ord(character) == 127 for character in filename
        ):
            raise HTTPException(
                422, "Use a plain document filename without path/control characters"
            )
        mime = (
            request.headers.get("content-type", "application/octet-stream").split(";", 1)[0].strip()
        )
        length = request.headers.get("content-length")
        if length is not None and (not length.isdigit() or int(length) > MAX_BYTES):
            raise HTTPException(413, "Document exceeds the 10 MiB upload limit")
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > MAX_BYTES:
                raise HTTPException(413, "Document exceeds the 10 MiB upload limit")

        def save() -> DocumentView:
            original = bytes(content)
            try:
                validated = validate_document(original, mime)
            except InvalidDocument as error:
                raise HTTPException(422, str(error)) from error
            digest = hashlib.sha256(original).hexdigest()
            request_hash = hashlib.sha256(
                json.dumps(
                    {
                        "store": str(store_id),
                        "sha256": digest,
                        "filename": filename,
                        "origin": data_origin,
                        "mime": mime,
                    },
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            try:
                with access.database().begin() as connection:
                    authorize(connection, actor, business_id, store_id)
                    lock(connection, "ocr-upload:" + str(business_id) + str(idempotency_key))
                    existing = (
                        connection.execute(
                            select(documents).where(
                                documents.c.idempotency_key == str(idempotency_key)
                            )
                        )
                        .mappings()
                        .first()
                    )
                    if existing:
                        if existing["request_hash"] != request_hash:
                            raise HTTPException(
                                409, "Upload key belongs to different document details"
                            )
                        return presentation(connection, dict(existing))
                    duplicate = connection.execute(
                        select(documents.c.id).where(
                            documents.c.store_id == str(store_id), documents.c.sha256 == digest
                        )
                    ).scalar_one_or_none()
                    if duplicate:
                        raise HTTPException(
                            409, "This original is already in this store's invoice inbox"
                        )
                    resource = str(uuid4())
                    connection.execute(
                        insert(documents).values(
                            id=resource,
                            business_id=str(business_id),
                            store_id=str(store_id),
                            actor_user_id=actor.user.id,
                            filename=filename,
                            mime_type=validated.mime,
                            data_origin=data_origin,
                            sha256=digest,
                            byte_size=len(original),
                            page_count=validated.page_count,
                            original_ciphertext=encrypt(
                                original, business_id, store_id, resource, "original"
                            ),
                            processing_ciphertext=encrypt(
                                validated.processing_content,
                                business_id,
                                store_id,
                                resource,
                                "processing",
                            ),
                            processing_mime=validated.processing_mime,
                            idempotency_key=str(idempotency_key),
                            request_hash=request_hash,
                        )
                    )
                    audit(
                        connection,
                        str(business_id),
                        actor.user.id,
                        "ocr.document.uploaded",
                        resource,
                        {"sha256": digest, "data_origin": data_origin, "stock_posted": False},
                    )
                    return presentation(connection, row(connection, documents, resource))
            except IntegrityError as error:
                raise HTTPException(
                    409, "Original invoice upload conflicts with an existing record"
                ) from error

        return await run_in_threadpool(save)

    @router.get("/{document_id}", response_model=DocumentView)
    def detail(
        business_id: UUID, store_id: UUID, document_id: UUID, request: Request
    ) -> DocumentView:
        actor = access.actor_for(request)
        with access.database().begin() as connection:
            authorize(connection, actor, business_id, store_id)
            return presentation(connection, private_row(connection, document_id, store_id), True)

    @router.get("/{document_id}/content")
    def content(
        business_id: UUID,
        store_id: UUID,
        document_id: UUID,
        request: Request,
        original: bool = False,
    ) -> Response:
        actor = access.actor_for(request)
        with access.database().begin() as connection:
            authorize(connection, actor, business_id, store_id)
            record = private_row(connection, document_id, store_id)
            kind = "original" if original else "processing"
            data = decrypt(
                cast(bytes, record[kind + "_ciphertext"]),
                business_id,
                store_id,
                str(document_id),
                kind,
            )
            mime = str(record["mime_type"] if original else record["processing_mime"])
            attachment = original or mime == "application/pdf"
            headers = {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; sandbox",
                "Content-Disposition": ("attachment" if attachment else "inline")
                + "; filename*=UTF-8''"
                + quote(str(record["filename"]), safe=""),
            }
            audit(
                connection,
                str(business_id),
                actor.user.id,
                "ocr.evidence.viewed",
                str(document_id),
                {"kind": kind},
            )
            return Response(data, media_type=mime, headers=headers)

    @router.post("/{document_id}/process", response_model=DocumentView)
    def process(
        business_id: UUID,
        store_id: UUID,
        document_id: UUID,
        payload: ProcessInput,
        request: Request,
        idempotency_key: Annotated[UUID, Header()],
    ) -> DocumentView:
        actor = access.actor_for(request, mutation=True)
        request_hash = hashlib.sha256(
            json.dumps(
                {"store": str(store_id), "document": str(document_id), **payload.model_dump()},
                sort_keys=True,
            ).encode()
        ).hexdigest()
        with access.database().begin() as connection:
            authorize(connection, actor, business_id, store_id)
            record = private_row(connection, document_id, store_id)
            if vault is None or provider is None:
                raise HTTPException(
                    503,
                    "Configure private document encryption and the PaddleOCR service "
                    "before extraction",
                )
            lock(connection, "ocr-process:" + str(business_id) + str(document_id))
            existing = (
                connection.execute(
                    select(attempts).where(attempts.c.idempotency_key == str(idempotency_key))
                )
                .mappings()
                .first()
            )
            if existing:
                if existing["request_hash"] != request_hash:
                    raise HTTPException(409, "Processing key belongs to a different request")
                return presentation(connection, record, True)
            active = (
                connection.execute(
                    select(attempts)
                    .where(attempts.c.document_id == str(document_id))
                    .order_by(attempts.c.created_at.desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
            if (
                active
                and not connection.execute(
                    select(results.c.id).where(results.c.attempt_id == active["id"])
                ).first()
            ):
                if cast(datetime, active["lease_expires_at"]) >= datetime.now(UTC):
                    raise HTTPException(
                        409,
                        "OCR already processing; refresh the document instead of submitting again",
                    )
                interrupted_result_id = str(uuid4())
                connection.execute(
                    insert(results).values(
                        id=interrupted_result_id,
                        business_id=str(business_id),
                        store_id=str(store_id),
                        actor_user_id=actor.user.id,
                        attempt_id=active["id"],
                        status="failed",
                        error_code="worker_interrupted",
                    )
                )
                connection.execute(
                    insert(audit_logs).values(
                        id=str(uuid4()),
                        business_id=str(business_id),
                        actor_user_id=actor.user.id,
                        action="ocr.processing.interrupted",
                        resource_id=interrupted_result_id,
                        source="ai",
                        before_value=None,
                        after_value={
                            "document_id": str(document_id),
                            "attempt_id": str(active["id"]),
                            "status": "failed",
                            "error_code": "worker_interrupted",
                            "human_approved": False,
                            "stock_posted": False,
                        },
                    )
                )
            processing = decrypt(
                cast(bytes, record["processing_ciphertext"]),
                business_id,
                store_id,
                str(document_id),
                "processing",
            )
            attempt_id = str(uuid4())
            connection.execute(
                insert(attempts).values(
                    id=attempt_id,
                    business_id=str(business_id),
                    store_id=str(store_id),
                    actor_user_id=actor.user.id,
                    document_id=str(document_id),
                    provider_model=MODEL,
                    reason=payload.reason,
                    lease_expires_at=datetime.now(UTC) + timedelta(seconds=240),
                    idempotency_key=str(idempotency_key),
                    request_hash=request_hash,
                )
            )
            audit(
                connection,
                str(business_id),
                actor.user.id,
                "ocr.processing.requested",
                attempt_id,
                {"document_id": str(document_id), "provider_model": MODEL},
            )
        # Inference holds no pooled database connection and cannot issue financial commands.
        error_code: str | None = None
        extracted: OcrEvidence | None = None
        try:
            extracted = provider.infer(processing, str(record["processing_mime"]))
            if len(extracted.pages) != record["page_count"]:
                raise ProviderFailure("page_count_mismatch")
        except ProviderFailure as error:
            error_code = error.code
        with access.database().begin() as connection:
            authorize(connection, actor, business_id, store_id)
            lock(connection, "ocr-process:" + str(business_id) + str(document_id))
            if not connection.execute(
                select(results.c.id).where(results.c.attempt_id == attempt_id)
            ).first():
                result_id = str(uuid4())
                status = "failed" if error_code else "review_required"
                connection.execute(
                    insert(results).values(
                        id=result_id,
                        business_id=str(business_id),
                        store_id=str(store_id),
                        actor_user_id=actor.user.id,
                        attempt_id=attempt_id,
                        status=status,
                        error_code=error_code,
                        evidence_ciphertext=encrypt(
                            extracted.model_dump_json().encode(),
                            business_id,
                            store_id,
                            result_id,
                            "ocr-result",
                        )
                        if extracted and not error_code
                        else None,
                    )
                )
                connection.execute(
                    insert(audit_logs).values(
                        id=str(uuid4()),
                        business_id=str(business_id),
                        actor_user_id=actor.user.id,
                        action="ocr.processing.completed",
                        resource_id=result_id,
                        source="ai",
                        before_value=None,
                        after_value={
                            "document_id": str(document_id),
                            "provider_model": MODEL,
                            "status": status,
                            "human_approved": False,
                            "stock_posted": False,
                        },
                    )
                )
            return presentation(connection, private_row(connection, document_id, store_id), True)

    @router.get("/{document_id}/matches", response_model=list[Candidate])
    def matches(
        business_id: UUID,
        store_id: UUID,
        document_id: UUID,
        request: Request,
        q: str = Query(min_length=2, max_length=250),
        supplier_id: UUID | None = None,
    ) -> list[Candidate]:
        actor = access.actor_for(request)
        with access.database().begin() as connection:
            authorize(connection, actor, business_id, store_id)
            private_row(connection, document_id, store_id)
            known = (
                connection.execute(
                    select(mappings.c.product_id).where(
                        mappings.c.supplier_id == str(supplier_id),
                        mappings.c.normalized_description == normalized(q),
                    )
                ).scalar_one_or_none()
                if supplier_id
                else None
            )
            catalog = list(
                connection.execute(
                    select(products)
                    .where(products.c.active.is_(True))
                    .order_by(products.c.id)
                    .limit(2000)
                ).mappings()
            )
            if known and all(product["id"] != known for product in catalog):
                mapped_product = (
                    connection.execute(
                        select(products).where(products.c.id == known, products.c.active.is_(True))
                    )
                    .mappings()
                    .first()
                )
                if mapped_product:
                    catalog.append(mapped_product)
            alternate_names: dict[str, list[str]] = {}
            for product_id, name in connection.execute(
                select(aliases.c.product_id, aliases.c.name).where(
                    aliases.c.product_id.in_([product["id"] for product in catalog])
                )
            ):
                alternate_names.setdefault(str(product_id), []).append(str(name))
            candidates: list[Candidate] = []
            query_pack = pack_sizes(q)
            for product in catalog:
                product_pack = pack_sizes(str(product["name"]))
                if query_pack and product_pack and query_pack.isdisjoint(product_pack):
                    continue
                names = [
                    str(product["name"]),
                    str(product["sku"]),
                    *alternate_names.get(str(product["id"]), []),
                ]
                score = max(
                    SequenceMatcher(None, normalized(q), normalized(name)).ratio() for name in names
                )
                method = "normalized_name" if score == 1 else "fuzzy_name"
                match_score = 100 if score == 1 else min(90, round(score * 100))
                if product["id"] == known:
                    method, match_score = "reviewed_supplier_mapping", 100
                if match_score >= 35:
                    candidates.append(
                        Candidate(
                            product_id=str(product["id"]),
                            name=str(product["name"]),
                            sku=str(product["sku"]),
                            unit=str(product["unit"]),
                            match_score=match_score,
                            method=method,
                        )
                    )
            return sorted(
                candidates, key=lambda candidate: (-candidate.match_score, candidate.product_id)
            )[:8]

    @router.post("/{document_id}/mappings")
    def map_product(
        business_id: UUID,
        store_id: UUID,
        document_id: UUID,
        payload: MappingInput,
        request: Request,
        idempotency_key: Annotated[UUID, Header()],
    ) -> dict[str, str]:
        actor = access.actor_for(request, mutation=True)
        request_hash = hashlib.sha256(
            json.dumps(
                {"document": str(document_id), **payload.model_dump(mode="json")}, sort_keys=True
            ).encode()
        ).hexdigest()
        try:
            with access.database().begin() as connection:
                authorize(connection, actor, business_id, store_id, "actions.approve")
                private_row(connection, document_id, store_id)
                row(connection, suppliers, str(payload.supplier_id))
                if not row(connection, products, str(payload.product_id))["active"]:
                    raise HTTPException(422, "An inactive product cannot be mapped")
                lock(connection, "ocr-mapping:" + str(business_id) + str(idempotency_key))
                existing = (
                    connection.execute(
                        select(mappings).where(mappings.c.idempotency_key == str(idempotency_key))
                    )
                    .mappings()
                    .first()
                )
                if existing:
                    if existing["request_hash"] != request_hash:
                        raise HTTPException(
                            409, "Mapping key was already used for different details"
                        )
                    return {"id": str(existing["id"])}
                resource = str(uuid4())
                connection.execute(
                    insert(mappings).values(
                        id=resource,
                        business_id=str(business_id),
                        store_id=str(store_id),
                        actor_user_id=actor.user.id,
                        document_id=str(document_id),
                        supplier_id=str(payload.supplier_id),
                        product_id=str(payload.product_id),
                        supplier_description=payload.supplier_description,
                        normalized_description=normalized(payload.supplier_description),
                        reason=payload.reason,
                        idempotency_key=str(idempotency_key),
                        request_hash=request_hash,
                    )
                )
                audit(
                    connection,
                    str(business_id),
                    actor.user.id,
                    "ocr.product_mapping.approved",
                    resource,
                    {
                        "document_id": str(document_id),
                        "product_id": str(payload.product_id),
                        "stock_posted": False,
                    },
                )
                return {"id": resource}
        except IntegrityError as error:
            raise HTTPException(
                409,
                "Supplier description already has a reviewed mapping; review it before changing",
            ) from error

    return router
