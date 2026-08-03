from __future__ import annotations

import pandas as pd
import numpy as np
import pytest

from lavine_reversal import StrategyConfig, build_snapshot


def panel(days: int = 8, symbols: int = 20) -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-02", periods=days)
    rows = []
    for day_index, date in enumerate(dates):
        for symbol_index in range(symbols):
            rows.append({"date": date, "symbol": f"{symbol_index:06d}.SZ", "close": 100 + symbol_index * day_index})
    return pd.DataFrame(rows)


def test_snapshot_uses_exact_market_session_lookback_and_deterministic_deciles():
    data = panel()
    decision = pd.bdate_range("2024-01-02", periods=8)[5]
    result = build_snapshot(data, decision, StrategyConfig(min_universe=20))
    assert result["lookback_date"] == "20240102"
    assert result["long_symbols"] == ["000000.SZ", "000001.SZ"]
    assert result["short_symbols"] == ["000018.SZ", "000019.SZ"]
    assert sum(result["weights"].values()) == pytest.approx(0.0)
    assert sum(abs(value) for value in result["weights"].values()) == pytest.approx(1.0)


def test_snapshot_ignores_future_rows():
    data = panel()
    decision = pd.bdate_range("2024-01-02", periods=8)[5]
    baseline = build_snapshot(data.loc[data["date"] <= decision], decision)
    changed = data.copy()
    changed.loc[changed["date"] > decision, "close"] *= 100
    future_only = pd.DataFrame({
        "date": [data["date"].max()], "symbol": ["999999.SZ"], "close": [10.0],
    })
    changed = pd.concat([changed, future_only], ignore_index=True)
    result = build_snapshot(changed, decision)
    assert result["weights"] == baseline["weights"]
    assert result["diagnostics"] == baseline["diagnostics"]


def test_snapshot_excludes_decision_day_st_rows():
    data = panel()
    decision = pd.bdate_range("2024-01-02", periods=8)[5]
    data["is_st"] = False
    data.loc[(data["date"] == decision) & (data["symbol"] == "000019.SZ"), "is_st"] = True
    result = build_snapshot(data, decision, StrategyConfig(min_universe=19))
    assert "000019.SZ" not in result["scores"]


def test_duplicate_rows_fail_closed():
    data = panel()
    with pytest.raises(ValueError, match="duplicate daily row"):
        build_snapshot(pd.concat([data, data.iloc[[0]]]), data["date"].iloc[-1])


def test_string_booleans_are_parsed_by_value():
    data = panel()
    data["is_st"] = "False"
    decision = pd.bdate_range("2024-01-02", periods=8)[5]
    result = build_snapshot(data, decision)
    assert result["universe_size"] == 20


@pytest.mark.parametrize("value", [np.nan, np.inf, -np.inf, 0.0, -1.0])
def test_invalid_close_fails_closed(value):
    data = panel()
    data["close"] = data["close"].astype(float)
    data.loc[0, "close"] = value
    with pytest.raises(ValueError, match="close must be finite and positive"):
        build_snapshot(data, data["date"].iloc[-1])


def test_invalid_boolean_fails_closed():
    data = panel()
    data["tradable"] = "unknown"
    with pytest.raises(ValueError, match="invalid boolean value"):
        build_snapshot(data, data["date"].iloc[-1])


def test_non_overlapping_engine_configuration_is_enforced():
    with pytest.raises(ValueError, match="rebalance_every == hold_days"):
        StrategyConfig(rebalance_every=1, hold_days=5)
    with pytest.raises(ValueError, match="cost_rate must be finite"):
        StrategyConfig(cost_rate=float("nan"))


def test_explicit_calendar_prevents_sparse_panel_from_changing_lookback():
    data = panel(days=8)
    dates = pd.bdate_range("2024-01-02", periods=8)
    sparse = data.loc[data["date"] != dates[2]]
    decision = dates[6]
    inferred = build_snapshot(sparse, decision)
    explicit = build_snapshot(sparse, decision, calendar=dates)
    assert inferred["lookback_date"] == dates[0].strftime("%Y%m%d")
    assert explicit["lookback_date"] == dates[1].strftime("%Y%m%d")
