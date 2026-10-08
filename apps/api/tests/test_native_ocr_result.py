"""Pinned native output contract regression; no pretend inference."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace


def test_native_paddlex_markdown_plural_key_and_private_output_pruning():
    path = Path(__file__).resolve().parents[3] / "services/ocr/result_adapter.py"
    spec = importlib.util.spec_from_file_location("native_ocr_adapter", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    native = SimpleNamespace(
        json={
            "res": {
                "input_path": "private-source",
                "parsing_res_list": [
                    {
                        "block_label": "table",
                        "block_content": "synthetic table",
                        "block_bbox": [1, 2, 3, 4],
                        "private_extra": "not returned",
                    }
                ],
            }
        },
        markdown={"markdown_texts": "synthetic markdown", "markdown_images": {"private": "image"}},
    )
    normalized = module.page_evidence(native)
    assert normalized["markdown"] == {"text": "synthetic markdown"}
    assert normalized["prunedResult"]["parsing_res_list"] == [
        {"block_label": "table", "block_content": "synthetic table", "block_bbox": [1, 2, 3, 4]}
    ]
    assert "private" not in str(normalized)
