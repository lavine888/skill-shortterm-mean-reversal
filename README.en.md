<div align="center">

# Q58 · Short-Term Mean Reversal

**Five-session cross-sectional reversal research for A-shares**

Point-in-time signals · Drift-aware turnover · PandaData · Auditable outputs

[![CI](https://github.com/lavine888/skill-shortterm-mean-reversal/actions/workflows/validate.yml/badge.svg)](https://github.com/lavine888/skill-shortterm-mean-reversal/actions/workflows/validate.yml)
[![Version](https://img.shields.io/badge/version-0.5.0-2563EB)](./CHANGELOG.md)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/tests-33%20passed-2E7D32)](./tests)
[![PandaData](https://img.shields.io/badge/PandaData-0.0.12-0F766E)](./VALIDATION.md)
[![License](https://img.shields.io/badge/license-GPL--3.0-4B5563)](./LICENSE)

<a href="https://github.com/lavine888/skill-shortterm-mean-reversal">Repository</a> ·
<a href="./SKILL.md">Skill entrypoint</a> ·
<a href="./VALIDATION.md">Validation report</a>

[简体中文](./README.md) · **English**

</div>

> The independent canonical Skill for Liangshuyuan Q58. It researches short-term loser rebounds and winner reversals as a cross-sectional factor. It is not a live trading system or investment advice.

## At A Glance

<table>
<tr>
<td><strong>Signal</strong><br>5-market-session return</td>
<td><strong>Portfolio</strong><br>Bottom 10% long / top 10% short</td>
<td><strong>Execution</strong><br>Next-session close</td>
<td><strong>Holding</strong><br>5 sessions, non-overlapping</td>
</tr>
<tr>
<td><strong>Evidence</strong><br>Point-in-time inputs</td>
<td><strong>Accounting</strong><br>Drift-aware turnover</td>
<td><strong>Data</strong><br>PandaData or frozen panel</td>
<td><strong>Status</strong><br>Runnable / experimental</td>
</tr>
</table>

## Research Contract

| Stage | Fixed rule |
|---|---|
| Signal | `decision_close / close_5_market_sessions_ago - 1` |
| Ranking | The lowest trailing returns receive the highest reversal score |
| Long leg | Bottom 10%, gross notional `+0.5` |
| Short leg | Top 10%, gross notional `-0.5` |
| Execution | Observe the decision close; enter at the next market close |
| Holding | Entry close through the close five market sessions later |
| Rebalance | Every five market sessions; `rebalance_every == hold_days` |
| Cost | `cost_rate × actual traded notional` after weight drift |
| Missing entry | No fill; capital remains in cash |
| Missing exit | Fail-closed by default |
| Delisting | Optional, explicit `last_available_close` policy only |

### Signal Lifecycle

```mermaid
flowchart LR
    A[Point-in-time panel] --> B[5-session return]
    B --> C[Cross-sectional rank]
    C --> D[Bottom 10% long]
    C --> E[Top 10% short]
    D --> F[Next-close execution]
    E --> F
    F --> G[5-session holding period]
    G --> H[Drift-aware turnover and costs]
    H --> I[Validated JSON evidence]
```

## Real Validation

The live-data run used `panda_data 0.0.12` and completed against the full Shanghai/Shenzhen universe. Raw provider outputs are deliberately excluded from Git; the report preserves their hashes, counts and conclusions.

| Check | Observed result |
|---|---:|
| Decision-date universe | **5,122** securities |
| Valid five-session signals | **5,121** securities |
| 2024 backtest panel | **1,389,119** rows / 5,174 securities |
| Complete non-overlapping periods | **48** |
| Forward-return coverage | **99.998%** |
| Rank IC coverage | **99.976%** |
| Local test suite | **33 passed** |

### 2024 Research Snapshot

| Metric | Result |
|---|---:|
| Gross return | `35.32%` |
| Net return, 0.1% one-way cost | `24.59%` |
| Annualized return | `25.96%` |
| Annualized volatility | `18.21%` |
| Sharpe | `1.35` |
| Maximum drawdown | `-6.17%` |
| Mean Rank IC | `0.0444` |

This is a one-year research result, not evidence of durable alpha. The first half had Rank IC `-0.0096`; the second half had `0.0984`. The factor is visibly regime-sensitive.

See [VALIDATION.md](./VALIDATION.md) for the full evidence, stability split, delisting cases and limitations.

## Quick Start

### Deterministic demo

```powershell
pip install -r requirements-dev.txt
python -m pytest -q

python scripts/backtest.py --provider demo `
  --start 20220101 --end 20241231 `
  --evidence-output output/demo-evidence.parquet `
  --output output/demo.json

python scripts/validate.py output/demo.json --evidence output/demo-evidence.parquet
python scripts/summarize.py output/demo.json --evidence output/demo-evidence.parquet
```

The demo is synthetic and deterministic. It validates the software contract, not the strategy.

### PandaData snapshot

Credentials are read from environment variables only. They are never written to source, output or cache:

```powershell
$env:PANDA_DATA_USERNAME = "your-account"
$env:PANDA_DATA_PASSWORD = "your-password"

python scripts/factor.py --provider pandadata --all-a `
  --as-of 20241231 `
  --cache-dir output/panda-cache `
  --output output/factor-20241231.json

python scripts/validate.py output/factor-20241231.json
```

### PandaData backtest

```powershell
python scripts/backtest.py --provider pandadata --all-a `
  --start 20240102 --end 20241231 `
  --cost-rate 0.001 `
  --cache-dir output/panda-cache `
  --delisting-exit-policy last_available_close `
  --evidence-output output/backtest-2024-evidence.parquet `
  --output output/backtest-2024.json

python scripts/validate.py output/backtest-2024.json --evidence output/backtest-2024-evidence.parquet
python scripts/summarize.py output/backtest-2024.json --evidence output/backtest-2024-evidence.parquet
```

PandaData remains `experimental`: the current price response does not expose historical point-in-time suspension, ST, limit-up/limit-down or borrow-availability fields.

Request caches are isolated by SDK, anonymous account hash, provider environment, method and parameters. Each response is written atomically as Parquet plus a verified manifest, so an interrupted full-market run can resume completed requests.

## Offline Panel Contract

CSV or Parquet input must contain:

| Column | Meaning |
|---|---|
| `date` | A-share trading date |
| `symbol` | Security code, for example `600519.SH` |
| `close` | Post-adjusted close |

Optional point-in-time flags are `suspended`, `is_st` and `tradable`. The normalizer accepts only explicit boolean values such as `true/false`, `1/0` or `yes/no`; invalid prices, blank symbols, weekend dates and conflicting duplicate rows fail closed.

```powershell
python scripts/backtest.py --provider file `
  --input data/daily.parquet `
  --calendar data/trading-calendar.csv `
  --start 20210101 --end 20251231 `
  --output output/backtest.json
```

Use an independent frozen calendar for sparse panels. Without `--calendar`, the result records `calendar_source=panel_date_union`.

## Auditability

Every validated result contains:

- Input-panel and market-calendar SHA-256 hashes;
- Source, SDK, universe, date range and rule configuration;
- Decision, entry, planned exit and actual exit dates;
- Per-symbol past return, target/executed weight and entry/exit price;
- Fill status, forced-delisting status, coverage, Rank IC, turnover and costs;
- Recomputable aggregate metrics and a deterministic `run_id`.

With `--evidence-output`, the complete cross-sectional Parquet is bound to the JSON by schema, row count and SHA-256. The validator independently reconstructs both tails and recomputes Rank IC.

The validator rejects non-finite numbers, invalid chronology, inconsistent returns or costs, missing evidence and tampered run IDs. JSON writes use atomic replacement.

## Repository Map

```text
lavine_reversal/     factor logic, normalization, accounting, providers, validation
scripts/             factor, backtest, validate, summarize, packaging
tests/               point-in-time, execution, delisting, data and contract tests
references/          methodology, data contract and source boundaries
SKILL.md             Agent Skill entrypoint and qsh-form
VALIDATION.md        Real PandaData execution report
```

## Release Archive

```powershell
python scripts/package_release.py --destination dist
```

The deterministic ZIP contains both READMEs and a `MANIFEST.sha256`. The packager rejects generated output, caches, credentials and Python build artifacts.

## Boundaries

- A-share cash equities generally cannot provide the individual-stock short leg used by this diagnostic.
- Borrow fees, availability, recalls, limit-order queues and intraday slippage are not modeled.
- `last_available_close` assumes the last pre-delisting close was executable; this is an explicit research approximation.
- The engine supports non-overlapping portfolios only.
- One year of full-market results is not enough for a multi-cycle conclusion; multi-year and out-of-sample validation remain required.

Read [methodology.md](./references/methodology.md) for the math, [data_guide.md](./references/data_guide.md) for input requirements, and [source_boundary.md](./references/source_boundary.md) before interpreting any result.

See [ROADMAP.md](./ROADMAP.md) for independent cross-sectional evidence, official trading states and multi-year validation work.

## License

[GNU General Public License v3.0](./LICENSE)
