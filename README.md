# Q58 Short-Term Mean Reversal

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/tests-24%20passed-2E7D32)](./tests)
[![PandaData](https://img.shields.io/badge/PandaData-0.0.12-0F766E)](./VALIDATION.md)
[![License](https://img.shields.io/badge/license-GPL--3.0-4B5563)](./LICENSE)
[![Validation](https://img.shields.io/badge/validation-runnable-F59E0B)](./VALIDATION.md)

量枢院 Q58 的独立 canonical Skill：用严格 point-in-time 纪律研究 A 股五交易日横截面反转，并完整记录信号、成交、成本、退市处置和数据来源。

> 当前版本 `0.3.0`。这是因子研究工具，不是实盘交易系统，也不构成投资建议。

## 策略一览

| 项目 | 固定口径 |
|---|---|
| 信号 | `decision_close / close_5_market_sessions_ago - 1` |
| 多头 | 过去 5 日收益最低的 10% |
| 空头 | 过去 5 日收益最高的 10% |
| 权重 | 多头 `+0.5`、空头 `-0.5`，组内等权 |
| 成交 | 决策日后第 1 个市场交易日收盘 |
| 持有与调仓 | 持有 5 个市场交易日，每 5 日调仓 |
| 成本 | 单边费率 × 漂移后实际交易名义金额 |
| 缺失进场 | 不成交，资金保留为现金 |
| 缺失退出 | 默认 fail-closed，不以 0% 收益填充 |
| 退市 | 仅显式启用时按确认退市前最后报价强制退出 |

## 已验证

真实 PandaData 全市场验证使用 `panda_data 0.0.12`：

| 验证项 | 结果 |
|---|---:|
| 2024-12-31 决策日存续池 | 5,122 只 |
| 有效五日信号 | 5,121 只 |
| 2024 回测行情 | 1,389,119 行 / 5,174 只证券 |
| 完整非重叠期间 | 48 |
| 平均远期收益覆盖率 | 99.998% |
| 平均 Rank IC 覆盖率 | 99.976% |
| 单元测试 | 24 passed |

2024 研究回测得到毛收益 `35.32%`、0.1% 单边成本后净收益 `24.59%`、平均 Rank IC `0.0444`。上半年 Rank IC 为负、下半年明显转正，结果存在显著状态依赖，不能外推为长期收益。

完整口径、稳定性拆解、退市样本和限制见 [VALIDATION.md](./VALIDATION.md)。原始全市场 JSON 含大量供应商数据证据，已通过哈希固定但不纳入 Git。

## 快速开始

```powershell
pip install -r requirements-dev.txt
pytest -q

python scripts/backtest.py --provider demo `
  --start 20220101 --end 20241231 `
  --output output/demo.json

python scripts/validate.py output/demo.json
python scripts/summarize.py output/demo.json
```

`demo` 使用确定性合成数据，只验证软件行为，不构成策略收益证据。

## PandaData

凭据只从环境变量读取，不写入代码、结果或缓存：

```powershell
$env:PANDA_DATA_USERNAME = "your-account"
$env:PANDA_DATA_PASSWORD = "your-password"

python scripts/factor.py --provider pandadata --all-a `
  --as-of 20241231 `
  --output output/factor-20241231.json

python scripts/backtest.py --provider pandadata --all-a `
  --start 20240102 --end 20241231 `
  --cost-rate 0.001 `
  --delisting-exit-policy last_available_close `
  --output output/backtest-2024.json

python scripts/validate.py output/backtest-2024.json
python scripts/summarize.py output/backtest-2024.json
```

PandaData 路径仍标记为 `experimental`，因为当前行情响应没有提供历史点时停牌、ST、涨跌停和融券可得性字段。

## 离线数据

CSV 或 Parquet 必需列：

| 列 | 含义 |
|---|---|
| `date` | A 股交易日期 |
| `symbol` | 证券代码，例如 `600519.SH` |
| `close` | 后复权收盘价 |

可选点时布尔列为 `suspended`、`is_st`、`tradable`。布尔值只接受 `true/false`、`1/0`、`yes/no` 等明确值；非有限价格、空代码、周末日期和冲突重复行会直接报错。

```powershell
python scripts/backtest.py --provider file `
  --input data/daily.parquet `
  --calendar data/trading-calendar.csv `
  --start 20210101 --end 20251231 `
  --output output/backtest.json
```

正式运行应提供独立市场日历。未传 `--calendar` 时，结果会明确记录 `calendar_source=panel_date_union`。

## 审计设计

每份结果均包含：

- 输入面板与市场日历 SHA-256；
- SDK、股票池、日期范围和规则配置；
- 决策、进场、实际退出和计划退出日期；
- 每只入选证券的过去收益、目标/成交权重和进出价格；
- 未成交、强制退市退出、覆盖率、Rank IC、漂移换手和成本；
- 可由校验器重算的聚合指标及确定性 `run_id`。

结果写盘采用临时文件原子替换。校验器会拒绝非有限数字、日期错序、收益或成本不一致、证据缺失以及内容被篡改的 `run_id`。

## 项目结构

```text
lavine_reversal/     因子、数据规范化、回测会计、PandaData 适配与校验
scripts/             factor / backtest / validate / summarize / package_release
tests/               信号、未来函数、退市、漂移换手、脏数据与契约测试
references/          方法、数据契约和来源边界
SKILL.md              Agent Skill 入口与 qsh-form
VALIDATION.md         真实全市场执行验证
```

## 研究边界

- A 股现金股票通常不能直接构建这里的个股空头组合。
- 尚未建模融券费、可借券数量、召回、涨跌停排队和盘中滑点。
- `last_available_close` 假设退市前最后交易日可以成交，是显式研究近似。
- 当前引擎只支持 `rebalance_every == hold_days` 的非重叠组合。
- 一年全市场结果不足以证明跨周期稳定性，正式结论需要多年度和样本外验证。

方法细节见 [methodology.md](./references/methodology.md)，数据约束见 [data_guide.md](./references/data_guide.md)，来源与结论边界见 [source_boundary.md](./references/source_boundary.md)。
