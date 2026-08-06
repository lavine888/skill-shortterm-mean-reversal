from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .contract import compute_run_id


EVIDENCE_SCHEMA_VERSION = 3
EVIDENCE_SCHEMA = pa.schema([
    pa.field("decision_date", pa.string(), nullable=False),
    pa.field("lookback_date", pa.string(), nullable=False),
    pa.field("entry_date", pa.string(), nullable=False),
    pa.field("exit_date", pa.string(), nullable=False),
    pa.field("symbol", pa.string(), nullable=False),
    pa.field("past_return", pa.float64(), nullable=False),
    pa.field("reversal_score", pa.float64(), nullable=False),
    pa.field("selected_side", pa.string(), nullable=False),
    pa.field("target_weight", pa.float64(), nullable=False),
    pa.field("entry_price", pa.float64(), nullable=True),
    pa.field("entry_suspended", pa.bool_(), nullable=False),
    pa.field("entry_is_st", pa.bool_(), nullable=False),
    pa.field("entry_tradable", pa.bool_(), nullable=False),
    pa.field("entry_borrowable", pa.bool_(), nullable=False),
    pa.field("entry_limit_up", pa.float64(), nullable=True),
    pa.field("entry_limit_down", pa.float64(), nullable=True),
    pa.field("entry_block_reason", pa.string(), nullable=True),
    pa.field("exit_price", pa.float64(), nullable=True),
    pa.field("exit_suspended", pa.bool_(), nullable=False),
    pa.field("exit_is_st", pa.bool_(), nullable=False),
    pa.field("exit_tradable", pa.bool_(), nullable=False),
    pa.field("exit_borrowable", pa.bool_(), nullable=False),
    pa.field("exit_limit_up", pa.float64(), nullable=True),
    pa.field("exit_limit_down", pa.float64(), nullable=True),
    pa.field("exit_block_reason", pa.string(), nullable=True),
    pa.field("forward_return", pa.float64(), nullable=True),
])
EVIDENCE_COLUMNS = EVIDENCE_SCHEMA.names


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class FactorEvidenceWriter:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.temporary = self.path.with_suffix(f".{os.getpid()}.tmp.parquet")
        self._writer: pq.ParquetWriter | None = None
        self._rows = 0
        self._decisions: set[str] = set()
        self._closed = False

    def __enter__(self) -> "FactorEvidenceWriter":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.temporary.unlink(missing_ok=True)
        return self

    def write(self, frame: pd.DataFrame) -> None:
        if self._closed:
            raise RuntimeError("factor evidence writer is closed")
        if list(frame.columns) != EVIDENCE_COLUMNS:
            raise ValueError("factor evidence columns do not match the fixed contract")
        table = pa.Table.from_pandas(frame, schema=EVIDENCE_SCHEMA, preserve_index=False, safe=True)
        if self._writer is None:
            self._writer = pq.ParquetWriter(
                self.temporary, EVIDENCE_SCHEMA, compression="zstd",
                use_dictionary=["decision_date", "lookback_date", "entry_date", "exit_date", "symbol", "selected_side"],
            )
        self._writer.write_table(table)
        self._rows += len(frame)
        self._decisions.update(frame["decision_date"].astype(str).unique())

    def close(self) -> None:
        if self._closed:
            return
        if self._writer is None or self._rows == 0:
            raise ValueError("factor evidence contains no rows")
        self._writer.close()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.temporary.replace(self.path)
        self._closed = True

    def __exit__(self, exc_type, exc, traceback) -> None:
        if exc_type is None:
            self.close()
        else:
            if self._writer is not None:
                self._writer.close()
            self.temporary.unlink(missing_ok=True)
            self._closed = True

    def metadata(self) -> dict[str, Any]:
        if not self._closed or not self.path.exists():
            raise RuntimeError("factor evidence must be closed before reading metadata")
        return {
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "artifact_name": self.path.name,
            "row_count": self._rows,
            "decision_count": len(self._decisions),
            "columns": EVIDENCE_COLUMNS,
            "file_sha256": file_sha256(self.path),
        }


def attach_factor_evidence(payload: dict[str, Any], metadata: dict[str, Any]) -> None:
    payload["factor_evidence"] = metadata
    payload["run_id"] = compute_run_id(payload)
