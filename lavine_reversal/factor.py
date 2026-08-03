from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .config import StrategyConfig


REQUIRED_COLUMNS = {"date", "symbol", "close"}
TRUE_VALUES = {"true", "1", "yes", "y", "t"}
FALSE_VALUES = {"false", "0", "no", "n", "f"}


def _parse_boolean(series: pd.Series, column: str) -> pd.Series:
    def parse(value: Any) -> bool:
        if isinstance(value, (bool, np.bool_)):
            return bool(value)
        if isinstance(value, (int, np.integer)) and value in (0, 1):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in TRUE_VALUES:
                return True
            if normalized in FALSE_VALUES:
                return False
        raise ValueError(f"{column} contains invalid boolean value: {value!r}")

    return series.map(parse).astype(bool)


def normalize_panel(panel: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(panel, pd.DataFrame) or panel.empty:
        raise ValueError("daily panel must be a non-empty DataFrame")
    missing = sorted(REQUIRED_COLUMNS - set(panel.columns))
    if missing:
        raise ValueError("daily panel missing required columns: " + ", ".join(missing))
    provided_columns = list(panel.attrs.get("provided_columns", panel.columns))
    work = panel.copy()
    work["date"] = pd.to_datetime(work["date"], errors="coerce").dt.normalize()
    work["symbol"] = work["symbol"].astype("string").str.strip().str.upper()
    work["close"] = pd.to_numeric(work["close"], errors="coerce")
    if work["date"].isna().any() or work["symbol"].isna().any() or work["symbol"].eq("").any():
        raise ValueError("date and symbol must not be null")
    if work["date"].dt.dayofweek.ge(5).any():
        raise ValueError("A-share panel contains a weekend date")
    duplicated = work.duplicated(["date", "symbol"], keep=False)
    if duplicated.any():
        sample = work.loc[duplicated, ["date", "symbol"]].iloc[0]
        raise ValueError(f"duplicate daily row: {sample['date'].date()} {sample['symbol']}")
    invalid_close = ~np.isfinite(work["close"].to_numpy(dtype=float)) | ~work["close"].gt(0).to_numpy()
    if invalid_close.any():
        raise ValueError(f"close must be finite and positive; invalid rows={int(invalid_close.sum())}")
    for column, default in (("suspended", False), ("is_st", False), ("tradable", True)):
        if column not in work:
            work[column] = default
        else:
            work[column] = _parse_boolean(work[column], column)
    result = work.sort_values(["date", "symbol"]).reset_index(drop=True)
    result.attrs["provided_columns"] = provided_columns
    return result


def market_dates(panel: pd.DataFrame, calendar: Any = None) -> pd.DatetimeIndex:
    if calendar is None:
        return pd.DatetimeIndex(panel["date"].drop_duplicates().sort_values())
    values = pd.to_datetime(pd.Index(calendar), errors="coerce").normalize()
    if values.isna().any() or (values.dayofweek >= 5).any():
        raise ValueError("calendar contains an invalid or weekend date")
    dates = pd.DatetimeIndex(values.unique()).sort_values()
    outside = pd.DatetimeIndex(panel["date"].unique()).difference(dates)
    if len(outside):
        raise ValueError(f"panel contains {len(outside)} dates absent from the explicit calendar")
    return dates


def build_snapshot(
    panel: pd.DataFrame,
    decision_date: str | pd.Timestamp,
    config: StrategyConfig | None = None,
    calendar: Any = None,
) -> dict[str, Any]:
    cfg = config or StrategyConfig()
    work = normalize_panel(panel)
    return _build_snapshot_normalized(work, decision_date, cfg, market_dates(work, calendar))


def _build_snapshot_normalized(
    work: pd.DataFrame,
    decision_date: str | pd.Timestamp,
    config: StrategyConfig,
    dates: pd.DatetimeIndex | None = None,
    date_frames: dict[pd.Timestamp, pd.DataFrame] | None = None,
    visible_symbols: set[str] | None = None,
) -> dict[str, Any]:
    cfg = config
    decision = pd.Timestamp(decision_date).normalize()
    dates = dates if dates is not None else market_dates(work)
    matches = np.flatnonzero(dates == decision)
    if not len(matches):
        raise ValueError(f"decision date is not in the market calendar: {decision.date()}")
    position = int(matches[0])
    if position < cfg.lookback:
        raise ValueError("insufficient market-session lookback")
    lookback_date = dates[position - cfg.lookback]

    if date_frames is None:
        current = work.loc[work["date"] == decision].set_index("symbol")
        previous = work.loc[work["date"] == lookback_date].set_index("symbol")
    else:
        empty = work.iloc[0:0].set_index("symbol")
        current = date_frames.get(decision, empty).copy()
        previous = date_frames.get(lookback_date, empty).copy()
    all_symbols = sorted(
        visible_symbols
        if visible_symbols is not None
        else work.loc[work["date"] <= decision, "symbol"].unique().tolist()
    )
    common = current.index.intersection(previous.index)
    past = current.loc[common, "close"] / previous.loc[common, "close"] - 1.0
    eligible = (
        current.loc[common, "close"].notna()
        & previous.loc[common, "close"].notna()
        & ~current.loc[common, "suspended"]
        & ~current.loc[common, "is_st"]
        & current.loc[common, "tradable"]
    )
    ranked = pd.DataFrame({"symbol": common.astype(str), "past_return": past.to_numpy(), "eligible": eligible.to_numpy()})
    ranked = ranked.loc[ranked["eligible"] & ranked["past_return"].notna()].copy()
    ranked = ranked.sort_values(["past_return", "symbol"], kind="mergesort").reset_index(drop=True)
    if len(ranked) < cfg.min_universe:
        raise ValueError(f"eligible universe {len(ranked)} is below min_universe {cfg.min_universe}")

    n_long = max(1, int(np.floor(len(ranked) * cfg.long_fraction)))
    n_short = max(1, int(np.floor(len(ranked) * cfg.short_fraction)))
    long_rows = ranked.iloc[:n_long]
    short_rows = ranked.iloc[-n_short:]
    weights = {symbol: 0.5 / n_long for symbol in long_rows["symbol"]}
    weights.update({symbol: -0.5 / n_short for symbol in short_rows["symbol"]})
    ranked["reversal_score"] = -ranked["past_return"]
    exclusions: dict[str, list[str]] = {
        "missing_decision_price": [], "missing_lookback_price": [],
        "suspended": [], "st": [], "non_tradable": [],
    }
    for symbol in all_symbols:
        if symbol not in current.index:
            exclusions["missing_decision_price"].append(symbol)
        elif symbol not in previous.index:
            exclusions["missing_lookback_price"].append(symbol)
        elif bool(current.at[symbol, "suspended"]):
            exclusions["suspended"].append(symbol)
        elif bool(current.at[symbol, "is_st"]):
            exclusions["st"].append(symbol)
        elif not bool(current.at[symbol, "tradable"]):
            exclusions["non_tradable"].append(symbol)
    exclusions = {reason: symbols for reason, symbols in exclusions.items() if symbols}
    return {
        "decision_date": decision.strftime("%Y%m%d"),
        "lookback_date": lookback_date.strftime("%Y%m%d"),
        "universe_size": int(len(ranked)),
        "long_symbols": long_rows["symbol"].tolist(),
        "short_symbols": short_rows["symbol"].tolist(),
        "weights": weights,
        "diagnostics": {
            "input_symbol_count": len(all_symbols),
            "eligible_symbol_count": int(len(ranked)),
            "excluded_symbol_count": len(all_symbols) - len(ranked),
            "exclusions": exclusions,
        },
        "scores": {
            row.symbol: {"past_return": float(row.past_return), "reversal_score": float(row.reversal_score)}
            for row in ranked.itertuples(index=False)
        },
    }
