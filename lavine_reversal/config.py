from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from numbers import Integral, Real


@dataclass(frozen=True)
class StrategyConfig:
    lookback: int = 5
    long_fraction: float = 0.10
    short_fraction: float = 0.10
    rebalance_every: int = 5
    execution_lag: int = 1
    hold_days: int = 5
    cost_rate: float = 0.001
    min_universe: int = 20
    delisting_exit_policy: str = "error"

    def __post_init__(self) -> None:
        for name in ("lookback", "rebalance_every", "execution_lag", "hold_days", "min_universe"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, Integral) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        for name in ("long_fraction", "short_fraction", "cost_rate"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(float(value)):
                raise ValueError(f"{name} must be finite")
        for name in ("long_fraction", "short_fraction"):
            value = getattr(self, name)
            if not 0 < value < 0.5:
                raise ValueError(f"{name} must be between 0 and 0.5")
        if self.long_fraction + self.short_fraction >= 1:
            raise ValueError("long and short fractions must not overlap")
        if self.cost_rate < 0:
            raise ValueError("cost_rate must be non-negative")
        if self.rebalance_every != self.hold_days:
            raise ValueError("this non-overlapping engine requires rebalance_every == hold_days")
        if self.delisting_exit_policy not in {"error", "last_available_close"}:
            raise ValueError("delisting_exit_policy must be error or last_available_close")

    def to_dict(self) -> dict[str, int | float | str]:
        return asdict(self)
