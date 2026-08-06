from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lavine_reversal import StrategyConfig, run_backtest, run_daily_backtest
from lavine_reversal.contract import compute_run_id, write_json_atomic
from lavine_reversal.providers import DemoProvider, FileProvider, PandaDataProvider
from lavine_reversal.validate import validate_result
from lavine_reversal.version import SCHEMA_VERSION, SKILL_NAME, SKILL_VERSION


def _fold_years(start: str, end: str) -> list[str]:
    first, last = pd.Timestamp(start).year, pd.Timestamp(end).year
    return [str(year) for year in range(first, last + 1)]


def _annual_runs(panel: pd.DataFrame, years: list[str], config: StrategyConfig, mode: str, source: str, calendar, calendar_source: str) -> list[dict]:
    runs = []
    for year in years:
        year_start = pd.Timestamp(year=int(year), month=1, day=1).strftime("%Y%m%d")
        year_end = pd.Timestamp(year=int(year), month=12, day=31).strftime("%Y%m%d")
        try:
            if mode == "daily_nav":
                result = run_daily_backtest(
                    panel, year_start, year_end, config, source=source,
                    calendar=calendar, calendar_source=calendar_source,
                )
            else:
                result = run_backtest(
                    panel, year_start, year_end, config, source=source,
                    calendar=calendar, calendar_source=calendar_source,
                )
            validate_result(result)
            metrics = result["metrics"]
            runs.append({
                "fold": year,
                "start": year_start,
                "end": year_end,
                "run_id": result["run_id"],
                "periods": metrics["periods"] if mode != "daily_nav" else metrics["periods"],
                "total_return": metrics["total_return"],
                "max_drawdown": metrics["max_drawdown"],
                "sharpe": metrics["sharpe"],
                "mean_rank_ic": metrics.get("mean_rank_ic"),
                "total_cost": metrics.get("total_cost"),
                "total_short_fee": metrics.get("total_short_fee", 0.0),
                "forced_delisting_exits": metrics.get("forced_delisting_exits", 0),
                "delisting_settlement_exits": metrics.get("delisting_settlement_exits", 0),
            })
        except Exception as exc:
            runs.append({
                "fold": year,
                "start": year_start,
                "end": year_end,
                "run_id": None,
                "error": f"{type(exc).__name__}: {exc}",
            })
    return runs


def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-year chronological out-of-sample validation")
    parser.add_argument("--provider", choices=["demo", "file", "pandadata"], default="demo")
    parser.add_argument("--input", help="CSV or Parquet daily panel for provider=file")
    parser.add_argument("--start", default="20210101")
    parser.add_argument("--end", default="20251231")
    parser.add_argument("--calendar")
    parser.add_argument("--accounting-mode", choices=["period", "daily_nav"], default="period")
    parser.add_argument("--cost-rate", type=float, default=0.001)
    parser.add_argument("--short-fee-rate", type=float, default=0.0)
    parser.add_argument("--delisting-exit-policy", choices=["error", "last_available_close"], default="error")
    parser.add_argument("--output", default="output/oos-validation.json")
    parser.add_argument("--cache-dir", default="output/panda-cache")
    parser.add_argument("--request-interval", type=float, default=1.0)
    args = parser.parse_args()

    calendar = None
    calendar_source = "panel_date_union"
    if args.calendar:
        calendar_path = Path(args.calendar).resolve()
        calendar_frame = pd.read_csv(calendar_path)
        calendar = calendar_frame["date"] if "date" in calendar_frame else calendar_frame.iloc[:, 0]
        calendar_source = "explicit"

    if args.provider == "demo":
        provider = DemoProvider()
    elif args.provider == "file":
        if not args.input:
            parser.error("--input is required for provider=file")
        provider = FileProvider(args.input)
    else:
        provider = PandaDataProvider(request_interval=args.request_interval, cache_dir=args.cache_dir)

    load_start = (pd.Timestamp(args.start) - timedelta(days=45)).strftime("%Y%m%d")
    panel = provider.load(load_start, args.end)
    if args.provider == "pandadata":
        if calendar is None:
            calendar = provider.load_calendar(load_start, args.end)
            calendar_source = "pandadata"
        provider.bind_runtime_context(panel)
        cache = provider.cache_diagnostics()
        print(f"PandaData cache hits={cache['hits']} misses={cache['misses']}")

    config = StrategyConfig(
        cost_rate=args.cost_rate,
        short_fee_rate=args.short_fee_rate,
        delisting_exit_policy=args.delisting_exit_policy,
    )
    years = _fold_years(args.start, args.end)
    annual = _annual_runs(panel, years, config, args.accounting_mode, args.provider, calendar, calendar_source)

    completed = [run for run in annual if "error" not in run]
    returns = [run["total_return"] for run in completed]
    payload = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "oos_validation",
        "skill": SKILL_NAME,
        "skill_version": SKILL_VERSION,
        "provider": args.provider,
        "accounting_mode": args.accounting_mode,
        "start": pd.Timestamp(args.start).strftime("%Y%m%d"),
        "end": pd.Timestamp(args.end).strftime("%Y%m%d"),
        "config": config.to_dict(),
        "folds": annual,
        "summary": {
            "completed_folds": len(completed),
            "failed_folds": len(annual) - len(completed),
            "mean_annual_return": float(np.mean(returns)) if returns else None,
            "best_year": max(completed, key=lambda run: run["total_return"])["fold"] if completed else None,
            "worst_year": min(completed, key=lambda run: run["total_return"])["fold"] if completed else None,
            "all_years_positive": bool(completed) and all(run["total_return"] > 0 for run in completed),
            "aggregate": None,
        },
    }
    payload["run_id"] = compute_run_id(payload)
    write_json_atomic(args.output, payload)
    for run in annual:
        status = f"error={run['error']}" if "error" in run else f"return={run['total_return']:.4f}"
        print(f"{run['fold']}: {status}")
    print(Path(args.output).resolve())


if __name__ == "__main__":
    main()
