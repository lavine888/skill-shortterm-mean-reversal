from __future__ import annotations

from typing import Any

import numpy as np

from .contract import compute_run_id
from .daily import _market_block_reason
from .engine import calculate_stats
from .version import SCHEMA_VERSION, SKILL_NAME, SKILL_VERSION


TOP_LEVEL_KEYS = {
    "schema_version", "artifact_type", "accounting_mode", "skill", "skill_version",
    "source", "source_status", "start", "end", "config", "source_context",
    "data_capabilities", "delisting_settlements", "cohorts", "days", "final_positions",
    "metrics", "run_id",
}
DAY_KEYS = {
    "date", "cash", "nav", "daily_return", "long_value", "short_value",
    "gross_exposure", "traded_notional", "cost", "short_fee", "nav_before_entries",
    "long_headroom", "short_headroom", "marks", "attempts",
}
MARK_KEYS = {
    "lot_id", "symbol", "units", "price", "market_value", "entry_date",
    "scheduled_exit_date", "effective_exit_date",
}
ATTEMPT_KEYS = {
    "order_id", "lot_id", "symbol", "kind", "attempt_number", "status",
    "block_reason", "requested_units", "requested_notional", "fill_units",
    "fill_price", "notional", "cost", "observed_price", "suspended", "is_st",
    "tradable", "borrowable", "limit_up", "limit_down",
}
COHORT_KEYS = {
    "cohort_id", "decision_date", "entry_date", "scheduled_exit_date",
    "signal_universe_size", "long_symbols", "short_symbols",
}


def _close(left: Any, right: Any, tolerance: float = 1e-10) -> bool:
    try:
        return bool(np.isclose(float(left), float(right), rtol=tolerance, atol=tolerance))
    except (TypeError, ValueError):
        return False


def _valid_number(value: Any, *, positive: bool = False) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return bool(np.isfinite(number) and (not positive or number > 0))


def validate_daily_result(payload: dict[str, Any]) -> None:
    if set(payload) != TOP_LEVEL_KEYS:
        raise ValueError("unsupported daily NAV result contract")
    if payload["schema_version"] != SCHEMA_VERSION or payload["skill_version"] != SKILL_VERSION:
        raise ValueError("daily NAV result version mismatch")
    if payload["skill"] != SKILL_NAME or payload["artifact_type"] != "daily_nav_backtest":
        raise ValueError("invalid daily NAV artifact identity")
    if payload["accounting_mode"] != "daily_nav":
        raise ValueError("invalid daily NAV accounting mode")
    if payload["source_status"] not in {"synthetic", "experimental", "user_supplied"}:
        raise ValueError("invalid source status")
    if payload["data_capabilities"].get("calendar_source") not in {"panel_date_union", "explicit", "pandadata"}:
        raise ValueError("invalid market calendar source")
    from .validate import _valid_date, _validate_source_context
    _validate_source_context(payload)
    if not _valid_date(payload["start"]) or not _valid_date(payload["end"]) or payload["start"] > payload["end"]:
        raise ValueError("invalid daily NAV result date range")
    if not isinstance(payload["days"], list) or not payload["days"]:
        raise ValueError("daily NAV result has no daily ledger")
    config = payload["config"]
    cost_rate = float(config["cost_rate"])

    expected_entries: dict[str, dict[str, Any]] = {}
    cohort_ids: set[str] = set()
    previous_decision = ""
    for cohort in payload["cohorts"]:
        if set(cohort) != COHORT_KEYS:
            raise ValueError("invalid daily NAV cohort contract")
        if len(cohort["long_symbols"]) != len(set(cohort["long_symbols"])) or len(cohort["short_symbols"]) != len(set(cohort["short_symbols"])) or set(cohort["long_symbols"]) & set(cohort["short_symbols"]):
            raise ValueError("daily NAV cohort groups overlap")
        if not cohort["long_symbols"] or not cohort["short_symbols"]:
            raise ValueError("daily NAV cohort has an empty side")
        if cohort["cohort_id"] in cohort_ids:
            raise ValueError("duplicate daily NAV cohort ID")
        cohort_ids.add(cohort["cohort_id"])
        if cohort["cohort_id"] != cohort["decision_date"] or cohort["decision_date"] <= previous_decision:
            raise ValueError("daily NAV cohorts are not strictly ordered")
        previous_decision = cohort["decision_date"]
        if not all(_valid_date(cohort[key]) for key in ("decision_date", "entry_date", "scheduled_exit_date")) or not cohort["decision_date"] < cohort["entry_date"] < cohort["scheduled_exit_date"]:
            raise ValueError("daily NAV cohort dates are invalid")
        if not isinstance(cohort["signal_universe_size"], int) or cohort["signal_universe_size"] < len(cohort["long_symbols"]) + len(cohort["short_symbols"]):
            raise ValueError("daily NAV cohort universe size is invalid")
        for symbol in cohort["long_symbols"]:
            expected_entries[f"{cohort['cohort_id']}:{symbol}"] = {"cohort": cohort, "symbol": symbol, "direction": 1}
        for symbol in cohort["short_symbols"]:
            expected_entries[f"{cohort['cohort_id']}:{symbol}"] = {"cohort": cohort, "symbol": symbol, "direction": -1}

    positions: dict[str, dict[str, Any]] = {}
    entry_attempted: set[str] = set()
    exit_attempts: dict[str, int] = {}
    order_ids: set[str] = set()
    cash = 1.0
    previous_nav = 1.0
    previous_date = ""
    filled_entries = blocked_entries = filled_exits = blocked_exits = settlement_exits = 0
    total_cost = 0.0
    total_short_fee = 0.0
    daily_returns: list[float] = []

    for day_index, day in enumerate(payload["days"]):
        if set(day) != DAY_KEYS:
            raise ValueError(f"invalid daily NAV day contract at index {day_index}")
        date = day["date"]
        if not isinstance(date, str) or len(date) != 8 or date <= previous_date:
            raise ValueError("daily NAV dates are not strictly increasing")
        previous_date = date
        attempts = day["attempts"]
        if any(set(attempt) != ATTEMPT_KEYS for attempt in attempts):
            raise ValueError(f"invalid order attempt contract on {date}")
        for attempt in attempts:
            if not all(isinstance(attempt[key], bool) for key in ("suspended", "is_st", "tradable", "borrowable")):
                raise ValueError(f"invalid observed trading flags on {date}")
            for key in ("observed_price", "limit_up", "limit_down"):
                if attempt[key] is not None and not _valid_number(attempt[key], positive=True):
                    raise ValueError(f"invalid observed {key} on {date}")
        kinds = [attempt["kind"] for attempt in attempts]
        if not set(kinds).issubset({"entry", "exit", "settlement"}):
            raise ValueError(f"invalid order kind on {date}")
        if "entry" in kinds and (set(kinds) - {"entry"}) and max(i for i, kind in enumerate(kinds) if kind in ("exit", "settlement")) > min(i for i, kind in enumerate(kinds) if kind == "entry"):
            raise ValueError(f"exit and settlement attempts must precede entries on {date}")

        settlement_attempts = [item for item in attempts if item["kind"] == "settlement"]
        settlements = payload.get("delisting_settlements", {})
        day_notional = 0.0
        day_cost = 0.0
        for attempt in settlement_attempts:
            lot_id = attempt["lot_id"]
            if lot_id not in positions or attempt["symbol"] != positions[lot_id]["symbol"]:
                raise ValueError(f"settlement references an unknown lot on {date}")
            expected = settlements.get(attempt["symbol"])
            if expected is None:
                raise ValueError(f"settlement has no recorded delisting value on {date}")
            if attempt["order_id"] in order_ids:
                raise ValueError("duplicate order ID")
            order_ids.add(attempt["order_id"])
            exit_attempts[lot_id] = exit_attempts.get(lot_id, 0) + 1
            if attempt["attempt_number"] != exit_attempts[lot_id]:
                raise ValueError(f"invalid settlement attempt sequence for {lot_id}")
            expected_units = -positions[lot_id]["units"]
            if not _close(attempt["requested_units"], expected_units):
                raise ValueError(f"settlement units do not close lot {lot_id}")
            if attempt["status"] != "filled" or attempt["block_reason"] is not None:
                raise ValueError(f"settlement was not filled for {lot_id}")
            if attempt["fill_price"] is None or not _close(attempt["fill_price"], float(expected["price"])):
                raise ValueError(f"settlement price is inconsistent for {lot_id}")
            expected_notional = abs(expected_units * float(attempt["fill_price"]))
            expected_cost = cost_rate * expected_notional
            if not _close(attempt["fill_units"], expected_units) or not _close(attempt["notional"], expected_notional) or not _close(attempt["cost"], expected_cost):
                raise ValueError(f"settlement accounting is inconsistent for {lot_id}")
            cash -= expected_units * float(attempt["fill_price"]) + expected_cost
            day_notional += expected_notional
            day_cost += expected_cost
            del positions[lot_id]
            settlement_exits += 1

        due = {lot_id for lot_id, lot in positions.items() if date >= lot["effective_exit_date"]}
        observed_exits = {attempt["lot_id"] for attempt in attempts if attempt["kind"] == "exit"}
        if observed_exits != due:
            raise ValueError(f"pending exits were not attempted exactly once on {date}")

        for attempt in (item for item in attempts if item["kind"] == "exit"):
            lot_id = attempt["lot_id"]
            if lot_id not in positions or attempt["symbol"] != positions[lot_id]["symbol"]:
                raise ValueError(f"exit references an unknown lot on {date}")
            if attempt["order_id"] in order_ids:
                raise ValueError("duplicate order ID")
            order_ids.add(attempt["order_id"])
            exit_attempts[lot_id] = exit_attempts.get(lot_id, 0) + 1
            if attempt["attempt_number"] != exit_attempts[lot_id]:
                raise ValueError(f"invalid exit attempt sequence for {lot_id}")
            expected_units = -positions[lot_id]["units"]
            if not _valid_number(attempt["observed_price"], positive=True):
                raise ValueError(f"exit is missing an observed price for {lot_id}")
            if not _close(attempt["requested_units"], expected_units):
                raise ValueError(f"exit units do not close lot {lot_id}")
            reason = _market_block_reason(
                expected_units, attempt["observed_price"], attempt["suspended"],
                attempt["is_st"], attempt["tradable"], attempt["limit_up"],
                attempt["limit_down"], block_st=False,
            )
            expected_request = abs(expected_units * float(attempt["observed_price"]))
            if not _close(attempt["requested_notional"], expected_request):
                raise ValueError(f"exit requested notional is inconsistent for {lot_id}")
            if attempt["status"] == "filled":
                if reason is not None or attempt["block_reason"] is not None:
                    raise ValueError(f"ineligible exit was filled for {lot_id}")
                expected_notional = abs(expected_units * float(attempt["fill_price"]))
                if not _close(attempt["fill_units"], expected_units) or not _close(attempt["fill_price"], attempt["observed_price"]) or not _close(attempt["notional"], expected_notional):
                    raise ValueError(f"filled exit accounting is inconsistent for {lot_id}")
                expected_cost = cost_rate * expected_notional
                if not _close(attempt["cost"], expected_cost):
                    raise ValueError(f"filled exit cost is inconsistent for {lot_id}")
                cash -= expected_units * float(attempt["fill_price"]) + expected_cost
                day_notional += expected_notional
                day_cost += expected_cost
                del positions[lot_id]
                filled_exits += 1
            elif attempt["status"] == "blocked":
                if reason is None or attempt["block_reason"] != reason:
                    raise ValueError(f"exit block reason is inconsistent for {lot_id}")
                if attempt["fill_price"] is not None or not _close(attempt["fill_units"], 0) or not _close(attempt["notional"], 0) or not _close(attempt["cost"], 0):
                    raise ValueError(f"blocked exit changed accounting for {lot_id}")
                blocked_exits += 1
            else:
                raise ValueError(f"invalid exit status for {lot_id}")

        end_marks = {mark["lot_id"]: mark for mark in day["marks"]}
        if any(set(mark) != MARK_KEYS for mark in day["marks"]):
            raise ValueError(f"invalid position mark contract on {date}")
        if len(end_marks) != len(day["marks"]):
            raise ValueError(f"duplicate position mark on {date}")
        pre_entry_value = 0.0
        long_before = short_before = 0.0
        for lot_id, lot in positions.items():
            mark = end_marks.get(lot_id)
            if mark is None:
                raise ValueError(f"open lot {lot_id} is missing a mark on {date}")
            value = lot["units"] * float(mark["price"])
            pre_entry_value += value
            long_before += max(value, 0.0)
            short_before += max(-value, 0.0)
        nav_before_entries = cash + pre_entry_value
        expected_long_headroom = max(0.0, 0.5 * nav_before_entries - long_before)
        expected_short_headroom = max(0.0, 0.5 * nav_before_entries - short_before)
        if not _close(day["nav_before_entries"], nav_before_entries) or not _close(day["long_headroom"], expected_long_headroom) or not _close(day["short_headroom"], expected_short_headroom):
            raise ValueError(f"daily NAV entry budget is inconsistent on {date}")

        todays_expected = {
            lot_id: spec for lot_id, spec in expected_entries.items()
            if spec["cohort"]["entry_date"] == date
        }
        observed_entries = {attempt["lot_id"] for attempt in attempts if attempt["kind"] == "entry"}
        if observed_entries != set(todays_expected):
            raise ValueError(f"entry attempts do not match the scheduled cohort on {date}")
        for attempt in (item for item in attempts if item["kind"] == "entry"):
            lot_id = attempt["lot_id"]
            spec = todays_expected[lot_id]
            cohort = spec["cohort"]
            if attempt["symbol"] != spec["symbol"]:
                raise ValueError(f"entry symbol is inconsistent for {lot_id}")
            if lot_id in entry_attempted or attempt["attempt_number"] != 1:
                raise ValueError(f"entry was attempted more than once for {lot_id}")
            entry_attempted.add(lot_id)
            if attempt["order_id"] in order_ids:
                raise ValueError("duplicate order ID")
            order_ids.add(attempt["order_id"])
            side_count = len(cohort["long_symbols"] if spec["direction"] > 0 else cohort["short_symbols"])
            expected_request = (expected_long_headroom if spec["direction"] > 0 else expected_short_headroom) / side_count
            if not _close(attempt["requested_notional"], expected_request):
                raise ValueError(f"entry budget is inconsistent for {lot_id}")
            existing = any(lot["symbol"] == attempt["symbol"] for lot in positions.values())
            if existing:
                reason = "existing_position"
            elif expected_request <= 0:
                reason = "no_side_headroom"
            else:
                reason = _market_block_reason(
                    attempt["requested_units"] or 0.0, attempt["observed_price"],
                    attempt["suspended"], attempt["is_st"], attempt["tradable"],
                    attempt["limit_up"], attempt["limit_down"], block_st=True,
                    borrowable=attempt["borrowable"],
                )
            if expected_request > 0 and attempt["observed_price"] is not None:
                expected_units = spec["direction"] * expected_request / float(attempt["observed_price"])
                if not _close(attempt["requested_units"], expected_units):
                    raise ValueError(f"entry requested units are inconsistent for {lot_id}")
            if attempt["block_reason"] == "delisting_before_exit":
                reason = "delisting_before_exit"
            if attempt["status"] == "filled":
                if reason is not None or attempt["block_reason"] is not None:
                    raise ValueError(f"ineligible entry was filled for {lot_id}")
                mark = end_marks.get(lot_id)
                if mark is None:
                    raise ValueError(f"filled entry is missing its mark for {lot_id}")
                expected_notional = abs(float(attempt["requested_units"]) * float(attempt["fill_price"]))
                expected_cost = cost_rate * expected_notional
                if not _close(attempt["fill_units"], attempt["requested_units"]) or not _close(attempt["fill_price"], attempt["observed_price"]) or not _close(attempt["notional"], expected_notional) or not _close(attempt["cost"], expected_cost):
                    raise ValueError(f"filled entry accounting is inconsistent for {lot_id}")
                cash -= float(attempt["fill_units"]) * float(attempt["fill_price"]) + expected_cost
                day_notional += expected_notional
                day_cost += expected_cost
                positions[lot_id] = {
                    "symbol": attempt["symbol"], "units": float(attempt["fill_units"]),
                    "effective_exit_date": mark["effective_exit_date"],
                }
                filled_entries += 1
            elif attempt["status"] == "blocked":
                if reason is None or attempt["block_reason"] != reason:
                    raise ValueError(f"entry block reason is inconsistent for {lot_id}")
                if attempt["fill_price"] is not None or not _close(attempt["fill_units"], 0) or not _close(attempt["notional"], 0) or not _close(attempt["cost"], 0):
                    raise ValueError(f"blocked entry changed accounting for {lot_id}")
                blocked_entries += 1
            else:
                raise ValueError(f"invalid entry status for {lot_id}")

        if set(end_marks) != set(positions):
            raise ValueError(f"daily marks do not match open positions on {date}")
        long_value = short_value = marked_total = 0.0
        for lot_id, lot in positions.items():
            mark = end_marks[lot_id]
            if mark["symbol"] != lot["symbol"] or not _valid_number(mark["price"], positive=True) or not _close(mark["units"], lot["units"]):
                raise ValueError(f"invalid position mark for {lot_id}")
            market_value = lot["units"] * float(mark["price"])
            if not _close(mark["market_value"], market_value):
                raise ValueError(f"position market value is inconsistent for {lot_id}")
            marked_total += market_value
            long_value += max(market_value, 0.0)
            short_value += max(-market_value, 0.0)
        short_fee = float(config["short_fee_rate"]) / 252.0 * short_value
        cash -= short_fee
        nav = cash + marked_total
        daily_return = nav / previous_nav - 1.0
        if not _close(day["cash"], cash) or not _close(day["nav"], nav) or not _close(day["daily_return"], daily_return):
            raise ValueError(f"daily cash or NAV is inconsistent on {date}")
        if not _close(day["long_value"], long_value) or not _close(day["short_value"], short_value) or not _close(day["gross_exposure"], (long_value + short_value) / nav):
            raise ValueError(f"daily exposure is inconsistent on {date}")
        if not _close(day["traded_notional"], day_notional) or not _close(day["cost"], day_cost):
            raise ValueError(f"daily trading accounting is inconsistent on {date}")
        if not _close(day["short_fee"], short_fee):
            raise ValueError(f"daily short fee is inconsistent on {date}")
        total_cost += day_cost
        total_short_fee += short_fee
        daily_returns.append(daily_return)
        previous_nav = nav

    if entry_attempted != set(expected_entries):
        raise ValueError("not every scheduled entry was attempted")
    if payload["final_positions"] != payload["days"][-1]["marks"]:
        raise ValueError("final positions do not match the final daily ledger")
    metrics = payload["metrics"]
    stats = calculate_stats(daily_returns, 1)
    stats["total_return"] = float(payload["days"][-1]["nav"] - 1.0)
    for key, value in stats.items():
        if value is None:
            if metrics[key] is not None:
                raise ValueError(f"daily metric {key} is inconsistent")
        elif not _close(metrics[key], value):
            raise ValueError(f"daily metric {key} is inconsistent")
    expected_counts = {
        "total_cost": total_cost,
        "total_short_fee": total_short_fee,
        "filled_entries": filled_entries,
        "blocked_entries": blocked_entries,
        "filled_exits": filled_exits,
        "blocked_exit_attempts": blocked_exits,
        "delisting_settlement_exits": settlement_exits,
        "open_position_count": len(positions),
        "unresolved_exit_count": sum(payload["days"][-1]["date"] >= lot["effective_exit_date"] for lot in positions.values()),
    }
    if set(metrics) != set(stats) | set(expected_counts):
        raise ValueError("invalid daily NAV metrics contract")
    for key, value in expected_counts.items():
        if not _close(metrics[key], value):
            raise ValueError(f"daily metric {key} is inconsistent")
    if payload["run_id"] != compute_run_id(payload):
        raise ValueError("run_id does not match canonical payload")
