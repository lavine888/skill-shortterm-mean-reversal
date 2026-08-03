from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lavine_reversal import StrategyConfig, build_snapshot
from lavine_reversal.contract import compute_run_id, write_json_atomic
from lavine_reversal.engine import build_source_context
from lavine_reversal.factor import market_dates
from lavine_reversal.providers import DemoProvider, FileProvider, PandaDataProvider
from lavine_reversal.validate import validate_snapshot_result
from lavine_reversal.version import SCHEMA_VERSION, SKILL_NAME, SKILL_VERSION


def main() -> None:
    parser = argparse.ArgumentParser(description="Build one point-in-time Q58 factor snapshot")
    parser.add_argument("--provider", choices=["demo", "file", "pandadata"], required=True)
    parser.add_argument("--input")
    parser.add_argument("--as-of", required=True)
    universe = parser.add_mutually_exclusive_group()
    universe.add_argument("--symbols", nargs="+")
    universe.add_argument("--all-a", action="store_true")
    parser.add_argument("--calendar", help="CSV/text market calendar with a date column")
    parser.add_argument("--cache-dir", default="output/panda-cache", help="PandaData request cache")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        as_of = pd.Timestamp(args.as_of)
    except Exception as exc:
        parser.error(f"invalid --as-of date: {exc}")
    output = Path(args.output).resolve()
    calendar = None
    calendar_source = "panel_date_union"
    if args.calendar:
        calendar_path = Path(args.calendar).resolve()
        if calendar_path == output:
            parser.error("--output must not overwrite --calendar")
        calendar_frame = pd.read_csv(calendar_path)
        calendar = calendar_frame["date"] if "date" in calendar_frame else calendar_frame.iloc[:, 0]
        calendar_source = "explicit"
    if args.provider == "file":
        if not args.input:
            parser.error("--input is required for provider=file")
        if args.all_a:
            parser.error("--all-a is only valid for provider=pandadata")
        if Path(args.input).resolve() == output:
            parser.error("--output must not overwrite --input")
        provider = FileProvider(args.input)
    elif args.provider == "pandadata":
        if args.input:
            parser.error("--input is not valid for provider=pandadata")
        if not args.all_a and not args.symbols:
            parser.error("pass --all-a or --symbols for provider=pandadata")
        provider = PandaDataProvider(cache_dir=args.cache_dir)
    else:
        if args.input or args.all_a:
            parser.error("--input and --all-a are not valid for provider=demo")
        provider = DemoProvider()
    start = (as_of - timedelta(days=45)).strftime("%Y%m%d")
    if args.provider == "pandadata" and args.all_a:
        panel = provider.load(start, args.as_of, None, universe_as_of=args.as_of)
    else:
        panel = provider.load(start, args.as_of, None if args.all_a else args.symbols)
    if isinstance(provider, PandaDataProvider):
        if calendar is None:
            calendar = provider.load_calendar(start, args.as_of)
            calendar_source = "pandadata"
        provider.bind_runtime_context(panel)
        cache = provider.cache_diagnostics()
        print(f"PandaData cache hits={cache['hits']} misses={cache['misses']}")
    config = StrategyConfig()
    provided_columns = set(panel.attrs.get("provided_columns", panel.columns))
    dates = market_dates(panel, calendar)
    snapshot = build_snapshot(panel, args.as_of, config, calendar=dates)
    result = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "factor_snapshot",
        "skill": SKILL_NAME,
        "skill_version": SKILL_VERSION,
        "source": args.provider,
        "source_status": "synthetic" if args.provider == "demo" else ("experimental" if args.provider == "pandadata" else "user_supplied"),
        "config": config.to_dict(),
        "source_context": build_source_context(panel, dates),
        "data_capabilities": {
            "post_adjusted_close": "provider_method" if args.provider == "pandadata" else ("synthetic" if args.provider == "demo" else "declared_by_input_contract"),
            "suspended_flag": "suspended" in provided_columns,
            "st_flag": "is_st" in provided_columns,
            "tradable_flag": "tradable" in provided_columns,
            "trade_status": "trade_status" in provided_columns,
            "limit_prices": {"limit_up", "limit_down"}.issubset(provided_columns),
            "historical_name": "name" in provided_columns,
            "delisting_date": "de_listed_date" in provided_columns,
            "calendar_source": calendar_source,
        },
        "snapshot": snapshot,
    }
    result["run_id"] = compute_run_id(result)
    validate_snapshot_result(result)
    write_json_atomic(output, result)
    print(Path(args.output))


if __name__ == "__main__":
    main()
