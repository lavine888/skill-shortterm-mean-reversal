from __future__ import annotations

import pandas as pd
import pytest

from lavine_reversal.providers import PandaDataProvider


def provider_with_response(frame: pd.DataFrame) -> PandaDataProvider:
    provider = object.__new__(PandaDataProvider)
    provider._call = lambda *args, **kwargs: frame.copy()
    return provider


def test_pandadata_exact_duplicates_are_deduplicated():
    frame = pd.DataFrame({
        "date": ["2024-01-02", "2024-01-02"],
        "symbol": ["000001.SZ", "000001.SZ"],
        "close": [10.0, 10.0],
    })
    result = provider_with_response(frame).load("20240101", "20241231", ["000001.SZ"])
    assert len(result) == 1


def test_pandadata_conflicting_duplicates_fail_closed():
    frame = pd.DataFrame({
        "date": ["2024-01-02", "2024-01-02"],
        "symbol": ["000001.SZ", "000001.SZ"],
        "close": [10.0, 11.0],
    })
    with pytest.raises(RuntimeError, match="conflicting PandaData daily rows"):
        provider_with_response(frame).load("20240101", "20241231", ["000001.SZ"])
