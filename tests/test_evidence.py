from __future__ import annotations

from copy import deepcopy

import pandas as pd
import pytest

from lavine_reversal import StrategyConfig, run_backtest
from lavine_reversal.contract import compute_run_id
from lavine_reversal.evidence import FactorEvidenceWriter, attach_factor_evidence, file_sha256
from lavine_reversal.providers import DemoProvider
from lavine_reversal.validate import validate_result


def evidence_result(tmp_path):
    panel = DemoProvider(n_symbols=30).load("2024-01-01", "2024-06-30")
    path = tmp_path / "factor-evidence.parquet"
    with FactorEvidenceWriter(path) as writer:
        result = run_backtest(
            panel, "2024-02-01", "2024-06-28",
            StrategyConfig(min_universe=20), source="demo",
            evidence_sink=writer.write,
        )
    attach_factor_evidence(result, writer.metadata())
    return result, path


def test_full_cross_section_evidence_validates(tmp_path):
    result, path = evidence_result(tmp_path)
    validate_result(result, evidence_path=path)
    assert result["factor_evidence"]["decision_count"] == result["metrics"]["periods"]
    assert result["factor_evidence"]["row_count"] == sum(
        period["signal_universe_size"] for period in result["periods"]
    )


def test_group_tampering_fails_even_after_rehash(tmp_path):
    result, path = evidence_result(tmp_path)
    frame = pd.read_parquet(path)
    decision = result["periods"][0]["decision_date"]
    selected = frame.index[(frame["decision_date"] == decision) & (frame["selected_side"] == "long")][0]
    frame.loc[selected, "selected_side"] = "none"
    frame.to_parquet(path, index=False)
    result["factor_evidence"]["file_sha256"] = file_sha256(path)
    result["run_id"] = compute_run_id(result)
    with pytest.raises(ValueError, match="long selection is inconsistent"):
        validate_result(result, evidence_path=path)


def test_rank_ic_tampering_fails_even_after_rehash(tmp_path):
    result, path = evidence_result(tmp_path)
    tampered = deepcopy(result)
    tampered["periods"][0]["rank_ic"] += 0.1
    rank_ics = [period["rank_ic"] for period in tampered["periods"] if period["rank_ic"] is not None]
    tampered["metrics"]["mean_rank_ic"] = sum(rank_ics) / len(rank_ics)
    tampered["run_id"] = compute_run_id(tampered)
    with pytest.raises(ValueError, match="Rank IC is inconsistent"):
        validate_result(tampered, evidence_path=path)


def test_directional_limit_block_is_bound_to_full_evidence(tmp_path):
    panel = DemoProvider(n_symbols=30).load("2024-01-01", "2024-06-30")
    config = StrategyConfig(min_universe=20)
    baseline = run_backtest(panel, "2024-02-01", "2024-06-28", config, source="demo")
    first = baseline["periods"][0]
    symbol = first["long_symbols"][0]
    entry = pd.Timestamp(first["entry_date"])
    panel["limit_up"] = panel["close"] * 2.0
    panel["limit_down"] = panel["close"] * 0.5
    mask = (panel["date"] == entry) & (panel["symbol"] == symbol)
    panel.loc[mask, "limit_up"] = panel.loc[mask, "close"]
    path = tmp_path / "limited-evidence.parquet"
    with FactorEvidenceWriter(path) as writer:
        result = run_backtest(
            panel, "2024-02-01", "2024-06-28", config,
            source="demo", evidence_sink=writer.write,
        )
    attach_factor_evidence(result, writer.metadata())
    validate_result(result, evidence_path=path)
    assert result["periods"][0]["selected_evidence"][symbol]["entry_block_reason"] == "limit_up"
