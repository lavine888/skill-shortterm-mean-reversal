# 数据指南

优先使用 PandaData `get_stock_daily_post` 的后复权收盘价，避免分红送转制造虚假反转。离线文件必须冻结并记录来源日期，不要把当前成分股列表用于历史全市场回测。

必需字段为 `date`、`symbol`、`close`。同一证券同一天只能有一行，价格必须为正。可选的 `suspended`、`is_st`、`tradable` 应是当日点时状态；如果来源只提供当前状态，不应回填到历史。

全市场历史回测应包含当时已上市、尚未退市的证券，并保留退市标的。输出中的 `forward_coverage` 和 `signal_universe_size` 是解释缺失与偏差的必要证据。

市场交易日不应依赖稀疏证券面板推断。正式运行应通过 `--calendar` 提供冻结的 SH/SZ 交易日文件，并保存输出中的 `calendar_sha256`。未提供时结果会明确标记 `calendar_source=panel_date_union`。
