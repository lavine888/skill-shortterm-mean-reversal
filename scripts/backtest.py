from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lavine_reversal import StrategyConfig, run_backtest
from lavine_reversal.contract import write_json_atomic
from lavine_reversal.evidence import FactorEvidenceWriter, attach_factor_evidence
from lavine_reversal.providers import DemoProvider, FileProvider, PandaDataProvider
from lavine_reversal.validate import validate_result


def main() -> None:
    parser = argparse.ArgumentParser(description="Q58 five-session short-term reversal research backtest")
    parser.add_argument("--provider", choices=["demo", "file", "pandadata"], required=True)
    parser.add_argument("--input", help="CSV or Parquet daily panel for provider=file")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    universe = parser.add_mutually_exclusive_group()
    universe.add_argument("--symbols", nargs="+")
    universe.add_argument("--all-a", action="store_true", help="Use PandaData full SH/SZ universe")
    parser.add_argument("--cost-rate", type=float, default=0.001)
    parser.add_argument("--delisting-exit-policy", choices=["error", "last_available_close"], default="error")
    parser.add_argument("--calendar", help="CSV/text market calendar with a date column")
    parser.add_argument("--output", required=True)
    parser.add_argument("--evidence-output", help="Full cross-sectional factor evidence Parquet")
    parser.add_argument("--request-interval", type=float, default=1.0)
    parser.add_argument("--cache-dir", default="output/panda-cache", help="PandaData request cache")
    args = parser.parse_args()
    try:
        start, end = pd.Timestamp(args.start), pd.Timestamp(args.end)
    except Exception as exc:
        parser.error(f"invalid date: {exc}")
    if start > end:
        parser.error("--start must be on or before --end")
    if args.request_interval < 0:
        parser.error("--request-interval must be non-negative")
    output = Path(args.output).resolve()
    evidence_output = Path(args.evidence_output).resolve() if args.evidence_output else None
    if evidence_output == output:
        parser.error("--evidence-output must differ from --output")
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
        if evidence_output is not None and Path(args.input).resolve() == evidence_output:
            parser.error("--evidence-output must not overwrite --input")
        provider = FileProvider(args.input)
    elif args.provider == "pandadata":
        if args.input:
            parser.error("--input is not valid for provider=pandadata")
        if not args.all_a and not args.symbols:
            parser.error("pass --all-a or --symbols for provider=pandadata")
        provider = PandaDataProvider(request_interval=args.request_interval, cache_dir=args.cache_dir)
    else:
        if args.input or args.all_a:
            parser.error("--input and --all-a are not valid for provider=demo")
        provider = DemoProvider()
    config = StrategyConfig(cost_rate=args.cost_rate, delisting_exit_policy=args.delisting_exit_policy)
    load_start = (pd.Timestamp(args.start) - timedelta(days=45)).strftime("%Y%m%d")
    panel = provider.load(load_start, args.end, None if args.all_a else args.symbols)
    if isinstance(provider, PandaDataProvider):
        if calendar is None:
            calendar = provider.load_calendar(load_start, args.end)
            calendar_source = "pandadata"
        provider.bind_runtime_context(panel)
        cache = provider.cache_diagnostics()
        print(f"PandaData cache hits={cache['hits']} misses={cache['misses']}")
    if evidence_output is not None:
        with FactorEvidenceWriter(evidence_output) as writer:
            result = run_backtest(
                panel, args.start, args.end, config, source=args.provider,
                calendar=calendar, calendar_source=calendar_source,
                evidence_sink=writer.write,
            )
        attach_factor_evidence(result, writer.metadata())
    else:
        result = run_backtest(
            panel, args.start, args.end, config, source=args.provider,
            calendar=calendar, calendar_source=calendar_source,
        )
    validate_result(result, evidence_path=evidence_output)
    write_json_atomic(output, result)
    metrics = result["metrics"]
    print(f"run_id={result['run_id']} periods={metrics['periods']} total_return={metrics['total_return']:.4f} mean_rank_ic={metrics['mean_rank_ic']}")
    print(Path(args.output))


if __name__ == "__main__":
    main()
