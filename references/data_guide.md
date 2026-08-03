# 数据指南

优先使用 PandaData `get_stock_daily_post` 的后复权收盘价，避免分红送转制造虚假反转。离线文件必须冻结并记录来源日期，不要把当前成分股列表用于历史全市场回测。

必需字段为 `date`、`symbol`、`close`。同一证券同一天只能有一行，价格必须为正。可选的 `suspended`、`is_st`、`tradable`、`limit_up`、`limit_down` 应是当日点时状态；如果来源只提供当前状态，不应回填到历史。

PandaData Provider 使用 `get_stock_daily_post` 的 `trade_status`、`name`、`limit_up` 和 `limit_down`：非零 `trade_status` 映射为停牌，历史名称包含 `ST` 映射为 ST，涨跌停价格用于方向性成交约束。

全市场历史回测应包含当时已上市、尚未退市的证券，并保留退市标的。输出中的 `forward_coverage` 和 `signal_universe_size` 是解释缺失与偏差的必要证据。

市场交易日不应依赖稀疏证券面板推断。PandaData 模式默认调用并缓存 `get_trade_cal(exchange="SH")`；离线运行应通过 `--calendar` 提供冻结的 SH/SZ 交易日文件。输出保存 `calendar_source` 与 `calendar_sha256`。
