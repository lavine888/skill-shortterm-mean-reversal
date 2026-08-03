from __future__ import annotations

import pandas as pd
import pytest

from lavine_reversal import StrategyConfig, run_backtest
from lavine_reversal.contract import compute_run_id
from lavine_reversal.providers import DemoProvider
from lavine_reversal.validate import validate_result


def test_backtest_delays_execution_and_holds_five_sessions():
    data = DemoProvider(n_symbols=30).load("2024-01-01", "2024-04-30")
    result = run_backtest(data, "2024-02-01", "2024-04-30", StrategyConfig(min_universe=20), source="demo")
    first = result["periods"][0]
    dates = sorted(data["date"].unique())
    decision_index = dates.index(pd.Timestamp(first["decision_date"]))
    assert pd.Timestamp(first["entry_date"]) == dates[decision_index + 1]
    assert pd.Timestamp(first["exit_date"]) == dates[decision_index + 6]


def test_costs_reduce_return_and_include_terminal_liquidation():
    data = DemoProvider(n_symbols=30).load("2024-01-01", "2024-06-30")
    free = run_backtest(data, "2024-02-01", "2024-06-28", StrategyConfig(min_universe=20, cost_rate=0.0))
    costly = run_backtest(data, "2024-02-01", "2024-06-28", StrategyConfig(min_universe=20, cost_rate=0.01))
    assert costly["metrics"]["total_return"] < free["metrics"]["total_return"]
    assert costly["periods"][-1]["cost"] > 0


def test_result_contract_validates_and_is_deterministic():
    data = DemoProvider(n_symbols=30).load("2024-01-01", "2024-06-30")
    left = run_backtest(data, "2024-02-01", "2024-06-28", StrategyConfig(min_universe=20), source="demo")
    right = run_backtest(data, "2024-02-01", "2024-06-28", StrategyConfig(min_universe=20), source="demo")
    validate_result(left)
    assert left["run_id"] == right["run_id"]
    assert left["metrics"]["periods"] > 0


def test_missing_entry_remains_cash_and_is_not_charged_as_a_fill():
    data = DemoProvider(n_symbols=30).load("2024-01-01", "2024-06-30")
    config = StrategyConfig(min_universe=20)
    baseline = run_backtest(data, "2024-02-01", "2024-06-28", config, source="demo")
    first = baseline["periods"][0]
    symbol = first["long_symbols"][0]
    target = abs(first["selected_evidence"][symbol]["target_weight"])
    missing = data.loc[~((data["date"] == pd.Timestamp(first["entry_date"])) & (data["symbol"] == symbol))]
    result = run_backtest(missing, "2024-02-01", "2024-06-28", config, source="demo")
    changed = result["periods"][0]
    assert changed["selected_evidence"][symbol]["entry_status"] == "unfilled"
    assert changed["selected_evidence"][symbol]["executed_weight"] == 0
    assert changed["forward_coverage"] < 1
    assert changed["traded_notional"] == pytest.approx(first["traded_notional"] - target)


def test_missing_exit_for_executed_position_fails_closed():
    data = DemoProvider(n_symbols=30).load("2024-01-01", "2024-06-30")
    config = StrategyConfig(min_universe=20)
    baseline = run_backtest(data, "2024-02-01", "2024-06-28", config, source="demo")
    first = baseline["periods"][0]
    symbol = first["long_symbols"][0]
    missing = data.loc[~((data["date"] == pd.Timestamp(first["exit_date"])) & (data["symbol"] == symbol))]
    with pytest.raises(ValueError, match="cannot value or exit"):
        run_backtest(missing, "2024-02-01", "2024-06-28", config, source="demo")


def test_confirmed_delisting_can_use_explicit_forced_exit_policy():
    data = DemoProvider(n_symbols=30).load("2024-01-01", "2024-06-30")
    baseline = run_backtest(data, "2024-02-01", "2024-06-28", StrategyConfig(min_universe=20), source="demo")
    first = baseline["periods"][0]
    symbol = first["long_symbols"][0]
    scheduled_exit = pd.Timestamp(first["exit_date"])
    data["de_listed_date"] = pd.NaT
    data.loc[data["symbol"] == symbol, "de_listed_date"] = scheduled_exit
    data = data.loc[~((data["symbol"] == symbol) & (data["date"] >= scheduled_exit))]
    config = StrategyConfig(min_universe=20, delisting_exit_policy="last_available_close")
    result = run_backtest(data, "2024-02-01", first["exit_date"], config, source="demo")
    evidence = result["periods"][0]["selected_evidence"][symbol]
    assert evidence["exit_status"] == "forced_delisting_exit"
    assert evidence["actual_exit_date"] < first["exit_date"]
    validate_result(result)


def test_turnover_uses_drifted_exit_weights():
    data = DemoProvider(n_symbols=30).load("2024-01-01", "2024-06-30")
    config = StrategyConfig(min_universe=20)
    result = run_backtest(data, "2024-02-01", "2024-06-28", config, source="demo")
    first, second = result["periods"][:2]
    ending_nav = 1.0 + first["net_return"]
    drifted = {
        symbol: item["executed_weight"] * (1.0 + item["forward_return"]) / ending_nav
        for symbol, item in first["selected_evidence"].items()
        if item["entry_status"] == "filled"
    }
    next_weights = {
        symbol: item["executed_weight"]
        for symbol, item in second["selected_evidence"].items()
        if item["entry_status"] == "filled"
    }
    expected = sum(abs(next_weights.get(symbol, 0.0) - drifted.get(symbol, 0.0)) for symbol in set(drifted) | set(next_weights))
    assert second["traded_notional"] == pytest.approx(expected)


def test_validator_rejects_tampering_even_with_recomputed_run_id():
    data = DemoProvider(n_symbols=30).load("2024-01-01", "2024-06-30")
    result = run_backtest(data, "2024-02-01", "2024-06-28", StrategyConfig(min_universe=20), source="demo")
    result["metrics"]["total_return"] += 0.1
    result["run_id"] = compute_run_id(result)
    with pytest.raises(ValueError, match="metrics.total_return is inconsistent"):
        validate_result(result)


def test_panel_hash_covers_delisting_dates():
    data = DemoProvider(n_symbols=30).load("2024-01-01", "2024-06-30")
    data["de_listed_date"] = pd.NaT
    config = StrategyConfig(min_universe=20)
    baseline = run_backtest(data, "2024-02-01", "2024-06-28", config, source="demo")
    changed = data.copy()
    changed.loc[changed["symbol"] == "000030.SZ", "de_listed_date"] = pd.Timestamp("2025-01-01")
    updated = run_backtest(changed, "2024-02-01", "2024-06-28", config, source="demo")
    assert baseline["source_context"]["panel_sha256"] != updated["source_context"]["panel_sha256"]


def test_validator_rejects_invalid_source_hash_with_recomputed_run_id():
    data = DemoProvider(n_symbols=30).load("2024-01-01", "2024-06-30")
    result = run_backtest(data, "2024-02-01", "2024-06-28", StrategyConfig(min_universe=20), source="demo")
    result["source_context"]["panel_sha256"] = "not-a-hash"
    result["run_id"] = compute_run_id(result)
    with pytest.raises(ValueError, match="hashes must be lowercase SHA-256"):
        validate_result(result)


@pytest.mark.parametrize(
    ("side", "limit_column", "reason"),
    [("long_symbols", "limit_up", "limit_up"), ("short_symbols", "limit_down", "limit_down")],
)
def test_directional_entry_limits_leave_blocked_targets_in_cash(side, limit_column, reason):
    data = DemoProvider(n_symbols=30).load("2024-01-01", "2024-06-30")
    config = StrategyConfig(min_universe=20)
    baseline = run_backtest(data, "2024-02-01", "2024-06-28", config, source="demo")
    first = baseline["periods"][0]
    symbol = first[side][0]
    entry = pd.Timestamp(first["entry_date"])
    data["limit_up"] = data["close"] * 2.0
    data["limit_down"] = data["close"] * 0.5
    mask = (data["date"] == entry) & (data["symbol"] == symbol)
    data.loc[mask, limit_column] = data.loc[mask, "close"]
    result = run_backtest(data, "2024-02-01", "2024-06-28", config, source="demo")
    item = result["periods"][0]["selected_evidence"][symbol]
    assert item["entry_status"] == "unfilled"
    assert item["entry_block_reason"] == reason
    assert item["executed_weight"] == 0.0


def test_long_position_at_limit_down_cannot_be_exited():
    data = DemoProvider(n_symbols=30).load("2024-01-01", "2024-06-30")
    config = StrategyConfig(min_universe=20)
    baseline = run_backtest(data, "2024-02-01", "2024-06-28", config, source="demo")
    first = baseline["periods"][0]
    symbol = first["long_symbols"][0]
    exit_date = pd.Timestamp(first["exit_date"])
    data["limit_up"] = data["close"] * 2.0
    data["limit_down"] = data["close"] * 0.5
    mask = (data["date"] == exit_date) & (data["symbol"] == symbol)
    data.loc[mask, "limit_down"] = data.loc[mask, "close"]
    with pytest.raises(ValueError, match=f"cannot value or exit.*{symbol}"):
        run_backtest(data, "2024-02-01", "2024-06-28", config, source="demo")
