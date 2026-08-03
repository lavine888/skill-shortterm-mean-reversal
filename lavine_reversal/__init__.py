from .config import StrategyConfig
from .engine import run_backtest
from .factor import build_snapshot
from .version import SKILL_VERSION

__all__ = ["StrategyConfig", "build_snapshot", "run_backtest"]
__version__ = SKILL_VERSION
