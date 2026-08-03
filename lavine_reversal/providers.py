from __future__ import annotations

import hashlib
import importlib.metadata
import json
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
    def __init__(
        self, request_interval: float = 1.0, retries: int = 5,
        cache_dir: str | Path | None = None,
    ):
        if not math.isfinite(request_interval) or request_interval < 0:
            raise ValueError("request_interval must be finite and non-negative")
        if isinstance(retries, bool) or not isinstance(retries, int) or retries < 1:
            raise ValueError("retries must be a positive integer")
        self.request_interval = request_interval
        self.retries = retries
        self.cache_dir = Path(cache_dir) if cache_dir else None
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
        self.sdk_version = importlib.metadata.version("panda-data")
        self._cache_context = {
            "sdk_version": self.sdk_version,
            "account_hash": hashlib.sha256(username.encode("utf-8")).hexdigest()[:16],
            "base_url_hash": hashlib.sha256((base_url or "default").encode("utf-8")).hexdigest()[:16],
        }
        self._cache_hits = 0
        self._cache_misses = 0
        self._response_entries: set[str] = set()

    def _cache_paths(self, name: str, kwargs: dict) -> tuple[Path, Path] | None:
        if self.cache_dir is None:
            return None
        payload = json.dumps(
            {"method": name, "kwargs": kwargs, "context": self._cache_context},
            sort_keys=True, ensure_ascii=True, default=str, separators=(",", ":"),
        )
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        parquet = self.cache_dir / name / f"{digest}.parquet"
        return parquet, parquet.with_suffix(".json")

    @staticmethod
    def _frame_digest(frame: pd.DataFrame) -> str:
        metadata = json.dumps(
            {"columns": list(frame.columns), "dtypes": [str(dtype) for dtype in frame.dtypes]},
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        values = pd.util.hash_pandas_object(frame, index=False).values.tobytes()
        return hashlib.sha256(metadata + values).hexdigest()

    def _read_cache(self, paths: tuple[Path, Path] | None) -> pd.DataFrame | None:
        if paths is None:
            return None
        parquet, manifest_path = paths
        if not parquet.exists() or not manifest_path.exists():
            return None
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            frame = pd.read_parquet(parquet)
            digest = self._frame_digest(frame)
            if (
                manifest.get("frame_sha256") != digest
                or manifest.get("row_count") != len(frame)
                or manifest.get("columns") != list(frame.columns)
            ):
                return None
            self._cache_hits += 1
            self._response_entries.add(f"{parquet.stem}:{digest}")
            return frame
        except Exception:
            return None

    def _write_cache(self, paths: tuple[Path, Path] | None, frame: pd.DataFrame) -> bool:
        if paths is None:
            return False
        parquet, manifest_path = paths
        temporary = parquet.with_suffix(f".{os.getpid()}.tmp.parquet")
        manifest_temporary = manifest_path.with_suffix(f".{os.getpid()}.tmp.json")
        try:
            parquet.parent.mkdir(parents=True, exist_ok=True)
            digest = self._frame_digest(frame)
            frame.to_parquet(temporary, index=False)
            manifest_temporary.write_text(json.dumps({
                "method": parquet.parent.name,
                "row_count": len(frame),
                "columns": list(frame.columns),
                "frame_sha256": digest,
                "context": self._cache_context,
            }, sort_keys=True, indent=2), encoding="utf-8")
            temporary.replace(parquet)
            manifest_temporary.replace(manifest_path)
            self._response_entries.add(f"{parquet.stem}:{digest}")
            return True
        except Exception:
            return False
        finally:
            temporary.unlink(missing_ok=True)
            manifest_temporary.unlink(missing_ok=True)

    def _call(self, name: str, **kwargs) -> pd.DataFrame:
        cache_paths = self._cache_paths(name, kwargs)
        cached = self._read_cache(cache_paths)
        if cached is not None:
            return cached
        self._cache_misses += 1
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
                frame = result.copy() if isinstance(result, pd.DataFrame) else pd.DataFrame(result)
                if not self._write_cache(cache_paths, frame):
                    self._response_entries.add(f"uncached:{self._frame_digest(frame)}")
                return frame
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

    def load_calendar(self, start: str, end: str) -> pd.DatetimeIndex:
        frame = self._call(
            "get_trade_cal", start_date=pd.Timestamp(start).strftime("%Y%m%d"),
            end_date=pd.Timestamp(end).strftime("%Y%m%d"), exchange="SH",
            is_trading_day=None, fields=["nature_date", "is_trade", "exchange"],
        )
        required = {"nature_date", "is_trade"}
        missing = sorted(required - set(frame.columns))
        if missing:
            raise RuntimeError("get_trade_cal missing columns: " + ", ".join(missing))
        raw_dates = frame.loc[
            pd.to_numeric(frame["is_trade"], errors="coerce").eq(1), "nature_date"
        ].astype("string").str.replace(r"\.0$", "", regex=True)
        dates = pd.to_datetime(raw_dates, format="%Y%m%d", errors="coerce").dropna().dt.normalize()
        calendar = pd.DatetimeIndex(dates.drop_duplicates().sort_values())
        if calendar.empty:
            raise RuntimeError("get_trade_cal returned no SH trading sessions")
        return calendar

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
                    end_date=chunk_end,
                    fields=["symbol", "date", "close", "trade_status", "limit_up", "limit_down", "name"],
                    st=True,
                )
                if not frame.empty:
                    frames.append(frame)
        if not frames:
            raise RuntimeError("PandaData returned no post-adjusted daily prices")
        combined = pd.concat(frames, ignore_index=True)
        trading_required = {"trade_status", "limit_up", "limit_down", "name"}
        trading_missing = sorted(trading_required - set(combined.columns))
        if trading_missing:
            raise RuntimeError("get_stock_daily_post missing trading columns: " + ", ".join(trading_missing))
        exact = combined.drop_duplicates().copy()
        conflicting = exact.duplicated(["date", "symbol"], keep=False)
        if conflicting.any():
            sample = exact.loc[conflicting, ["date", "symbol"]].iloc[0]
            raise RuntimeError(f"conflicting PandaData daily rows: {sample['date']} {sample['symbol']}")
        exact["de_listed_date"] = exact["symbol"].astype(str).str.upper().map(self._delisted_dates)
        exact["suspended"] = pd.to_numeric(exact["trade_status"], errors="coerce").ne(0)
        exact["is_st"] = exact["name"].astype("string").str.upper().str.contains("ST", regex=False, na=False)
        exact["tradable"] = ~exact["suspended"]
        result = normalize_panel(exact)
        result.attrs["provided_columns"] = [
            "date", "symbol", "close", "de_listed_date", "suspended", "is_st",
            "tradable", "limit_up", "limit_down", "trade_status", "name",
        ]
        result.attrs["provider_context"] = {
            "provider": "PandaData",
            "sdk_version": self.sdk_version,
            "requested_universe_size": len(universe),
            "universe_sha256": hashlib.sha256("\n".join(sorted(universe)).encode("ascii")).hexdigest(),
        }
        self.bind_runtime_context(result)
        return result

    def bind_runtime_context(self, frame: pd.DataFrame) -> None:
        context = dict(frame.attrs.get("provider_context", {}))
        context.update({
            "cache_enabled": self.cache_dir is not None,
            "response_count": len(self._response_entries),
            "response_manifest_sha256": hashlib.sha256(
                "\n".join(sorted(self._response_entries)).encode("ascii")
            ).hexdigest(),
        })
        frame.attrs["provider_context"] = context

    def cache_diagnostics(self) -> dict[str, int]:
        return {"hits": self._cache_hits, "misses": self._cache_misses}
