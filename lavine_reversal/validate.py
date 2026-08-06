from __future__ import annotations

import math
import re
from numbers import Real
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .contract import compute_run_id
from .engine import _limit_block_reason, _rank_ic, calculate_stats
from .evidence import EVIDENCE_COLUMNS, EVIDENCE_SCHEMA_VERSION, file_sha256
from .version import SCHEMA_VERSION, SKILL_NAME, SKILL_VERSION


TOP_LEVEL_KEYS = {
    "schema_version", "skill", "skill_version", "source", "source_status",
    "start", "end", "config", "data_capabilities", "metrics", "periods",
    "source_context", "limitations", "run_id",
}
PERIOD_KEYS = {
    "lookback_date", "decision_date", "entry_date", "exit_date",
    "signal_universe_size", "long_symbols", "short_symbols",
    "selected_evidence", "forward_coverage", "rank_ic_sample_size",
    "rank_ic_coverage", "rank_ic", "gross_return", "traded_notional",
    "cost", "short_fee", "net_return", "forced_delisting_exit_count",
}
EVIDENCE_KEYS = {
    "past_return", "reversal_score", "target_weight", "executed_weight",
    "entry_price", "exit_price", "forward_return", "entry_status", "exit_status",
    "entry_block_reason", "exit_block_reason", "actual_exit_date",
}
SNAPSHOT_TOP_LEVEL_KEYS = {
    "schema_version", "artifact_type", "skill", "skill_version", "source",
    "source_status", "config", "source_context", "data_capabilities",
    "snapshot", "run_id",
}
SNAPSHOT_KEYS = {
    "decision_date", "lookback_date", "universe_size", "long_symbols",
    "short_symbols", "weights", "scores", "diagnostics",
}


def _assert_finite_json(value: Any, path: str = "result") -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, Real):
        if not math.isfinite(float(value)):
            raise ValueError(f"{path} contains a non-finite number")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _assert_finite_json(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} contains a non-string key")
            _assert_finite_json(item, f"{path}.{key}")
        return
    raise ValueError(f"{path} contains unsupported type {type(value).__name__}")


def _require_keys(value: dict[str, Any], required: set[str], path: str) -> None:
    missing = sorted(required - set(value))
    if missing:
        raise ValueError(f"{path} missing keys: {', '.join(missing)}")


def _close(left: float, right: float) -> bool:
    return math.isclose(float(left), float(right), rel_tol=1e-10, abs_tol=1e-12)


def _valid_date(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"\d{8}", value) is not None


def _valid_sha256(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _validate_source_context(payload: dict[str, Any]) -> None:
    context = payload["source_context"]
    required = {
        "row_count", "symbol_count", "first_date", "last_date", "panel_sha256",
        "calendar_sha256", "calendar_sessions", "provider_context",
    }
    _require_keys(context, required, "source_context")
    if any(not isinstance(context[key], int) or context[key] <= 0 for key in ("row_count", "symbol_count", "calendar_sessions")):
        raise ValueError("source_context counts must be positive integers")
    if not _valid_date(context["first_date"]) or not _valid_date(context["last_date"]) or context["first_date"] > context["last_date"]:
        raise ValueError("source_context dates are invalid")
    if not _valid_sha256(context["panel_sha256"]) or not _valid_sha256(context["calendar_sha256"]):
        raise ValueError("source_context hashes must be lowercase SHA-256")
    provider = context["provider_context"]
    if not isinstance(provider, dict) or not provider.get("provider"):
        raise ValueError("source_context provider_context is invalid")
    expected_status = "synthetic" if payload["source"] == "demo" else ("experimental" if payload["source"] == "pandadata" else "user_supplied")
    if payload["source_status"] != expected_status:
        raise ValueError("source_status is inconsistent with source")
    if payload["source"] == "pandadata":
        panda_required = {
            "sdk_version", "requested_universe_size", "universe_sha256",
            "cache_enabled", "response_count", "response_manifest_sha256",
        }
        _require_keys(provider, panda_required, "source_context.provider_context")
        if not _valid_sha256(provider["universe_sha256"]) or not _valid_sha256(provider["response_manifest_sha256"]):
            raise ValueError("PandaData provenance hashes are invalid")
        if not isinstance(provider["cache_enabled"], bool) or not isinstance(provider["response_count"], int) or provider["response_count"] <= 0:
            raise ValueError("PandaData response diagnostics are invalid")


def _validate_evidence_metadata(payload: dict[str, Any]) -> None:
    metadata = payload.get("factor_evidence")
    if metadata is None:
        return
    required = {"schema_version", "artifact_name", "row_count", "decision_count", "columns", "file_sha256"}
    _require_keys(metadata, required, "factor_evidence")
    if metadata["schema_version"] != EVIDENCE_SCHEMA_VERSION:
        raise ValueError("unsupported factor evidence schema")
    if metadata["columns"] != EVIDENCE_COLUMNS:
        raise ValueError("factor evidence columns do not match the fixed contract")
    if not isinstance(metadata["artifact_name"], str) or not metadata["artifact_name"].endswith(".parquet"):
        raise ValueError("factor evidence artifact name is invalid")
    if not isinstance(metadata["row_count"], int) or metadata["row_count"] <= 0:
        raise ValueError("factor evidence row count is invalid")
    if not isinstance(metadata["decision_count"], int) or metadata["decision_count"] <= 0:
        raise ValueError("factor evidence decision count is invalid")
    if metadata["decision_count"] != len(payload["periods"]):
        raise ValueError("factor evidence decision count does not match backtest periods")
    if metadata["row_count"] != sum(period["signal_universe_size"] for period in payload["periods"]):
        raise ValueError("factor evidence row count does not match period universes")
    if not _valid_sha256(metadata["file_sha256"]):
        raise ValueError("factor evidence hash is invalid")


def _validate_factor_evidence(payload: dict[str, Any], evidence_path: str | Path) -> None:
    metadata = payload.get("factor_evidence")
    if metadata is None:
        raise ValueError("result does not bind a factor evidence artifact")
    path = Path(evidence_path)
    if path.name != metadata["artifact_name"]:
        raise ValueError("factor evidence filename does not match result metadata")
    if file_sha256(path) != metadata["file_sha256"]:
        raise ValueError("factor evidence file hash does not match result metadata")
    frame = pd.read_parquet(path)
    if list(frame.columns) != EVIDENCE_COLUMNS:
        raise ValueError("factor evidence file columns do not match the fixed contract")
    if len(frame) != metadata["row_count"] or frame["decision_date"].nunique() != metadata["decision_count"]:
        raise ValueError("factor evidence file counts do not match result metadata")
    expected_decisions = {period["decision_date"] for period in payload["periods"]}
    if set(frame["decision_date"]) != expected_decisions:
        raise ValueError("factor evidence decision dates do not match backtest periods")
    if frame.duplicated(["decision_date", "symbol"]).any():
        raise ValueError("factor evidence contains duplicate decision-date symbols")
    if not frame["selected_side"].isin(["long", "short", "none"]).all():
        raise ValueError("factor evidence contains an invalid selected side")
    numeric = ["past_return", "reversal_score", "target_weight"]
    if not np.isfinite(frame[numeric].to_numpy(dtype=float)).all():
        raise ValueError("factor evidence contains non-finite required values")
    boolean_columns = [
        "entry_suspended", "entry_is_st", "entry_tradable", "entry_borrowable",
        "exit_suspended", "exit_is_st", "exit_tradable", "exit_borrowable",
    ]
    if frame[boolean_columns].isna().any().any():
        raise ValueError("factor evidence contains missing trading-state flags")
    allowed_reasons = {None, "missing_price", "suspended", "st", "non_tradable", "not_borrowable", "limit_up", "limit_down", "not_applicable"}
    entry_reasons = {None if pd.isna(value) else value for value in frame["entry_block_reason"]}
    exit_reasons = {None if pd.isna(value) else value for value in frame["exit_block_reason"]}
    if not entry_reasons.issubset(allowed_reasons):
        raise ValueError("factor evidence contains an invalid entry block reason")
    if not exit_reasons.issubset(allowed_reasons):
        raise ValueError("factor evidence contains an invalid exit block reason")
    for column in ("entry_limit_up", "entry_limit_down", "exit_limit_up", "exit_limit_down"):
        present = frame[column].dropna().to_numpy(dtype=float)
        if len(present) and (not np.isfinite(present).all() or not (present > 0).all()):
            raise ValueError(f"factor evidence contains invalid {column} values")

    for period in payload["periods"]:
        decision = period["decision_date"]
        group = frame.loc[frame["decision_date"] == decision].copy()
        if len(group) != period["signal_universe_size"]:
            raise ValueError(f"factor evidence universe size is inconsistent on {decision}")
        for column in ("lookback_date", "entry_date", "exit_date"):
            if set(group[column]) != {period[column]}:
                raise ValueError(f"factor evidence {column} is inconsistent on {decision}")
        if not np.allclose(group["reversal_score"], -group["past_return"], rtol=1e-12, atol=1e-14):
            raise ValueError(f"factor evidence reversal scores are inconsistent on {decision}")

        ordered = group.sort_values(["past_return", "symbol"], kind="mergesort")
        expected_long = set(ordered.iloc[:len(period["long_symbols"])]["symbol"])
        expected_short = set(ordered.iloc[-len(period["short_symbols"]):]["symbol"])
        actual_long = set(group.loc[group["selected_side"] == "long", "symbol"])
        actual_short = set(group.loc[group["selected_side"] == "short", "symbol"])
        if actual_long != expected_long or actual_long != set(period["long_symbols"]):
            raise ValueError(f"factor evidence long selection is inconsistent on {decision}")
        if actual_short != expected_short or actual_short != set(period["short_symbols"]):
            raise ValueError(f"factor evidence short selection is inconsistent on {decision}")
        selected = actual_long | actual_short
        if not group.loc[~group["symbol"].isin(selected), "target_weight"].eq(0.0).all():
            raise ValueError(f"factor evidence non-selected weights are nonzero on {decision}")
        period_evidence = period["selected_evidence"]
        for row in group.loc[group["symbol"].isin(selected)].itertuples(index=False):
            item = period_evidence[row.symbol]
            if not _close(row.target_weight, item["target_weight"]):
                raise ValueError(f"factor evidence selected weight is inconsistent for {row.symbol}")
            if row.entry_price is None or pd.isna(row.entry_price):
                expected_entry_block = "missing_price"
            elif row.entry_suspended:
                expected_entry_block = "suspended"
            elif row.entry_is_st:
                expected_entry_block = "st"
            elif not row.entry_tradable:
                expected_entry_block = "non_tradable"
            elif row.target_weight < 0 and not row.entry_borrowable:
                expected_entry_block = "not_borrowable"
            else:
                expected_entry_block = _limit_block_reason(
                    row.target_weight, "entry", row.entry_price,
                    row.entry_limit_up if pd.notna(row.entry_limit_up) else None,
                    row.entry_limit_down if pd.notna(row.entry_limit_down) else None,
                )
            row_entry_reason = None if pd.isna(row.entry_block_reason) else row.entry_block_reason
            if row_entry_reason != expected_entry_block or item["entry_block_reason"] != expected_entry_block:
                raise ValueError(f"factor evidence entry block reason is inconsistent for {row.symbol}")
            if expected_entry_block is not None:
                expected_exit_block = "not_applicable"
            elif row.exit_price is None or pd.isna(row.exit_price):
                expected_exit_block = "missing_price"
            elif row.exit_suspended:
                expected_exit_block = "suspended"
            elif not row.exit_tradable:
                expected_exit_block = "non_tradable"
            else:
                expected_exit_block = _limit_block_reason(
                    row.target_weight, "exit", row.exit_price,
                    row.exit_limit_up if pd.notna(row.exit_limit_up) else None,
                    row.exit_limit_down if pd.notna(row.exit_limit_down) else None,
                )
            if item["exit_status"] in ("forced_delisting_exit", "delisting_settlement_exit"):
                expected_exit_block = None
            row_exit_reason = None if pd.isna(row.exit_block_reason) else row.exit_block_reason
            if row_exit_reason != expected_exit_block or item["exit_block_reason"] != expected_exit_block:
                raise ValueError(f"factor evidence exit block reason is inconsistent for {row.symbol}")

        priced = group["entry_price"].notna() & group["exit_price"].notna()
        expected_forward = group.loc[priced, "exit_price"] / group.loc[priced, "entry_price"] - 1.0
        if not np.allclose(group.loc[priced, "forward_return"], expected_forward, rtol=1e-10, atol=1e-12):
            raise ValueError(f"factor evidence forward returns are inconsistent on {decision}")
        if group.loc[~priced, "forward_return"].notna().any():
            raise ValueError(f"factor evidence has forward returns without endpoint prices on {decision}")
        sample_size = int(group["forward_return"].notna().sum())
        if sample_size != period["rank_ic_sample_size"]:
            raise ValueError(f"factor evidence Rank IC sample size is inconsistent on {decision}")
        rank_ic = _rank_ic(group.set_index("symbol")["reversal_score"], group.set_index("symbol")["forward_return"])
        if rank_ic is None:
            if period["rank_ic"] is not None:
                raise ValueError(f"factor evidence Rank IC is inconsistent on {decision}")
        elif period["rank_ic"] is None or not _close(rank_ic, period["rank_ic"]):
            raise ValueError(f"factor evidence Rank IC is inconsistent on {decision}")


def validate_result(payload: dict[str, Any], evidence_path: str | Path | None = None) -> None:
    if payload.get("artifact_type") == "daily_nav_backtest":
        if evidence_path is not None:
            raise ValueError("cross-sectional evidence is not supported for daily NAV results")
        from .daily_validate import validate_daily_result
        validate_daily_result(payload)
        return
    if not isinstance(payload, dict):
        raise ValueError("result must be an object")
    _assert_finite_json(payload)
    _require_keys(payload, TOP_LEVEL_KEYS, "result")
    if (
        payload["schema_version"] != SCHEMA_VERSION
        or payload["skill"] != SKILL_NAME
        or payload["skill_version"] != SKILL_VERSION
    ):
        raise ValueError("unsupported result contract")
    if payload["source_status"] not in {"synthetic", "experimental", "user_supplied"}:
        raise ValueError("invalid source_status")
    capabilities = payload["data_capabilities"]
    if capabilities.get("calendar_source") not in {"panel_date_union", "explicit", "pandadata"}:
        raise ValueError("invalid market calendar source")
    _validate_source_context(payload)
    if not _valid_date(payload["start"]) or not _valid_date(payload["end"]) or payload["start"] > payload["end"]:
        raise ValueError("invalid result date range")
    if not isinstance(payload["periods"], list) or not payload["periods"]:
        raise ValueError("periods must be a non-empty list")
    if compute_run_id(payload) != payload["run_id"]:
        raise ValueError("run_id does not match canonical result content")
    _validate_evidence_metadata(payload)

    config = payload["config"]
    cost_rate = float(config["cost_rate"])
    previous_decision = ""
    for index, period in enumerate(payload["periods"]):
        path = f"periods[{index}]"
        if not isinstance(period, dict):
            raise ValueError(f"{path} must be an object")
        _require_keys(period, PERIOD_KEYS, path)
        period_dates = [period[key] for key in ("lookback_date", "decision_date", "entry_date", "exit_date")]
        if not all(_valid_date(value) for value in period_dates) or not period_dates[0] < period_dates[1] < period_dates[2] < period_dates[3]:
            raise ValueError(f"{path} dates must satisfy lookback < decision < entry < exit")
        if previous_decision and period["decision_date"] <= previous_decision:
            raise ValueError("decision dates must be strictly increasing")
        previous_decision = period["decision_date"]

        longs, shorts = period["long_symbols"], period["short_symbols"]
        if len(longs) != len(set(longs)) or len(shorts) != len(set(shorts)) or set(longs) & set(shorts):
            raise ValueError(f"{path} selections contain duplicates or overlap")
        selected = longs + shorts
        evidence = period["selected_evidence"]
        if set(evidence) != set(selected):
            raise ValueError(f"{path} evidence does not match selected symbols")
        gross_return = 0.0
        priced = 0
        forced_delisting_exits = 0
        settlement_exits = 0
        for symbol in selected:
            item = evidence[symbol]
            _require_keys(item, EVIDENCE_KEYS, f"{path}.selected_evidence.{symbol}")
            if not _close(item["reversal_score"], -item["past_return"]):
                raise ValueError(f"{path} has inconsistent reversal score for {symbol}")
            target, executed = float(item["target_weight"]), float(item["executed_weight"])
            if symbol in longs and target <= 0 or symbol in shorts and target >= 0:
                raise ValueError(f"{path} has wrong target weight sign for {symbol}")
            if item["entry_status"] == "filled":
                if not _close(executed, target) or item["entry_price"] is None or item["entry_block_reason"] is not None:
                    raise ValueError(f"{path} has inconsistent filled entry for {symbol}")
                if item["exit_status"] not in {"filled", "forced_delisting_exit", "delisting_settlement_exit"} or item["exit_price"] is None or item["forward_return"] is None:
                    raise ValueError(f"{path} has unresolved filled position for {symbol}")
                if not _valid_date(item["actual_exit_date"]) or not period["entry_date"] <= item["actual_exit_date"] <= period["exit_date"]:
                    raise ValueError(f"{path} has invalid actual exit date for {symbol}")
                if item["exit_block_reason"] is not None:
                    raise ValueError(f"{path} has inconsistent filled exit for {symbol}")
                expected_return = float(item["exit_price"]) / float(item["entry_price"]) - 1.0
                if not _close(item["forward_return"], expected_return):
                    raise ValueError(f"{path} has inconsistent forward return for {symbol}")
                gross_return += executed * float(item["forward_return"])
                priced += 1
                forced_delisting_exits += item["exit_status"] == "forced_delisting_exit"
                settlement_exits += item["exit_status"] == "delisting_settlement_exit"
            elif item["entry_status"] == "unfilled":
                if not _close(executed, 0.0) or item["entry_price"] is not None or item["forward_return"] is not None or item["actual_exit_date"] is not None or not item["entry_block_reason"]:
                    raise ValueError(f"{path} has inconsistent unfilled entry for {symbol}")
                if item["exit_block_reason"] != "not_applicable":
                    raise ValueError(f"{path} has inconsistent unfilled exit for {symbol}")
            else:
                raise ValueError(f"{path} has invalid entry status for {symbol}")
        if not _close(sum(evidence[s]["target_weight"] for s in longs), 0.5):
            raise ValueError(f"{path} long target weights do not sum to 0.5")
        if not _close(sum(evidence[s]["target_weight"] for s in shorts), -0.5):
            raise ValueError(f"{path} short target weights do not sum to -0.5")
        if not _close(period["forward_coverage"], priced / len(selected)):
            raise ValueError(f"{path} forward coverage is inconsistent")
        if not 0 <= period["rank_ic_sample_size"] <= period["signal_universe_size"]:
            raise ValueError(f"{path} rank IC sample size is invalid")
        if not _close(period["rank_ic_coverage"], period["rank_ic_sample_size"] / period["signal_universe_size"]):
            raise ValueError(f"{path} rank IC coverage is inconsistent")
        if not _close(period["gross_return"], gross_return):
            raise ValueError(f"{path} gross return is inconsistent")
        if period["forced_delisting_exit_count"] != forced_delisting_exits:
            raise ValueError(f"{path} forced delisting exit count is inconsistent")
        if period["delisting_settlement_exit_count"] != settlement_exits:
            raise ValueError(f"{path} delisting settlement exit count is inconsistent")
        if not _close(period["cost"], cost_rate * period["traded_notional"]):
            raise ValueError(f"{path} cost is inconsistent")
        short_executed = sum(
            abs(item["executed_weight"])
            for item in evidence.values()
            if item["entry_status"] == "filled" and item["executed_weight"] < 0
        )
        expected_short_fee = float(config["short_fee_rate"]) * (int(config["hold_days"]) / 252.0) * short_executed
        if not _close(period["short_fee"], expected_short_fee):
            raise ValueError(f"{path} short fee is inconsistent")
        if not _close(period["net_return"], period["gross_return"] - period["cost"] - period["short_fee"]):
            raise ValueError(f"{path} net return is inconsistent")

    metrics = payload["metrics"]
    returns = [float(period["net_return"]) for period in payload["periods"]]
    expected_stats = calculate_stats(returns, int(config["hold_days"]))
    for key, expected in expected_stats.items():
        actual = metrics.get(key)
        if expected is None:
            if actual is not None:
                raise ValueError(f"metrics.{key} is inconsistent")
        elif actual is None or not _close(actual, expected):
            raise ValueError(f"metrics.{key} is inconsistent")
    expected_means = {
        "average_traded_notional": np.mean([period["traded_notional"] for period in payload["periods"]]),
        "average_forward_coverage": np.mean([period["forward_coverage"] for period in payload["periods"]]),
        "average_rank_ic_coverage": np.mean([period["rank_ic_coverage"] for period in payload["periods"]]),
    }
    rank_ics = [period["rank_ic"] for period in payload["periods"] if period["rank_ic"] is not None]
    expected_means["mean_rank_ic"] = np.mean(rank_ics) if rank_ics else None
    expected_means["forced_delisting_exits"] = sum(period["forced_delisting_exit_count"] for period in payload["periods"])
    expected_means["delisting_settlement_exits"] = sum(period["delisting_settlement_exit_count"] for period in payload["periods"])
    expected_means["total_short_fee"] = sum(period["short_fee"] for period in payload["periods"])
    for key, expected in expected_means.items():
        actual = metrics.get(key)
        if expected is None:
            if actual is not None:
                raise ValueError(f"metrics.{key} is inconsistent")
        elif actual is None or not _close(actual, expected):
            raise ValueError(f"metrics.{key} is inconsistent")
    if evidence_path is not None:
        _validate_factor_evidence(payload, evidence_path)


def validate_snapshot_result(payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        raise ValueError("snapshot result must be an object")
    _assert_finite_json(payload)
    _require_keys(payload, SNAPSHOT_TOP_LEVEL_KEYS, "snapshot result")
    if (
        payload["schema_version"] != SCHEMA_VERSION
        or payload["artifact_type"] != "factor_snapshot"
        or payload["skill"] != SKILL_NAME
        or payload["skill_version"] != SKILL_VERSION
    ):
        raise ValueError("unsupported snapshot contract")
    if compute_run_id(payload) != payload["run_id"]:
        raise ValueError("run_id does not match canonical snapshot content")
    _validate_source_context(payload)
    snapshot = payload["snapshot"]
    _require_keys(snapshot, SNAPSHOT_KEYS, "snapshot")
    if not _valid_date(snapshot["lookback_date"]) or not _valid_date(snapshot["decision_date"]) or snapshot["lookback_date"] >= snapshot["decision_date"]:
        raise ValueError("snapshot dates are invalid")
    longs, shorts = snapshot["long_symbols"], snapshot["short_symbols"]
    if len(longs) != len(set(longs)) or len(shorts) != len(set(shorts)) or set(longs) & set(shorts):
        raise ValueError("snapshot selections contain duplicates or overlap")
    selected = longs + shorts
    if set(snapshot["weights"]) != set(selected):
        raise ValueError("snapshot weights do not match selections")
    if snapshot["universe_size"] != len(snapshot["scores"]):
        raise ValueError("snapshot universe size does not match scores")
    diagnostics = snapshot["diagnostics"]
    if diagnostics["eligible_symbol_count"] != snapshot["universe_size"]:
        raise ValueError("snapshot diagnostics eligible count is inconsistent")
    excluded_symbols = [symbol for symbols in diagnostics["exclusions"].values() for symbol in symbols]
    if len(excluded_symbols) != len(set(excluded_symbols)):
        raise ValueError("snapshot diagnostics contain duplicate exclusions")
    if diagnostics["excluded_symbol_count"] != len(excluded_symbols):
        raise ValueError("snapshot diagnostics excluded count is inconsistent")
    if diagnostics["input_symbol_count"] != diagnostics["eligible_symbol_count"] + diagnostics["excluded_symbol_count"]:
        raise ValueError("snapshot diagnostics input count is inconsistent")
    if not set(selected).issubset(snapshot["scores"]):
        raise ValueError("snapshot selected symbols are absent from scores")
    for symbol, values in snapshot["scores"].items():
        if not _close(values["reversal_score"], -values["past_return"]):
            raise ValueError(f"snapshot has inconsistent reversal score for {symbol}")
    if not _close(sum(snapshot["weights"][symbol] for symbol in longs), 0.5):
        raise ValueError("snapshot long weights do not sum to 0.5")
    if not _close(sum(snapshot["weights"][symbol] for symbol in shorts), -0.5):
        raise ValueError("snapshot short weights do not sum to -0.5")
