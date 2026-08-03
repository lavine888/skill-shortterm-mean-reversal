from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

import numpy as np
import pandas as pd

from .config import StrategyConfig
from .contract import compute_run_id
from .factor import _build_snapshot_normalized, market_dates, normalize_panel
from .version import SCHEMA_VERSION, SKILL_NAME, SKILL_VERSION


def _rank_ic(scores: pd.Series, returns: pd.Series) -> float | None:
    common = scores.dropna().index.intersection(returns.dropna().index)
    if len(common) < 3:
        return None
    value = scores.loc[common].rank(method="average").corr(
        returns.loc[common].rank(method="average")
    )
    return None if pd.isna(value) else float(value)


def calculate_stats(period_returns: list[float], hold_days: int) -> dict[str, float | int | None]:
    values = np.asarray(period_returns, dtype=float)
    equity = np.concatenate(([1.0], np.cumprod(1.0 + values)))
    periods_per_year = 252.0 / hold_days
    years = len(values) / periods_per_year
    total = float(equity[-1] - 1.0)
    cagr = float(equity[-1] ** (1.0 / years) - 1.0) if years > 0 and equity[-1] > 0 else -1.0
    volatility = float(values.std(ddof=1) * np.sqrt(periods_per_year)) if len(values) > 1 else 0.0
    standard_deviation = values.std(ddof=1) if len(values) > 1 else 0.0
    sharpe = float(values.mean() / standard_deviation * np.sqrt(periods_per_year)) if standard_deviation > 0 else None
    running_max = np.maximum.accumulate(equity)
    max_drawdown = float(np.min(equity / running_max - 1.0))
    return {
        "periods": len(values), "total_return": total, "cagr": cagr,
        "annual_volatility": volatility, "sharpe": sharpe,
        "max_drawdown": max_drawdown,
    }


def _status_panel(work: pd.DataFrame, dates: pd.DatetimeIndex) -> pd.DataFrame:
    executable = (~work["suspended"]) & (~work["is_st"]) & work["tradable"]
    status = work.assign(_executable=executable).pivot(
        index="date", columns="symbol", values="_executable"
    )
    return status.reindex(dates).fillna(False).astype(bool)


def build_source_context(work: pd.DataFrame, dates: pd.DatetimeIndex) -> dict[str, Any]:
    digest_columns = ["date", "symbol", "close", "suspended", "is_st", "tradable"]
    if "de_listed_date" in work:
        digest_columns.append("de_listed_date")
    digest_frame = work[digest_columns].copy()
    digest_frame["date"] = digest_frame["date"].dt.strftime("%Y%m%d")
    if "de_listed_date" in digest_frame:
        digest_frame["de_listed_date"] = pd.to_datetime(
            digest_frame["de_listed_date"], format="mixed", errors="coerce"
        ).dt.strftime("%Y%m%d").fillna("")
    metadata = json.dumps(digest_columns, separators=(",", ":")).encode("utf-8")
    values = pd.util.hash_pandas_object(digest_frame, index=False).values.tobytes()
    calendar_values = "\n".join(date.strftime("%Y%m%d") for date in dates).encode("ascii")
    return {
        "row_count": len(work),
        "symbol_count": int(work["symbol"].nunique()),
        "first_date": work["date"].min().strftime("%Y%m%d"),
        "last_date": work["date"].max().strftime("%Y%m%d"),
        "panel_sha256": hashlib.sha256(metadata + values).hexdigest(),
        "calendar_sha256": hashlib.sha256(calendar_values).hexdigest(),
        "calendar_sessions": len(dates),
        "provider_context": work.attrs.get("provider_context", {}),
    }


def run_backtest(
    panel: pd.DataFrame,
    start: str,
    end: str,
    config: StrategyConfig | None = None,
    source: str = "file",
    calendar: Any = None,
    evidence_sink: Callable[[pd.DataFrame], None] | None = None,
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
    executable = _status_panel(work, dates)
    date_frames = {
        pd.Timestamp(date): frame.set_index("symbol")
        for date, frame in work.groupby("date", sort=False)
    }
    first = max(cfg.lookback, int(np.searchsorted(dates.values, start_date.to_datetime64(), side="left")))
    last = int(np.searchsorted(dates.values, end_date.to_datetime64(), side="right")) - 1
    if first > last:
        raise ValueError("backtest range has no eligible market dates")

    periods: list[dict[str, Any]] = []
    previous_drifted_weights: dict[str, float] = {}
    last_ending_positions: dict[str, float] = {}
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
        entry_prices, exit_prices = close.loc[entry], close.loc[exit_date]
        entry_status, exit_status = executable.loc[entry], executable.loc[exit_date]
        all_forward = exit_prices / entry_prices - 1.0
        scores = pd.Series({symbol: values["reversal_score"] for symbol, values in snapshot["scores"].items()})
        rank_sample = scores.dropna().index.intersection(all_forward.dropna().index)
        rank_ic = _rank_ic(scores, all_forward)

        if evidence_sink is not None:
            long_symbols = set(snapshot["long_symbols"])
            short_symbols = set(snapshot["short_symbols"])
            evidence_frame = pd.DataFrame([
                {
                    "decision_date": decision.strftime("%Y%m%d"),
                    "lookback_date": snapshot["lookback_date"],
                    "entry_date": entry.strftime("%Y%m%d"),
                    "exit_date": exit_date.strftime("%Y%m%d"),
                    "symbol": symbol,
                    "past_return": values["past_return"],
                    "reversal_score": values["reversal_score"],
                    "selected_side": "long" if symbol in long_symbols else ("short" if symbol in short_symbols else "none"),
                    "target_weight": snapshot["weights"].get(symbol, 0.0),
                    "entry_price": float(entry_prices[symbol]) if symbol in entry_prices.index and pd.notna(entry_prices[symbol]) else None,
                    "exit_price": float(exit_prices[symbol]) if symbol in exit_prices.index and pd.notna(exit_prices[symbol]) else None,
                    "forward_return": float(all_forward[symbol]) if symbol in all_forward.index and pd.notna(all_forward[symbol]) else None,
                }
                for symbol, values in snapshot["scores"].items()
            ]).sort_values("symbol", kind="mergesort").reset_index(drop=True)
            evidence_sink(evidence_frame)

        target_weights = snapshot["weights"]
        executed_weights: dict[str, float] = {}
        evidence: dict[str, dict[str, Any]] = {}
        unresolved_exit: list[str] = []
        forced_exit_symbols: set[str] = set()
        for symbol, target_weight in target_weights.items():
            can_enter = symbol in entry_prices.index and pd.notna(entry_prices[symbol]) and bool(entry_status.get(symbol, False))
            executed_weight = float(target_weight) if can_enter else 0.0
            can_exit = (
                can_enter and symbol in exit_prices.index and pd.notna(exit_prices[symbol])
                and bool(exit_status.get(symbol, False))
            )
            actual_exit_date = exit_date if can_exit else None
            actual_exit_price = float(exit_prices[symbol]) if can_exit else None
            exit_label = "filled" if can_exit else ("not_applicable" if not can_enter else "unresolved")
            if can_enter and not can_exit and cfg.delisting_exit_policy == "last_available_close" and "de_listed_date" in work:
                own = work.loc[work["symbol"] == symbol]
                delisted_values = pd.to_datetime(own["de_listed_date"], format="mixed", errors="coerce").dropna()
                delisted_date = delisted_values.iloc[0] if not delisted_values.empty else None
                candidates = own.loc[(own["date"] >= entry) & (own["date"] < delisted_date)] if delisted_date is not None and entry < delisted_date <= exit_date else own.iloc[0:0]
                if not candidates.empty:
                    forced = candidates.sort_values("date").iloc[-1]
                    forced_executable = not bool(forced["suspended"]) and not bool(forced["is_st"]) and bool(forced["tradable"])
                    if forced_executable:
                        actual_exit_date = pd.Timestamp(forced["date"])
                        actual_exit_price = float(forced["close"])
                        can_exit = True
                        exit_label = "forced_delisting_exit"
                        forced_exit_symbols.add(symbol)
            if can_enter and not can_exit:
                unresolved_exit.append(symbol)
            forward_return = actual_exit_price / float(entry_prices[symbol]) - 1.0 if can_exit else None
            if can_enter:
                executed_weights[symbol] = executed_weight
            evidence[symbol] = {
                **snapshot["scores"][symbol],
                "target_weight": float(target_weight),
                "executed_weight": executed_weight,
                "entry_price": float(entry_prices[symbol]) if can_enter else None,
                "exit_price": actual_exit_price,
                "forward_return": forward_return,
                "entry_status": "filled" if can_enter else "unfilled",
                "exit_status": exit_label,
                "actual_exit_date": actual_exit_date.strftime("%Y%m%d") if actual_exit_date is not None else None,
            }
        if unresolved_exit:
            sample = ", ".join(sorted(unresolved_exit)[:10])
            raise ValueError(
                f"cannot value or exit {len(unresolved_exit)} executed positions on {exit_date.date()}: {sample}"
            )

        traded_symbols = sorted(set(executed_weights) | set(previous_drifted_weights))
        traded_notional = float(sum(
            abs(executed_weights.get(symbol, 0.0) - previous_drifted_weights.get(symbol, 0.0))
            for symbol in traded_symbols
        ))
        gross_return = float(sum(
            weight * float(evidence[symbol]["forward_return"])
            for symbol, weight in executed_weights.items()
        ))
        forced_exit_notional = float(sum(
            abs(executed_weights[symbol] * (1.0 + float(evidence[symbol]["forward_return"])))
            for symbol in forced_exit_symbols
        ))
        traded_notional += forced_exit_notional
        cost = float(cfg.cost_rate * traded_notional)
        net_return = gross_return - cost
        ending_nav = 1.0 + net_return
        if ending_nav <= 0:
            raise ValueError(f"portfolio NAV is non-positive on {exit_date.date()}")
        last_ending_positions = {
            symbol: weight * (1.0 + float(evidence[symbol]["forward_return"]))
            for symbol, weight in executed_weights.items()
            if symbol not in forced_exit_symbols
        }
        previous_drifted_weights = {
            symbol: value / ending_nav for symbol, value in last_ending_positions.items()
        }
        priced = sum(item["forward_return"] is not None for item in evidence.values())
        periods.append({
            "decision_date": decision.strftime("%Y%m%d"),
            "entry_date": entry.strftime("%Y%m%d"),
            "exit_date": exit_date.strftime("%Y%m%d"),
            "lookback_date": snapshot["lookback_date"],
            "signal_universe_size": snapshot["universe_size"],
            "long_symbols": snapshot["long_symbols"],
            "short_symbols": snapshot["short_symbols"],
            "selected_evidence": evidence,
            "forward_coverage": priced / len(evidence),
            "rank_ic_sample_size": len(rank_sample),
            "rank_ic_coverage": len(rank_sample) / len(scores),
            "rank_ic": rank_ic,
            "forced_delisting_exit_count": len(forced_exit_symbols),
            "gross_return": gross_return,
            "traded_notional": traded_notional,
            "cost": cost,
            "net_return": net_return,
        })

    if not periods:
        raise ValueError("backtest range contains no complete decision-entry-holding period")
    liquidation_notional = float(sum(abs(value) for value in last_ending_positions.values()))
    liquidation_cost = float(cfg.cost_rate * liquidation_notional)
    periods[-1]["traded_notional"] += liquidation_notional
    periods[-1]["cost"] += liquidation_cost
    periods[-1]["net_return"] -= liquidation_cost

    net_returns = [period["net_return"] for period in periods]
    rank_ics = [period["rank_ic"] for period in periods if period["rank_ic"] is not None]
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
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
            "delisting_date": "de_listed_date" in provided_columns,
            "calendar_source": "explicit" if calendar is not None else "panel_date_union",
        },
        "metrics": {
            **calculate_stats(net_returns, cfg.hold_days),
            "mean_rank_ic": float(np.mean(rank_ics)) if rank_ics else None,
            "average_traded_notional": float(np.mean([p["traded_notional"] for p in periods])),
            "average_forward_coverage": float(np.mean([p["forward_coverage"] for p in periods])),
            "average_rank_ic_coverage": float(np.mean([p["rank_ic_coverage"] for p in periods])),
            "forced_delisting_exits": int(sum(p["forced_delisting_exit_count"] for p in periods)),
        },
        "periods": periods,
        "limitations": [
            "long-short factor research; A-share cash-equity shorting is not generally executable",
            "close-to-close model omits limit-order queues, borrow availability and intraday slippage",
            "missing or non-tradable entries remain cash; unresolved executed exits fail the run",
            f"delisting exit policy: {cfg.delisting_exit_policy}",
            "market sessions are inferred from the panel date union unless externally audited",
        ],
    }
    payload["run_id"] = compute_run_id(payload)
    return payload
