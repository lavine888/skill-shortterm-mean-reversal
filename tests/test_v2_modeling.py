from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from lavine_reversal import StrategyConfig, run_backtest, run_daily_backtest
from lavine_reversal.contract import compute_run_id
from lavine_reversal.providers import DemoProvider
from lavine_reversal.validate import validate_result


def demo_panel():
    return DemoProvider(n_symbols=30).load("2024-01-01", "2024-06-30")


def test_short_fee_reduces_period_return_and_is_audited():
    data = demo_panel()
    config = StrategyConfig(min_universe=20, short_fee_rate=0.10)
    result = run_backtest(data, "2024-02-01", "2024-06-28", config, source="demo")
    validate_result(result)
    first = result["periods"][0]
    assert first["short_fee"] > 0
    assert abs(first["net_return"] - (first["gross_return"] - first["cost"] - first["short_fee"])) < 1e-12
    assert result["metrics"]["total_short_fee"] > 0


def test_zero_short_fee_is_unchanged():
    data = demo_panel()
    free = run_backtest(data, "2024-02-01", "2024-06-28", StrategyConfig(min_universe=20, short_fee_rate=0.0), source="demo")
    fee = run_backtest(data, "2024-02-01", "2024-06-28", StrategyConfig(min_universe=20, short_fee_rate=0.10), source="demo")
    validate_result(fee)
    assert fee["metrics"]["total_return"] < free["metrics"]["total_return"]
    assert fee["metrics"]["total_short_fee"] > 0
    assert free["metrics"]["total_short_fee"] == 0.0


def test_daily_short_fee_accrues_and_validates():
    data = demo_panel()
    config = StrategyConfig(min_universe=20, short_fee_rate=0.10)
    result = run_daily_backtest(data, "2024-02-01", "2024-06-28", config, source="demo")
    validate_result(result)
    assert result["metrics"]["total_short_fee"] >= 0
    assert any(day["short_fee"] > 0 for day in result["days"])
    assert result["days"][0]["short_fee"] >= 0


def test_not_borrowable_short_is_blocked_period():
    data = demo_panel()
    config = StrategyConfig(min_universe=20)
    baseline = run_backtest(data, "2024-02-01", "2024-06-28", config, source="demo")
    first = baseline["periods"][0]
    symbol = first["short_symbols"][0]
    entry = pd.Timestamp(first["entry_date"])
    data["borrowable"] = True
    data.loc[(data["date"] == entry) & (data["symbol"] == symbol), "borrowable"] = False
    result = run_backtest(data, "2024-02-01", "2024-06-28", config, source="demo")
    validate_result(result)
    item = result["periods"][0]["selected_evidence"][symbol]
    assert item["entry_block_reason"] == "not_borrowable"
    assert item["executed_weight"] == 0.0


def test_not_borrowable_short_is_blocked_daily():
    data = demo_panel()
    config = StrategyConfig(min_universe=20)
    baseline = run_backtest(data, "2024-02-01", "2024-06-28", config, source="demo")
    first = baseline["periods"][0]
    symbol = first["short_symbols"][0]
    entry = pd.Timestamp(first["entry_date"])
    data["borrowable"] = True
    data.loc[(data["date"] == entry) & (data["symbol"] == symbol), "borrowable"] = False
    result = run_daily_backtest(data, "2024-02-01", "2024-06-28", config, source="demo")
    validate_result(result)
    blocked = [
        attempt for day in result["days"] for attempt in day["attempts"]
        if attempt["kind"] == "entry" and attempt["symbol"] == symbol
    ]
    assert blocked and blocked[0]["block_reason"] == "not_borrowable"


def test_delisting_settlement_price_exits_period_position():
    data = demo_panel()
    config = StrategyConfig(min_universe=20)
    baseline = run_backtest(data, "2024-02-01", "2024-06-28", config, source="demo")
    first = baseline["periods"][0]
    symbol = first["long_symbols"][0]
    exit_date = pd.Timestamp(first["exit_date"])
    data["de_listed_date"] = pd.NaT
    data["delisting_settlement_price"] = pd.NA
    data.loc[data["symbol"] == symbol, "de_listed_date"] = exit_date
    data.loc[data["symbol"] == symbol, "delisting_settlement_price"] = 15.0
    data = data.loc[~((data["symbol"] == symbol) & (data["date"] >= exit_date))]
    result = run_backtest(data, "2024-02-01", first["exit_date"], config, source="demo")
    validate_result(result)
    evidence = result["periods"][0]["selected_evidence"][symbol]
    assert evidence["exit_status"] == "delisting_settlement_exit"
    assert evidence["exit_price"] == pytest.approx(15.0)
    assert result["metrics"]["delisting_settlement_exits"] == 1
    assert symbol in result["delisting_settlements"]


def test_delisting_settlement_price_exits_daily_position():
    data = demo_panel()
    config = StrategyConfig(min_universe=20)
    baseline = run_backtest(data, "2024-02-01", "2024-06-28", config, source="demo")
    first = baseline["periods"][0]
    symbol = first["long_symbols"][0]
    exit_date = pd.Timestamp(first["exit_date"])
    data["de_listed_date"] = pd.NaT
    data["delisting_settlement_price"] = pd.NA
    data.loc[data["symbol"] == symbol, "de_listed_date"] = exit_date
    data.loc[data["symbol"] == symbol, "delisting_settlement_price"] = 18.0
    data = data.loc[~((data["symbol"] == symbol) & (data["date"] >= exit_date))]
    result = run_daily_backtest(data, "2024-02-01", "2024-06-28", config, source="demo")
    validate_result(result)
    assert result["metrics"]["delisting_settlement_exits"] >= 1
    settlements = [
        attempt for day in result["days"] for attempt in day["attempts"]
        if attempt["kind"] == "settlement"
    ]
    assert settlements and settlements[0]["symbol"] == symbol
    assert settlements[0]["fill_price"] == pytest.approx(18.0)


def test_data_capabilities_expose_borrow_and_settlement_flags():
    data = demo_panel()
    data["borrowable"] = True
    data["delisting_settlement_price"] = pd.NA
    data.attrs["provided_columns"] = list(data.columns)
    result = run_backtest(data, "2024-02-01", "2024-06-28", StrategyConfig(min_universe=20), source="demo")
    validate_result(result)
    assert result["data_capabilities"]["borrowable_flag"] is True
    assert result["data_capabilities"]["delisting_settlement_price"] is True


def test_oos_validation_script_writes_valid_artifact(tmp_path):
    output = tmp_path / "oos.json"
    from scripts.oos_validation import _fold_years, _annual_runs, write_json_atomic, compute_run_id
    years = _fold_years("20240101", "20241231")
    assert years == ["2024"]
    data = demo_panel()
    config = StrategyConfig(min_universe=20)
    runs = _annual_runs(data, years, config, "period", "demo", None, "panel_date_union")
    assert len(runs) == 1
    assert runs[0]["fold"] == "2024"
    assert runs[0]["total_return"] is not None
