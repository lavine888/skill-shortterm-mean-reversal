from __future__ import annotations

import pandas as pd
import pytest
from types import SimpleNamespace

from lavine_reversal.providers import PandaDataProvider


def daily_response(closes: list[float]) -> pd.DataFrame:
    size = len(closes)
    return pd.DataFrame({
        "date": ["2024-01-02"] * size,
        "symbol": ["000001.SZ"] * size,
        "close": closes,
        "trade_status": [0] * size,
        "limit_up": [11.0] * size,
        "limit_down": [9.0] * size,
        "name": ["Ping An Bank"] * size,
    })


def provider_with_response(frame: pd.DataFrame) -> PandaDataProvider:
    provider = object.__new__(PandaDataProvider)
    provider._call = lambda *args, **kwargs: frame.copy()
    provider.sdk_version = "test"
    provider.cache_dir = None
    provider._cache_hits = 0
    provider._cache_misses = 0
    provider._response_entries = set()
    return provider


def test_pandadata_exact_duplicates_are_deduplicated():
    frame = daily_response([10.0, 10.0])
    result = provider_with_response(frame).load("20240101", "20241231", ["000001.SZ"])
    assert len(result) == 1


def test_pandadata_conflicting_duplicates_fail_closed():
    frame = daily_response([10.0, 11.0])
    with pytest.raises(RuntimeError, match="conflicting PandaData daily rows"):
        provider_with_response(frame).load("20240101", "20241231", ["000001.SZ"])


def test_pandadata_maps_suspension_st_and_limit_fields():
    frame = pd.DataFrame({
        "date": ["2024-01-02", "2024-01-03"],
        "symbol": ["000001.SZ", "000001.SZ"],
        "close": [10.0, 9.5],
        "trade_status": [1, 0],
        "limit_up": [11.0, 10.5],
        "limit_down": [9.0, 8.5],
        "name": ["Example", "*ST Example"],
    })
    result = provider_with_response(frame).load("20240101", "20241231", ["000001.SZ"])
    assert result["suspended"].tolist() == [True, False]
    assert result["is_st"].tolist() == [False, True]
    assert result["tradable"].tolist() == [False, True]
    assert result["limit_up"].tolist() == [11.0, 10.5]


def cached_provider(cache_dir, api) -> PandaDataProvider:
    provider = object.__new__(PandaDataProvider)
    provider.request_interval = 0.0
    provider.retries = 1
    provider.cache_dir = cache_dir
    provider._last_request = 0.0
    provider._cache_context = {
        "sdk_version": "test", "account_hash": "account", "base_url_hash": "base",
    }
    provider._cache_hits = 0
    provider._cache_misses = 0
    provider._response_entries = set()
    provider.api = api
    return provider


def test_pandadata_cache_reuses_a_verified_response(tmp_path):
    calls = {"count": 0}

    def get_prices(**kwargs):
        calls["count"] += 1
        return pd.DataFrame({"symbol": ["000001.SZ"], "date": ["2024-01-02"], "close": [10.0]})

    first = cached_provider(tmp_path, SimpleNamespace(get_prices=get_prices))
    expected = first._call("get_prices", symbol=["000001.SZ"])
    second = cached_provider(tmp_path, SimpleNamespace(get_prices=lambda **kwargs: pytest.fail("cache miss")))
    actual = second._call("get_prices", symbol=["000001.SZ"])
    pd.testing.assert_frame_equal(actual, expected)
    assert calls["count"] == 1
    assert second._cache_hits == 1
    assert second._cache_misses == 0


def test_pandadata_calendar_uses_only_official_trading_sessions():
    frame = pd.DataFrame({
        "nature_date": [20240101, 20240102, 20240103, None],
        "is_trade": [0, 1, 1, 1],
        "exchange": ["SH", "SH", "SH", "SH"],
    })
    provider = object.__new__(PandaDataProvider)
    provider._call = lambda *args, **kwargs: frame.copy()
    calendar = provider.load_calendar("20240101", "20240103")
    assert calendar.tolist() == [pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-03")]


def test_runtime_context_covers_all_provider_responses(tmp_path):
    provider = cached_provider(tmp_path, SimpleNamespace())
    provider._response_entries = {"daily:abc", "calendar:def"}
    frame = pd.DataFrame({"date": [pd.Timestamp("2024-01-02")], "symbol": ["000001.SZ"], "close": [10.0]})
    frame.attrs["provider_context"] = {"provider": "PandaData", "sdk_version": "test"}
    provider.bind_runtime_context(frame)
    assert frame.attrs["provider_context"]["response_count"] == 2
    assert len(frame.attrs["provider_context"]["response_manifest_sha256"]) == 64
