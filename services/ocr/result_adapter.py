"""Normalize the pinned native PaddleX output without leaking source paths/images."""


def page_evidence(result):
    raw = result.json["res"]
    return {
        "prunedResult": {
            "parsing_res_list": [
                {
                    "block_label": block["block_label"],
                    "block_content": block["block_content"],
                    "block_bbox": block.get("block_bbox"),
                }
                for block in raw.get("parsing_res_list", [])
            ]
        },
        "markdown": {"text": result.markdown["markdown_texts"]},
    }
