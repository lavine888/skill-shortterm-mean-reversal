from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def canonical_json(payload: dict[str, Any]) -> str:
    content = {key: value for key, value in payload.items() if key != "run_id"}
    return json.dumps(
        content, sort_keys=True, ensure_ascii=False, allow_nan=False,
        separators=(",", ":"),
    )


def compute_run_id(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()[:20]


def write_json_atomic(path: str | Path, payload: dict[str, Any]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(f"{output.suffix}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
