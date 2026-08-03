from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lavine_reversal.validate import validate_result, validate_snapshot_result


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a Q58 backtest JSON")
    parser.add_argument("path")
    args = parser.parse_args()
    payload = json.loads(Path(args.path).read_text(encoding="utf-8"))
    if payload.get("artifact_type") == "factor_snapshot":
        validate_snapshot_result(payload)
    else:
        validate_result(payload)
    print(f"valid: {args.path}")


if __name__ == "__main__":
    main()
