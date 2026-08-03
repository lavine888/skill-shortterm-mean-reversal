from __future__ import annotations

from copy import deepcopy

import pandas as pd
import pytest

from lavine_reversal import StrategyConfig, run_backtest, run_daily_backtest
from lavine_reversal.contract import compute_run_id
from lavine_reversal.providers import DemoProvider
from lavine_reversal.validate import validate_result


def daily_fixture(with_blocked_exit: bool = False):
    panel = DemoProvider(n_symbols=30).load("2024-01-01", "2024-06-30")
    config = StrategyConfig(min_universe=20)
    if with_blocked_exit:
        baseline = run_backtest(panel, "2024-02-01", "2024-06-28", config, source="demo")
        first = baseline["periods"][0]
        symbol = first["long_symbols"][0]
        exit_date = pd.Timestamp(first["exit_date"])
        panel["limit_up"] = panel["close"] * 2.0
        panel["limit_down"] = panel["close"] * 0.5
        mask = (panel["date"] == exit_date) & (panel["symbol"] == symbol)
        panel.loc[mask, "limit_down"] = panel.loc[mask, "close"]
    else:
        symbol = None
        exit_date = None
    result = run_daily_backtest(
        panel, "2024-02-01", "2024-06-28", config, source="demo",
    )
    return result, symbol, exit_date


def test_daily_nav_ledger_replays_and_is_deterministic():
    first, _, _ = daily_fixture()
    second, _, _ = daily_fixture()
    validate_result(first)
    assert first["run_id"] == second["run_id"]
    assert first["metrics"]["open_position_count"] == 0
    assert first["metrics"]["filled_entries"] == first["metrics"]["filled_exits"]


def test_blocked_exit_is_retried_and_filled_next_session():
    result, symbol, exit_date = daily_fixture(with_blocked_exit=True)
    validate_result(result)
    attempts = [
        (day["date"], attempt)
        for day in result["days"]
        for attempt in day["attempts"]
        if attempt["kind"] == "exit" and attempt["symbol"] == symbol
    ]
    blocked_index = next(
        index for index, (date, attempt) in enumerate(attempts)
        if date == exit_date.strftime("%Y%m%d") and attempt["block_reason"] == "limit_down"
    )
    assert attempts[blocked_index][1]["status"] == "blocked"
    assert attempts[blocked_index + 1][1]["status"] == "filled"
    assert attempts[blocked_index + 1][1]["attempt_number"] == 2
    assert result["metrics"]["blocked_exit_attempts"] >= 1


def test_validator_rejects_a_missing_exit_retry():
    result, symbol, exit_date = daily_fixture(with_blocked_exit=True)
    tampered = deepcopy(result)
    days = tampered["days"]
    blocked_day = next(index for index, day in enumerate(days) if day["date"] == exit_date.strftime("%Y%m%d"))
    retry_day = days[blocked_day + 1]
    retry_day["attempts"] = [
        attempt for attempt in retry_day["attempts"]
        if not (attempt["kind"] == "exit" and attempt["symbol"] == symbol)
    ]
    tampered["run_id"] = compute_run_id(tampered)
    with pytest.raises(ValueError, match="pending exits were not attempted"):
        validate_result(tampered)


def test_validator_rejects_daily_order_cost_tampering():
    result, _, _ = daily_fixture()
    tampered = deepcopy(result)
    attempt = next(
        attempt for day in tampered["days"] for attempt in day["attempts"]
        if attempt["status"] == "filled"
    )
    attempt["cost"] += 0.01
    tampered["run_id"] = compute_run_id(tampered)
    with pytest.raises(ValueError, match="accounting is inconsistent"):
        validate_result(tampered)


def test_terminal_blocked_exit_remains_an_audited_open_position():
    panel = DemoProvider(n_symbols=30).load("2024-01-01", "2024-04-30")
    config = StrategyConfig(min_universe=20)
    baseline = run_backtest(panel, "2024-02-01", "2024-04-30", config, source="demo")
    first = baseline["periods"][0]
    symbol = first["long_symbols"][0]
    exit_date = pd.Timestamp(first["exit_date"])
    panel["limit_up"] = panel["close"] * 2.0
    panel["limit_down"] = panel["close"] * 0.5
    mask = (panel["date"] >= exit_date) & (panel["symbol"] == symbol)
    panel.loc[mask, "limit_down"] = panel.loc[mask, "close"]
    result = run_daily_backtest(
        panel, "2024-02-01", exit_date.strftime("%Y%m%d"), config, source="demo",
    )
    validate_result(result)
    assert result["metrics"]["open_position_count"] == 1
    assert result["metrics"]["unresolved_exit_count"] == 1
    assert result["final_positions"][0]["symbol"] == symbol


def test_missing_mark_after_a_blocked_exit_fails_closed():
    panel = DemoProvider(n_symbols=30).load("2024-01-01", "2024-04-30")
    config = StrategyConfig(min_universe=20)
    baseline = run_backtest(panel, "2024-02-01", "2024-04-30", config, source="demo")
    first = baseline["periods"][0]
    symbol = first["long_symbols"][0]
    exit_date = pd.Timestamp(first["exit_date"])
    next_date = panel.loc[panel["date"] > exit_date, "date"].min()
    panel["limit_up"] = panel["close"] * 2.0
    panel["limit_down"] = panel["close"] * 0.5
    exit_mask = (panel["date"] == exit_date) & (panel["symbol"] == symbol)
    panel.loc[exit_mask, "limit_down"] = panel.loc[exit_mask, "close"]
    panel = panel.loc[~((panel["date"] == next_date) & (panel["symbol"] == symbol))]
    with pytest.raises(ValueError, match=f"cannot mark open position.*{symbol}"):
        run_daily_backtest(
            panel, "2024-02-01", next_date.strftime("%Y%m%d"), config, source="demo",
        )


def test_existing_position_can_exit_after_becoming_st():
    panel = DemoProvider(n_symbols=30).load("2024-01-01", "2024-04-30")
    config = StrategyConfig(min_universe=20)
    baseline = run_backtest(panel, "2024-02-01", "2024-04-30", config, source="demo")
    first = baseline["periods"][0]
    symbol = first["long_symbols"][0]
    exit_date = pd.Timestamp(first["exit_date"])
    mask = (panel["date"] == exit_date) & (panel["symbol"] == symbol)
    panel.loc[mask, "is_st"] = True
    period_result = run_backtest(panel, "2024-02-01", "2024-04-30", config, source="demo")
    validate_result(period_result)
    assert period_result["periods"][0]["selected_evidence"][symbol]["exit_status"] == "filled"
    daily_result = run_daily_backtest(panel, "2024-02-01", "2024-04-30", config, source="demo")
    validate_result(daily_result)
    attempt = next(
        attempt for day in daily_result["days"] for attempt in day["attempts"]
        if attempt["kind"] == "exit" and attempt["symbol"] == symbol
    )
    assert attempt["is_st"] is True
    assert attempt["status"] == "filled"
