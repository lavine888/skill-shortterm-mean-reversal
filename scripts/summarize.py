from __future__ import annotations

import argparse
from collections import Counter
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lavine_reversal.engine import calculate_stats
from lavine_reversal.validate import validate_result


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize a validated Q58 backtest")
    parser.add_argument("path")
    parser.add_argument("--evidence", help="Bound full cross-sectional evidence Parquet")
    args = parser.parse_args()
    payload = json.loads(Path(args.path).read_text(encoding="utf-8"))
    validate_result(payload, evidence_path=args.evidence)
    if payload.get("artifact_type") == "daily_nav_backtest":
        metrics = payload["metrics"]
        unresolved = {
            mark["lot_id"]: mark for mark in payload["final_positions"]
            if mark["effective_exit_date"] <= payload["days"][-1]["date"]
        }
        last_exit_attempt = {}
        for day in payload["days"]:
            for attempt in day["attempts"]:
                if attempt["kind"] == "exit":
                    last_exit_attempt[attempt["lot_id"]] = attempt
        unresolved_reasons: Counter[str] = Counter(
            last_exit_attempt[lot_id]["block_reason"] for lot_id in unresolved
        )
        summary = {
            "run_id": payload["run_id"],
            "accounting_mode": "daily_nav",
            "days": len(payload["days"]),
            "total_return": metrics["total_return"],
            "max_drawdown": metrics["max_drawdown"],
            "sharpe": metrics["sharpe"],
            "total_cost": metrics["total_cost"],
            "filled_entries": metrics["filled_entries"],
            "blocked_entries": metrics["blocked_entries"],
            "filled_exits": metrics["filled_exits"],
            "blocked_exit_attempts": metrics["blocked_exit_attempts"],
            "open_position_count": metrics["open_position_count"],
            "unresolved_exit_count": metrics["unresolved_exit_count"],
            "unresolved_exit_reasons": dict(sorted(unresolved_reasons.items())),
            "unresolved_symbols": sorted(mark["symbol"] for mark in unresolved.values())[:20],
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return
    periods = payload["periods"]
    gross_total = float(np.prod([1.0 + period["gross_return"] for period in periods]) - 1.0)
    positive_periods = sum(period["net_return"] > 0 for period in periods)
    long_contributions = []
    short_contributions = []
    entry_blocks: Counter[str] = Counter()
    for period in periods:
        evidence = period["selected_evidence"].values()
        entry_blocks.update(
            item["entry_block_reason"] for item in evidence
            if item["entry_block_reason"] is not None
        )
        long_contributions.append(sum(
            item["executed_weight"] * item["forward_return"]
            for item in evidence
            if item["executed_weight"] > 0 and item["forward_return"] is not None
        ))
        short_contributions.append(sum(
            item["executed_weight"] * item["forward_return"]
            for item in evidence
            if item["executed_weight"] < 0 and item["forward_return"] is not None
        ))
    midpoint = len(periods) // 2
    halves = []
    for label, subset in (("first_half", periods[:midpoint]), ("second_half", periods[midpoint:])):
        stats = calculate_stats([period["net_return"] for period in subset], payload["config"]["hold_days"])
        rank_ics = [period["rank_ic"] for period in subset if period["rank_ic"] is not None]
        halves.append({
            "label": label,
            "periods": len(subset),
            "total_return": stats["total_return"],
            "max_drawdown": stats["max_drawdown"],
            "mean_rank_ic": float(np.mean(rank_ics)) if rank_ics else None,
        })
    summary = {
        "run_id": payload["run_id"],
        "gross_total_return": gross_total,
        "net_total_return": payload["metrics"]["total_return"],
        "positive_periods": positive_periods,
        "periods": len(periods),
        "average_long_contribution": float(np.mean(long_contributions)),
        "average_short_contribution": float(np.mean(short_contributions)),
        "sum_period_costs": float(sum(period["cost"] for period in periods)),
        "forced_delisting_exits": payload["metrics"]["forced_delisting_exits"],
        "entry_blocks": dict(sorted(entry_blocks.items())),
        "halves": halves,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
