from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from .config import StrategyConfig
from .contract import compute_run_id
from .engine import (
    _boolean_panel,
    _delisting_settlements,
    _limit_block_reason,
    _numeric_panel,
    _optional_float,
    _symbol_settlement_prices,
    build_source_context,
    calculate_stats,
)
from .factor import _build_snapshot_normalized, market_dates, normalize_panel
from .version import SCHEMA_VERSION, SKILL_NAME, SKILL_VERSION


@dataclass
class PositionLot:
    lot_id: str
    symbol: str
    units: float
    entry_date: pd.Timestamp
    entry_price: float
    scheduled_exit_date: pd.Timestamp
    effective_exit_date: pd.Timestamp
    exit_attempts: int = 0


def _market_block_reason(
    units: float,
    price: float | None,
    suspended: bool,
    is_st: bool,
    tradable: bool,
    limit_up: float | None,
    limit_down: float | None,
    block_st: bool = True,
    borrowable: bool = True,
) -> str | None:
    if price is None:
        return "missing_price"
    if suspended:
        return "suspended"
    if block_st and is_st:
        return "st"
    if not tradable:
        return "non_tradable"
    if block_st and units < 0 and not borrowable:
        return "not_borrowable"
    return _limit_block_reason(units, "entry", price, limit_up, limit_down)


def _attempt(
    *, order_id: str, lot_id: str, symbol: str, kind: str,
    attempt_number: int, requested_units: float | None, requested_notional: float,
    price: float | None, suspended: bool, is_st: bool, tradable: bool,
    limit_up: float | None, limit_down: float | None,
    cost_rate: float, borrowable: bool = True, extra_reason: str | None = None,
) -> dict[str, Any]:
    reason = extra_reason or _market_block_reason(
        requested_units or 0.0, price, suspended, is_st, tradable,
        limit_up, limit_down, block_st=kind == "entry", borrowable=borrowable,
    )
    filled = reason is None and requested_units is not None and requested_notional > 0
    fill_units = float(requested_units) if filled else 0.0
    notional = abs(fill_units * float(price)) if filled else 0.0
    cost = cost_rate * notional
    return {
        "order_id": order_id,
        "lot_id": lot_id,
        "symbol": symbol,
        "kind": kind,
        "attempt_number": attempt_number,
        "status": "filled" if filled else "blocked",
        "block_reason": None if filled else (reason or "no_side_headroom"),
        "requested_units": float(requested_units) if requested_units is not None else None,
        "requested_notional": float(requested_notional),
        "fill_units": fill_units,
        "fill_price": float(price) if filled else None,
        "notional": float(notional),
        "cost": float(cost),
        "observed_price": price,
        "suspended": suspended,
        "is_st": is_st,
        "tradable": tradable,
        "borrowable": borrowable,
        "limit_up": limit_up,
        "limit_down": limit_down,
    }


def run_daily_backtest(
    panel: pd.DataFrame,
    start: str,
    end: str,
    config: StrategyConfig | None = None,
    source: str = "file",
    calendar: Any = None,
    calendar_source: str | None = None,
) -> dict[str, Any]:
    cfg = config or StrategyConfig()
    provided_columns = set(panel.attrs.get("provided_columns", panel.columns))
    work = normalize_panel(panel)
    start_date = pd.to_datetime(start, errors="raise").normalize()
    end_date = pd.to_datetime(end, errors="raise").normalize()
    if start_date > end_date:
        raise ValueError("start must be on or before end")
    dates = market_dates(work, calendar)
    close = work.pivot(index="date", columns="symbol", values="close").reindex(dates)
    suspended = _boolean_panel(work, dates, "suspended", True)
    is_st = _boolean_panel(work, dates, "is_st", False)
    tradable = _boolean_panel(work, dates, "tradable", False)
    borrowable = _boolean_panel(work, dates, "borrowable", True)
    limit_up = _numeric_panel(work, dates, close.columns, "limit_up")
    limit_down = _numeric_panel(work, dates, close.columns, "limit_down")
    settlement_prices = _symbol_settlement_prices(work)
    date_frames = {
        pd.Timestamp(date): frame.set_index("symbol")
        for date, frame in work.groupby("date", sort=False)
    }

    first = max(cfg.lookback, int(np.searchsorted(dates.values, start_date.to_datetime64(), side="left")))
    last = int(np.searchsorted(dates.values, end_date.to_datetime64(), side="right")) - 1
    cohorts: list[dict[str, Any]] = []
    entries: dict[pd.Timestamp, list[dict[str, Any]]] = {}
    visible_symbols: set[str] = set()
    visible_cursor = 0
    for position in range(first, last + 1, cfg.rebalance_every):
        entry_position = position + cfg.execution_lag
        exit_position = entry_position + cfg.hold_days
        if exit_position >= len(dates) or dates[exit_position] > end_date:
            break
        decision, entry, exit_date = dates[position], dates[entry_position], dates[exit_position]
        while visible_cursor <= position:
            visible_symbols.update(date_frames.get(dates[visible_cursor], pd.DataFrame()).index.astype(str))
            visible_cursor += 1
        snapshot = _build_snapshot_normalized(
            work, decision, cfg, dates, date_frames=date_frames,
            visible_symbols=visible_symbols,
        )
        cohort = {
            "cohort_id": decision.strftime("%Y%m%d"),
            "decision_date": decision.strftime("%Y%m%d"),
            "entry_date": entry.strftime("%Y%m%d"),
            "scheduled_exit_date": exit_date.strftime("%Y%m%d"),
            "signal_universe_size": snapshot["universe_size"],
            "long_symbols": snapshot["long_symbols"],
            "short_symbols": snapshot["short_symbols"],
        }
        cohorts.append(cohort)
        entries.setdefault(entry, []).append(cohort)
    if not cohorts:
        raise ValueError("backtest range contains no complete decision-entry-holding cohort")

    delisting_dates: dict[str, pd.Timestamp] = {}
    final_dates: dict[str, pd.Timestamp] = {}
    if "de_listed_date" in work:
        for symbol, own in work.groupby("symbol"):
            values = pd.to_datetime(own["de_listed_date"], format="mixed", errors="coerce").dropna()
            if not values.empty:
                delisting = values.iloc[0].normalize()
                available = own.loc[own["date"] < delisting, "date"]
                if not available.empty:
                    delisting_dates[symbol] = delisting
                    final_dates[symbol] = available.max()

    positions: dict[str, PositionLot] = {}
    cash = 1.0
    previous_nav = 1.0
    days: list[dict[str, Any]] = []
    blocked_entries = 0
    blocked_exits = 0
    filled_entries = 0
    filled_exits = 0
    total_cost = 0.0
    total_short_fee = 0.0
    settlement_exits = 0
    first_entry = min(entries)
    for date in dates[(dates >= first_entry) & (dates <= end_date)]:
        prices = close.loc[date]
        day_suspended = suspended.loc[date]
        day_is_st = is_st.loc[date]
        day_tradable = tradable.loc[date]
        day_borrowable = borrowable.loc[date]
        day_limit_up = limit_up.loc[date]
        day_limit_down = limit_down.loc[date]
        attempts: list[dict[str, Any]] = []
        traded_notional = 0.0
        day_cost = 0.0

        for lot_id in list(positions):
            lot = positions[lot_id]
            settlement = settlement_prices.get(lot.symbol)
            delisting = delisting_dates.get(lot.symbol)
            if settlement is None or delisting is None or date < delisting:
                continue
            lot.exit_attempts += 1
            price = float(settlement)
            requested_units = -lot.units
            notional = abs(requested_units * price)
            cost = cfg.cost_rate * notional
            attempt = {
                "order_id": f"{date:%Y%m%d}:settlement:{lot_id}",
                "lot_id": lot_id, "symbol": lot.symbol, "kind": "settlement",
                "attempt_number": lot.exit_attempts, "status": "filled", "block_reason": None,
                "requested_units": requested_units, "requested_notional": notional,
                "fill_units": requested_units, "fill_price": price, "notional": notional,
                "cost": cost, "observed_price": price,
                "suspended": False, "is_st": False, "tradable": True, "borrowable": True,
                "limit_up": None, "limit_down": None,
            }
            attempts.append(attempt)
            cash -= requested_units * price + cost
            traded_notional += notional
            day_cost += cost
            settlement_exits += 1
            del positions[lot_id]

        for lot in positions.values():
            if _optional_float(prices, lot.symbol) is None:
                if settlement_prices.get(lot.symbol) is not None and delisting_dates.get(lot.symbol) is not None and date >= delisting_dates[lot.symbol]:
                    continue
                delisted = delisting_dates.get(lot.symbol)
                delisted_text = delisted.strftime("%Y-%m-%d") if delisted is not None else "unknown"
                raise ValueError(
                    f"cannot mark open position {lot.lot_id} on {date.date()}: "
                    f"entry={lot.entry_date.date()} scheduled_exit={lot.scheduled_exit_date.date()} "
                    f"effective_exit={lot.effective_exit_date.date()} delisted={delisted_text}"
                )

        due = sorted(
            (lot for lot in positions.values() if date >= lot.effective_exit_date),
            key=lambda lot: (lot.effective_exit_date, lot.lot_id),
        )
        for lot in due:
            lot.exit_attempts += 1
            price = _optional_float(prices, lot.symbol)
            requested_units = -lot.units
            attempt = _attempt(
                order_id=f"{date:%Y%m%d}:exit:{lot.lot_id}", lot_id=lot.lot_id,
                symbol=lot.symbol, kind="exit", attempt_number=lot.exit_attempts,
                requested_units=requested_units,
                requested_notional=abs(requested_units * float(price)), price=price,
                suspended=bool(day_suspended.get(lot.symbol, True)),
                is_st=bool(day_is_st.get(lot.symbol, False)),
                tradable=bool(day_tradable.get(lot.symbol, False)),
                limit_up=_optional_float(day_limit_up, lot.symbol),
                limit_down=_optional_float(day_limit_down, lot.symbol),
                cost_rate=cfg.cost_rate,
            )
            attempts.append(attempt)
            if attempt["status"] == "filled":
                cash -= attempt["fill_units"] * attempt["fill_price"] + attempt["cost"]
                traded_notional += attempt["notional"]
                day_cost += attempt["cost"]
                filled_exits += 1
                del positions[lot.lot_id]
            else:
                blocked_exits += 1

        marked_values = {
            lot_id: lot.units * float(prices[lot.symbol])
            for lot_id, lot in positions.items()
        }
        nav_before_entries = cash + sum(marked_values.values())
        long_value_before = sum(max(value, 0.0) for value in marked_values.values())
        short_value_before = sum(max(-value, 0.0) for value in marked_values.values())
        long_headroom = max(0.0, 0.5 * nav_before_entries - long_value_before)
        short_headroom = max(0.0, 0.5 * nav_before_entries - short_value_before)

        for cohort in entries.get(date, []):
            selected = [(symbol, 1) for symbol in cohort["long_symbols"]]
            selected.extend((symbol, -1) for symbol in cohort["short_symbols"])
            long_each = long_headroom / len(cohort["long_symbols"])
            short_each = short_headroom / len(cohort["short_symbols"])
            open_symbols = {lot.symbol for lot in positions.values()}
            for symbol, direction in selected:
                requested_notional = long_each if direction > 0 else short_each
                price = _optional_float(prices, symbol)
                requested_units = direction * requested_notional / price if price is not None and requested_notional > 0 else None
                lot_id = f"{cohort['cohort_id']}:{symbol}"
                extra_reason = None
                if symbol in open_symbols:
                    extra_reason = "existing_position"
                elif requested_notional <= 0:
                    extra_reason = "no_side_headroom"
                effective_exit = pd.Timestamp(cohort["scheduled_exit_date"])
                if symbol in delisting_dates and settlement_prices.get(symbol) is not None:
                    if date < delisting_dates[symbol] <= effective_exit:
                        effective_exit = delisting_dates[symbol]
                elif cfg.delisting_exit_policy == "last_available_close" and symbol in delisting_dates:
                    if date < delisting_dates[symbol] <= effective_exit:
                        effective_exit = final_dates[symbol]
                        if effective_exit <= date:
                            extra_reason = "delisting_before_exit"
                attempt = _attempt(
                    order_id=f"{date:%Y%m%d}:entry:{lot_id}", lot_id=lot_id,
                    symbol=symbol, kind="entry", attempt_number=1,
                    requested_units=requested_units,
                    requested_notional=requested_notional, price=price,
                    suspended=bool(day_suspended.get(symbol, True)),
                    is_st=bool(day_is_st.get(symbol, False)),
                    tradable=bool(day_tradable.get(symbol, False)),
                    borrowable=bool(day_borrowable.get(symbol, True)),
                    limit_up=_optional_float(day_limit_up, symbol),
                    limit_down=_optional_float(day_limit_down, symbol),
                    cost_rate=cfg.cost_rate, extra_reason=extra_reason,
                )
                attempts.append(attempt)
                if attempt["status"] == "filled":
                    cash -= attempt["fill_units"] * attempt["fill_price"] + attempt["cost"]
                    traded_notional += attempt["notional"]
                    day_cost += attempt["cost"]
                    filled_entries += 1
                    positions[lot_id] = PositionLot(
                        lot_id=lot_id, symbol=symbol, units=attempt["fill_units"],
                        entry_date=date, entry_price=attempt["fill_price"],
                        scheduled_exit_date=pd.Timestamp(cohort["scheduled_exit_date"]),
                        effective_exit_date=effective_exit,
                    )
                    open_symbols.add(symbol)
                else:
                    blocked_entries += 1

        marks = []
        for lot_id, lot in sorted(positions.items()):
            price = _optional_float(prices, lot.symbol)
            market_value = lot.units * float(price)
            marks.append({
                "lot_id": lot_id, "symbol": lot.symbol, "units": lot.units,
                "price": price, "market_value": market_value,
                "entry_date": lot.entry_date.strftime("%Y%m%d"),
                "scheduled_exit_date": lot.scheduled_exit_date.strftime("%Y%m%d"),
                "effective_exit_date": lot.effective_exit_date.strftime("%Y%m%d"),
            })
        long_value = sum(max(mark["market_value"], 0.0) for mark in marks)
        short_value = sum(max(-mark["market_value"], 0.0) for mark in marks)
        short_fee = float(cfg.short_fee_rate / 252.0 * short_value)
        cash -= short_fee
        nav = cash + sum(mark["market_value"] for mark in marks)
        if nav <= 0:
            raise ValueError(f"portfolio NAV is non-positive on {date.date()}")
        daily_return = nav / previous_nav - 1.0
        total_cost += day_cost
        total_short_fee += short_fee
        days.append({
            "date": date.strftime("%Y%m%d"), "cash": float(cash),
            "nav": float(nav), "daily_return": float(daily_return),
            "long_value": float(long_value), "short_value": float(short_value),
            "gross_exposure": float((long_value + short_value) / nav),
            "traded_notional": float(traded_notional), "cost": float(day_cost),
            "short_fee": short_fee,
            "nav_before_entries": float(nav_before_entries),
            "long_headroom": float(long_headroom), "short_headroom": float(short_headroom),
            "marks": marks, "attempts": attempts,
        })
        previous_nav = nav

    daily_returns = [day["daily_return"] for day in days]
    stats = calculate_stats(daily_returns, 1)
    stats["total_return"] = float(days[-1]["nav"] - 1.0)
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "daily_nav_backtest",
        "accounting_mode": "daily_nav",
        "skill": SKILL_NAME,
        "skill_version": SKILL_VERSION,
        "source": source,
        "source_status": "synthetic" if source == "demo" else ("experimental" if source == "pandadata" else "user_supplied"),
        "start": start_date.strftime("%Y%m%d"),
        "end": end_date.strftime("%Y%m%d"),
        "config": cfg.to_dict(),
        "source_context": build_source_context(work, dates),
        "data_capabilities": {
            "post_adjusted_close": "provider_method" if source == "pandadata" else ("synthetic" if source == "demo" else "declared_by_input_contract"),
            "suspended_flag": "suspended" in provided_columns,
            "st_flag": "is_st" in provided_columns,
            "tradable_flag": "tradable" in provided_columns,
            "borrowable_flag": "borrowable" in provided_columns,
            "delisting_settlement_price": "delisting_settlement_price" in provided_columns,
            "trade_status": "trade_status" in provided_columns,
            "limit_prices": {"limit_up", "limit_down"}.issubset(provided_columns),
            "historical_name": "name" in provided_columns,
            "delisting_date": "de_listed_date" in provided_columns,
            "calendar_source": calendar_source or ("explicit" if calendar is not None else "panel_date_union"),
        },
        "delisting_settlements": _delisting_settlements(work),
        "cohorts": cohorts,
        "days": days,
        "final_positions": days[-1]["marks"],
        "metrics": {
            **stats,
            "total_cost": float(total_cost),
            "total_short_fee": float(total_short_fee),
            "delisting_settlement_exits": settlement_exits,
            "filled_entries": filled_entries,
            "blocked_entries": blocked_entries,
            "filled_exits": filled_exits,
            "blocked_exit_attempts": blocked_exits,
            "open_position_count": len(positions),
            "unresolved_exit_count": sum(date >= lot.effective_exit_date for lot in positions.values()),
        },
    }
    payload["run_id"] = compute_run_id(payload)
    return payload
