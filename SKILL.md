---
name: shortterm-mean-reversal-lavine-version
description: "Run and audit the Q58 five-session cross-sectional short-term return reversal factor on A-shares. Use when an agent needs 5-day loser-versus-winner signals, point-in-time factor snapshots, or a cost-aware mean-reversion research backtest."
quantSkills:
  organization: https://github.com/lavine888
  repository: lavine888/skill-shortterm-mean-reversal
  repository_url: https://github.com/lavine888/skill-shortterm-mean-reversal
  project_type: skill
  collection: liangshuyuan-q58
  license: GPL-3.0-only
  category: factor
  tags: [a-share, mean-reversion, short-term-reversal, point-in-time, backtest]
  platforms: [claude-code, codex, openclaw]
  language: zh-en
  status: active
  validation_level: runnable
  maintainer_type: community
  requires: []
  summary_zh: A 股五交易日收益率横截面反转因子与成本敏感回测。
  summary_en: Cost-aware A-share five-session cross-sectional return reversal research.
---

```json qsh-form
{
  "version": 1,
  "task": {
    "placeholder": "例如：回测 2021-2025 年 A 股 5 日反转因子并检查成本敏感性",
    "required": true
  },
  "fields": [
    {"key": "start", "type": "date", "label": "开始日期"},
    {"key": "end", "type": "date", "label": "结束日期"},
    {"key": "symbols", "type": "text", "label": "A股代码（可选）"}
  ],
  "prompt_template": "{{task}}；区间：{{start}} 至 {{end}}；股票池：{{symbols}}。只使用决策时点可见数据，并明确交易成本和空头可执行性边界。附件：{{#attachments}}"
}
```

# Short-Term Mean Reversal - Lavine Version

Use this skill for Q58 research: rank A-shares by their trailing five-market-session return, buy the bottom decile and short the top decile. Signals use the decision close, execution is delayed to the next market close, and positions are held for five market sessions.

## Core Workflow

1. Load post-adjusted daily closes from PandaData, a verified resumable request cache, or a frozen offline file.
2. At each decision date, expose only rows dated on or before that date.
3. Require valid closes on both the decision date and exactly five market sessions earlier.
4. Exclude decision-date rows marked suspended, ST or non-tradable when those flags are supplied.
5. Select deterministic bottom/top deciles, with 0.5 long and 0.5 short gross notional.
6. Leave missing or non-tradable entries in cash; fail closed if an executed position cannot be valued and exited.
7. Measure returns from the next market close through the close five sessions later.
8. Deduct one-way costs from drift-adjusted traded notional and report Rank IC, turnover, coverage and drawdown.

## Run

Install and run the deterministic demo first:

```powershell
pip install -r requirements.txt
python scripts/backtest.py --provider demo --start 20220101 --end 20241231 `
  --evidence-output output/demo-evidence.parquet --output output/demo.json
python scripts/validate.py output/demo.json --evidence output/demo-evidence.parquet
```

Run a frozen CSV or Parquet panel containing `date`, `symbol`, and post-adjusted `close`:

```powershell
python scripts/backtest.py --provider file --input data/daily.parquet `
  --start 20210101 --end 20251231 --cost-rate 0.001 --output output/backtest.json
```

PandaData credentials are read from environment variables:

```powershell
$env:PANDA_DATA_USERNAME = "your-account"
$env:PANDA_DATA_PASSWORD = "your-password"
python scripts/backtest.py --provider pandadata --all-a `
  --start 20210101 --end 20251231 `
  --cache-dir output/panda-cache `
  --delisting-exit-policy last_available_close `
  --evidence-output output/factor-evidence.parquet --output output/backtest.json
```

## Output Contract

The JSON records the complete strategy configuration, source status, input-panel SHA-256, request-manifest SHA-256, deterministic run ID, aggregate performance, and each rebalance period. Per-symbol evidence includes target and executed weights, entry and exit prices, fill statuses and forward returns, allowing the validator to recompute period return and cost. An optional full cross-sectional Parquet is bound by schema, counts and SHA-256 so the validator can reconstruct tails and Rank IC. PandaData remains `experimental` until its trading-status fields and historical delisted universe are verified against the live SDK.

Use `scripts/summarize.py` only after `scripts/validate.py` passes. The explicit delisting policy assumes execution at the last available close before a confirmed delisting; the default policy remains `error`.

## Safety Boundary

The long-short portfolio is a factor research diagnostic, not a directly executable A-share cash-equity strategy. A-shares cannot generally be shorted; borrow availability, limits, limit-up/limit-down fills, intraday slippage and forced delisting outcomes are not modeled. Do not describe demo or backtest output as live performance or investment advice.

Read `references/methodology.md` and `references/data_guide.md` before interpreting results.
