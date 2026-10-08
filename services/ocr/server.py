"""Real CPU full-pipeline service. Loopback only; no URLs or financial tools."""

import base64
import binascii
import io
import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from typing import Literal

import uvicorn
import yaml
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services/ocr"))
from result_adapter import page_evidence  # noqa: E402 -- pinned local native output adapter

sys.path.insert(0, str(ROOT / "apps/api/src"))
from supermarket.document_security import (  # noqa: E402 -- shared bounded document validation
    MAX_BYTES,
    InvalidDocument,
    validate_document,
)

MODEL = "PaddleOCR-VL-1.6"
LIMIT = 8 * 1024 * 1024
pipeline = None
inference_lock = Lock()


@asynccontextmanager
async def lifespan(_):
    global pipeline
    import paddle
    import paddlex
    from paddleocr import PaddleOCRVL

    paddle.set_flags({"FLAGS_paddle_num_threads": int(os.environ.get("OCR_CPU_THREADS", "4"))})
    config_path = Path(paddlex.__file__).parent / "configs/pipelines/PaddleOCR-VL-1.6.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["batch_size"] = 1
    config["use_queues"] = False
    config["SubModules"]["LayoutDetection"]["batch_size"] = 1
    config["SubModules"]["VLRecognition"]["batch_size"] = 1
    config["SubPipelines"]["DocPreprocessor"]["batch_size"] = 1
    config["SubPipelines"]["DocPreprocessor"]["SubModules"]["DocOrientationClassify"][
        "batch_size"
    ] = 1
    pipeline = PaddleOCRVL(
        pipeline_version="v1.6",
        paddlex_config=config,
        device="cpu",
        cpu_threads=int(os.environ.get("OCR_CPU_THREADS", "4")),
        enable_mkldnn=False,
        use_doc_orientation_classify=True,
        use_doc_unwarping=True,
    )
    try:
        yield
    finally:
        pipeline.close()
        pipeline = None


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


class ParsingRequest(BaseModel):
    file: str = Field(min_length=1, max_length=14 * 1024 * 1024)
    fileType: Literal[0, 1]
    useDocOrientationClassify: bool = True
    useDocUnwarping: bool = True
    returnMarkdownImages: Literal[False] = False
    visualize: Literal[False] = False


@app.get("/health/ready")
def ready():
    if pipeline is None:
        raise HTTPException(503, "Model initialization pending")
    return {
        "status": "ready",
        "provider_model": MODEL,
        "pipeline_version": "v1.6",
        "device": "cpu",
        "busy": inference_lock.locked(),
    }


def parse(payload):
    if pipeline is None:
        raise HTTPException(503, "OCR model unavailable")
    try:
        content = base64.b64decode(payload.file, validate=True)
    except (ValueError, binascii.Error) as error:
        raise HTTPException(
            422, "Only Base64 document bytes are accepted; URL inputs are disabled"
        ) from error
    if len(content) > MAX_BYTES:
        raise HTTPException(413, "Document limit exceeded")
    try:
        validated = validate_document(
            content, "application/pdf" if payload.fileType == 0 else "application/octet-stream"
        )
    except InvalidDocument as error:
        raise HTTPException(422, str(error)) from error
    if not inference_lock.acquire(blocking=False):
        raise HTTPException(429, "Local OCR worker is busy; retry after it completes")
    try:
        with TemporaryDirectory(dir=ROOT / ".tools/phase2") as temporary:
            if payload.fileType == 0:
                source = Path(temporary) / "source.pdf"
                source.write_bytes(validated.processing_content)
                model_input = str(source)
            else:
                import numpy as np
                from PIL import Image

                with Image.open(io.BytesIO(validated.processing_content)) as image:
                    model_input = np.asarray(image.convert("RGB"))[:, :, ::-1].copy()
            pages = []
            for result in pipeline.predict(
                model_input,
                use_doc_orientation_classify=payload.useDocOrientationClassify,
                use_doc_unwarping=payload.useDocUnwarping,
                vlm_extra_args={"use_cache": True},
                max_new_tokens=4096,
            ):
                pages.append(page_evidence(result))
                if len(pages) > 10:
                    raise HTTPException(422, "Page limit exceeded")
            output = {"errorCode": 0, "result": {"layoutParsingResults": pages}}
            if len(json.dumps(output).encode()) > LIMIT:
                raise HTTPException(422, "OCR evidence output limit exceeded")
            return output
    finally:
        inference_lock.release()


@app.post("/layout-parsing")
async def layout_parsing(request: Request):
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 14 * 1024 * 1024:
            raise HTTPException(413, "Request limit exceeded")
    try:
        payload = ParsingRequest.model_validate_json(body)
    except ValueError as error:
        raise HTTPException(422, "Invalid document request") from error
    try:
        return await run_in_threadpool(parse, payload)
    except HTTPException:
        raise
    except Exception as error:
        # Do not return/log model data, private paths or native exception payloads.
        raise HTTPException(
            503, "Private OCR inference failed; original evidence is retained"
        ) from error


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8091, workers=1, access_log=False)
