from __future__ import annotations

import hashlib
import importlib.metadata
import os
import math
import time
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from .factor import normalize_panel


class FileProvider:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self, start: str, end: str, symbols: list[str] | None = None) -> pd.DataFrame:
        if not self.path.exists():
            raise FileNotFoundError(self.path)
        frame = pd.read_parquet(self.path) if self.path.suffix.lower() in {".parquet", ".pq"} else pd.read_csv(self.path)
        frame = normalize_panel(frame)
        mask = frame["date"].between(pd.Timestamp(start), pd.Timestamp(end))
        if symbols:
            mask &= frame["symbol"].isin([symbol.upper() for symbol in symbols])
        result = frame.loc[mask].reset_index(drop=True)
        result.attrs.update(frame.attrs)
        result.attrs["provider_context"] = {"provider": "file", "input_name": self.path.name}
        return result


class DemoProvider:
    def __init__(self, n_symbols: int = 100, seed: int = 5801):
        self.n_symbols = n_symbols
        self.seed = seed

    def load(self, start: str, end: str, symbols: list[str] | None = None) -> pd.DataFrame:
        rng = np.random.default_rng(self.seed)
        dates = pd.bdate_range(start, end)
        names = [f"{index:06d}.SZ" for index in range(1, self.n_symbols + 1)]
        returns = np.zeros((len(dates), len(names)))
        shocks = rng.normal(0.0002, 0.018, size=returns.shape)
        for index in range(len(dates)):
            reversal = -0.10 * returns[max(0, index - 5):index].sum(axis=0) if index else 0.0
            returns[index] = shocks[index] + reversal
        prices = 20.0 * np.cumprod(1.0 + returns, axis=0)
        frame = (
            pd.DataFrame(prices, index=dates, columns=names)
            .rename_axis(index="date", columns="symbol")
            .stack()
            .rename("close")
            .reset_index()
        )
        if symbols:
            frame = frame.loc[frame["symbol"].isin([symbol.upper() for symbol in symbols])]
        result = normalize_panel(frame)
        result.attrs["provided_columns"] = ["date", "symbol", "close"]
        result.attrs["provider_context"] = {
            "provider": "demo", "seed": self.seed, "configured_symbol_count": self.n_symbols,
        }
        return result


class PandaDataProvider:
    def __init__(self, request_interval: float = 1.0, retries: int = 5):
        if not math.isfinite(request_interval) or request_interval < 0:
            raise ValueError("request_interval must be finite and non-negative")
        if isinstance(retries, bool) or not isinstance(retries, int) or retries < 1:
            raise ValueError("retries must be a positive integer")
        self.request_interval = request_interval
        self.retries = retries
        self._last_request = 0.0
        try:
            import panda_data
        except ImportError as exc:
            raise RuntimeError("panda_data is not installed") from exc
        username = os.getenv("PANDA_DATA_USERNAME", "")
        password = os.getenv("PANDA_DATA_PASSWORD", "")
        if not username or not password:
            raise RuntimeError("PANDA_DATA_USERNAME and PANDA_DATA_PASSWORD are required")
        kwargs = {"username": username, "password": password}
        base_url = os.getenv("PANDA_DATA_BASE_URL", "")
        if base_url:
            kwargs["base_url"] = base_url
        panda_data.init_token(**kwargs)
        self.api = panda_data

    def _call(self, name: str, **kwargs) -> pd.DataFrame:
        for attempt in range(self.retries):
            delay = self.request_interval - (time.monotonic() - self._last_request)
            if delay > 0:
                time.sleep(delay)
            self._last_request = time.monotonic()
            try:
                api = getattr(self.api, name, None)
                if not callable(api):
                    raise RuntimeError(f"panda_data has no callable {name}")
                result = api(**kwargs)
                return result.copy() if isinstance(result, pd.DataFrame) else pd.DataFrame(result)
            except Exception:
                if attempt + 1 == self.retries:
                    raise
                time.sleep(min(30.0, 2.0 ** attempt))
        raise RuntimeError("unreachable")

    @staticmethod
    def _batches(values: list[str], size: int = 50) -> Iterable[list[str]]:
        for index in range(0, len(values), size):
            yield values[index:index + size]

    def _all_a(self, start: str, end: str, as_of: str | None = None) -> list[str]:
        detail = self._call("get_stock_detail", status=None)
        if "symbol" not in detail:
            raise RuntimeError("get_stock_detail returned no symbol column")
        if "listed_date" not in detail:
            raise RuntimeError("get_stock_detail returned no listed_date column")
        range_start, range_end = pd.Timestamp(start), pd.Timestamp(end)
        listed = pd.to_datetime(detail["listed_date"], format="mixed", errors="coerce")
        delisted = pd.to_datetime(
            detail["de_listed_date"] if "de_listed_date" in detail else pd.Series(pd.NaT, index=detail.index),
            format="mixed", errors="coerce",
        )
        symbols = detail["symbol"].astype(str).str.upper()
        self._delisted_dates = {
            symbol: date
            for symbol, date in zip(symbols, delisted)
            if pd.notna(date)
        }
        if as_of is not None:
            cutoff = pd.Timestamp(as_of)
            mask = symbols.str.match(r"^\d{6}\.(SH|SZ)$") & listed.le(cutoff) & (delisted.isna() | delisted.gt(cutoff))
        else:
            mask = symbols.str.match(r"^\d{6}\.(SH|SZ)$") & listed.le(range_end) & (delisted.isna() | delisted.ge(range_start))
        return sorted(symbols.loc[mask].unique().tolist())

    def load(self, start: str, end: str, symbols: list[str] | None = None, universe_as_of: str | None = None) -> pd.DataFrame:
        self._delisted_dates = {}
        universe = [symbol.upper() for symbol in symbols] if symbols else self._all_a(start, end, universe_as_of)
        frames: list[pd.DataFrame] = []
        first_year, last_year = pd.Timestamp(start).year, pd.Timestamp(end).year
        for year in range(first_year, last_year + 1):
            chunk_start = max(pd.Timestamp(start), pd.Timestamp(year=year, month=1, day=1)).strftime("%Y%m%d")
            chunk_end = min(pd.Timestamp(end), pd.Timestamp(year=year, month=12, day=31)).strftime("%Y%m%d")
            for batch in self._batches(universe):
                frame = self._call(
                    "get_stock_daily_post", symbol=batch, start_date=chunk_start,
                    end_date=chunk_end, fields=["symbol", "date", "close"],
                )
                if not frame.empty:
                    frames.append(frame)
        if not frames:
            raise RuntimeError("PandaData returned no post-adjusted daily prices")
        combined = pd.concat(frames, ignore_index=True)
        exact = combined.drop_duplicates().copy()
        conflicting = exact.duplicated(["date", "symbol"], keep=False)
        if conflicting.any():
            sample = exact.loc[conflicting, ["date", "symbol"]].iloc[0]
            raise RuntimeError(f"conflicting PandaData daily rows: {sample['date']} {sample['symbol']}")
        exact["de_listed_date"] = exact["symbol"].astype(str).str.upper().map(self._delisted_dates)
        result = normalize_panel(exact)
        result.attrs["provided_columns"] = ["date", "symbol", "close", "de_listed_date"]
        result.attrs["provider_context"] = {
            "provider": "PandaData",
            "sdk_version": importlib.metadata.version("panda-data"),
            "requested_universe_size": len(universe),
            "universe_sha256": hashlib.sha256("\n".join(sorted(universe)).encode("ascii")).hexdigest(),
        }
        return result
