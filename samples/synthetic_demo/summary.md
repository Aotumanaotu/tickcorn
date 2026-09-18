# 玉米期货 1-Tick 微观结构分析报告

- 合约: **C2701**  交易日: **2026-09-18**
- TickSize: 1.0（来自配置）  分类模式: 严格 (strict)
- 快照总数: 1,240（清洗后；重复 0，无效 0）

## 三个核心问题

### Q1: 1-Tick LastPrice 跳动中有多少未伴随真实报价移动？
在 C2701 的 2026-09-18 交易日中，共观察到 **380** 次 1-Tick LastPrice 跳动。其中 **78.9%** 属于 Bid-Ask Bounce（未伴随 Best Bid/Ask 真实移动；其中 HIGH_CONFIDENCE_BOUNCE 300 次 + LIKELY_BOUNCE 0 次），31.6% 为 Genuine Quote Move（真实报价移动），5.3% 无法确定（AMBIGUOUS，保守分类）。

### Q2: 真实 Quote Move 之前是否存在统计显著的盘口特征？
真实 Quote Move 之前的盘口特征（UP vs DOWN，t 检验 / Mann-Whitney U）：

- 上涨前平均 OBI1 = **-0.0028**，下跌前平均 OBI1 = **0.0215**（p = 0.649884）
- 上涨前 Microprice 偏移 = **-0.0014** tick，下跌前 = **0.0107** tick（p = 0.649884）
- 统计显著（p<0.05）的盘口特征: volume_delta_2, turnover_delta_2, volume_delta_3, turnover_delta_3, volume_delta_5, turnover_delta_5, mid_ret_10, volatility_10


### Q3: 考虑交易成本后是否仍有经济价值？
**本阶段不回答该问题。** 第一阶段仅度量统计可预测性 (Statistical Predictability)。经济价值 (Economic Profitability) 需要第三阶段的 ExecutionSimulator（含手续费、滑点、延迟、成交概率），并做敏感性分析后才能回答。参考：OBI 条件概率表中最强的方向优势为 |P(UP)-P(DOWN)| = 0.4167；对 1-tick 价差品种，该优势必须显著超过往返交易成本（价差+手续费+滑点）才可能具备经济价值。

## 分时段统计

| instrument_id   | trading_day   | group   |   total_snapshots |   transitions |   last_price_change_count |   one_tick_last_change_count |   quote_change_count |   genuine_quote_move_up_count |   genuine_quote_move_down_count |   high_confidence_bounce_count |   likely_bounce_count |   ambiguous_count |   no_move_count |   bounce_ratio |   genuine_move_ratio |   ambiguous_ratio |   high_bounce_share |   mean_spread_ticks |   mean_obi1 |   snapshot_rate_per_min |
|:----------------|:--------------|:--------|------------------:|--------------:|--------------------------:|-----------------------------:|---------------------:|------------------------------:|--------------------------------:|-------------------------------:|----------------------:|------------------:|----------------:|---------------:|---------------------:|------------------:|--------------------:|--------------------:|------------:|------------------------:|
| C2701           | 2026-09-18    | DAY     |              1240 |          1239 |                       440 |                          380 |                  140 |                            60 |                              60 |                            300 |                     0 |                20 |             799 |       0.789474 |             0.315789 |         0.0526316 |                   1 |             1.03226 |  -0.0224689 |                 240.388 |

## 状态转移矩阵 P(next | current)（5 态，AMBIGUOUS 已排除）

|             |   NO_MOVE |   BOUNCE_UP |   BOUNCE_DOWN |   QUOTE_UP |   QUOTE_DOWN |
|:------------|----------:|------------:|--------------:|-----------:|-------------:|
| NO_MOVE     |  0.473684 |    0.225564 |      0.150376 |   0.075188 |     0.075188 |
| BOUNCE_UP   |  1        |    0        |      0        |   0        |     0        |
| BOUNCE_DOWN |  1        |    0        |      0        |   0        |     0        |
| QUOTE_UP    |  1        |    0        |      0        |   0        |     0        |
| QUOTE_DOWN  |  1        |    0        |      0        |   0        |     0        |

## OBI 条件概率表（最强视野）

| obi_bucket   |   horizon |   n |       p_up |     p_down |     p_none |   p_up_minus_p_down |
|:-------------|----------:|----:|-----------:|-----------:|-----------:|--------------------:|
| [-1.0,-0.8)  |         5 |   0 | nan        | nan        | nan        |        nan          |
| [-0.8,-0.6)  |         5 |  24 |   0.583333 |   0.166667 |   0.25     |          0.416667   |
| [-0.6,-0.4)  |         5 | 114 |   0.22807  |   0.192982 |   0.578947 |          0.0350877  |
| [-0.4,-0.2)  |         5 | 232 |   0.181034 |   0.189655 |   0.62931  |         -0.00862069 |
| [-0.2,0.0)   |         5 | 294 |   0.258503 |   0.272109 |   0.469388 |         -0.0136054  |
| [0.0,0.2)    |         5 | 258 |   0.224806 |   0.263566 |   0.511628 |         -0.0387597  |
| [0.2,0.4)    |         5 | 206 |   0.257282 |   0.286408 |   0.456311 |         -0.0291262  |
| [0.4,0.6)    |         5 |  92 |   0.293478 |   0.184783 |   0.521739 |          0.108696   |
| [0.6,0.8)    |         5 |  20 |   0.2      |   0.3      |   0.5      |         -0.1        |
| [0.8,1.0]    |         5 |   0 | nan        | nan        | nan        |        nan          |

## 事件前特征对比（UP vs DOWN）

| feature                |   n_up |   n_down |        mean_up |      mean_down |    p_ttest |   p_mannwhitney |      cohens_d | significant   |
|:-----------------------|-------:|---------:|---------------:|---------------:|-----------:|----------------:|--------------:|:--------------|
| obi1                   |     60 |       60 |    -0.0027695  |     0.0214742  |   0.649884 |     0.790966    |  -0.0830874   | False         |
| obi3                   |     60 |       60 |    -0.00181957 |     0.00516792 |   0.699525 |     0.912233    |  -0.0706391   | False         |
| obi5                   |     60 |       60 |    -0.00095143 |     0.00256659 |   0.719565 |     0.91848     |  -0.0657092   | False         |
| microprice_dev_ticks   |     60 |       60 |    -0.00138475 |     0.0107371  |   0.649884 |     0.790966    |  -0.0830874   | False         |
| bid_volume1            |     60 |       60 |    71.6        |    74.95       |   0.532602 |     0.549546    |  -0.114275    | False         |
| ask_volume1            |     60 |       60 |    72.2333     |    73.2333     |   0.855172 |     0.819368    |  -0.033397    | False         |
| d_bid_volume1          |     60 |       60 |     0          |     0          | nan        |     1           | nan           | False         |
| d_ask_volume1          |     60 |       60 |     0          |     0          | nan        |     1           | nan           | False         |
| spread_ticks           |     60 |       60 |     1          |     1          | nan        |     1           | nan           | False         |
| volume_delta_1         |     60 |       60 |     0          |     0          | nan        |     1           | nan           | False         |
| turnover_delta_1       |     60 |       60 |     0          |     0          | nan        |     1           | nan           | False         |
| mid_ret_1              |     60 |       60 |     0          |     0          | nan        |     1           | nan           | False         |
| obi_mean_1             |     60 |       60 |    -0.0027695  |     0.0214742  |   0.649884 |     0.790966    |  -0.0830874   | False         |
| obi_change_1           |     60 |       60 |     0          |     0          | nan        |     1           | nan           | False         |
| microprice_dev_mean_1  |     60 |       60 |    -0.00138475 |     0.0107371  |   0.649884 |     0.790966    |  -0.0830874   | False         |
| volatility_1           |      0 |        0 |   nan          |   nan          | nan        |   nan           | nan           | False         |
| update_speed_1         |      0 |        0 |   nan          |   nan          | nan        |   nan           | nan           | False         |
| volume_delta_2         |     60 |       60 |     2          |     1          |   0        |     1.08278e-27 | nan           | True          |
| turnover_delta_2       |     60 |       60 |  4601          |  2300.5        |   0        |     1.08278e-27 | nan           | True          |
| mid_ret_2              |     60 |       60 |     0          |     0          | nan        |     1           | nan           | False         |
| obi_mean_2             |     60 |       60 |    -0.0027695  |     0.0214742  |   0.649884 |     0.790966    |  -0.0830874   | False         |
| obi_change_2           |     60 |       60 |     0.0473997  |     0.0255413  |   0.758128 |     0.786926    |   0.0563535   | False         |
| microprice_dev_mean_2  |     60 |       60 |    -0.00138475 |     0.0107371  |   0.649884 |     0.790966    |  -0.0830874   | False         |
| volatility_2           |     60 |       60 |     0          |     0          | nan        |     1           | nan           | False         |
| update_speed_2         |     60 |       60 |     4          |     4          | nan        |     1           | nan           | False         |
| volume_delta_3         |     60 |       60 |     2          |     1          |   0        |     1.08278e-27 | nan           | True          |
| turnover_delta_3       |     60 |       60 |  4601          |  2300.5        |   0        |     1.08278e-27 | nan           | True          |
| mid_ret_3              |     60 |       60 |     0          |     0          | nan        |     1           | nan           | False         |
| obi_mean_3             |     60 |       60 |    -0.0185694  |     0.0129604  |   0.456279 |     0.496695    |  -0.136468    | False         |
| obi_change_3           |     60 |       60 |     0.0473997  |     0.0255413  |   0.758128 |     0.786926    |   0.0563535   | False         |
| microprice_dev_mean_3  |     60 |       60 |    -0.0092847  |     0.00648019 |   0.456279 |     0.496695    |  -0.136468    | False         |
| volatility_3           |     60 |       60 |     0          |     0          | nan        |     1           | nan           | False         |
| update_speed_3         |     60 |       60 |     6          |     6          | nan        |     1           | nan           | False         |
| volume_delta_5         |     60 |       60 |     4          |     2          |   0        |     1.08278e-27 | nan           | True          |
| turnover_delta_5       |     60 |       60 |  9202          |  4601          |   0        |     1.08278e-27 | nan           | True          |
| mid_ret_5              |     60 |       60 |     0          |     0          | nan        |     1           | nan           | False         |
| obi_mean_5             |     60 |       60 |    -0.0171682  |     0.00514488 |   0.550251 |     0.601504    |  -0.109386    | False         |
| obi_change_5           |     60 |       60 |    -0.0228058  |     0.0305637  |   0.451501 |     0.601504    |  -0.137926    | False         |
| microprice_dev_mean_5  |     60 |       60 |    -0.00858411 |     0.00257244 |   0.550251 |     0.601504    |  -0.109386    | False         |
| volatility_5           |     60 |       60 |     0          |     0          | nan        |     1           | nan           | False         |
| update_speed_5         |     60 |       60 |     5          |     5          | nan        |     1           | nan           | False         |
| volume_delta_10        |     60 |       60 |    10.0333     |    10          |   0.884855 |     0.274752    |   0.0265547   | False         |
| turnover_delta_10      |     60 |       60 | 23081.7        | 23005          |   0.884855 |     0.274752    |   0.0265547   | False         |
| mid_ret_10             |     60 |       60 |     0          |     1          |   0        |     1.08278e-27 | nan           | True          |
| obi_mean_10            |     60 |       60 |    -0.0263348  |    -0.00892426 |   0.526094 |     0.778862    |  -0.116093    | False         |
| obi_change_10          |     60 |       60 |     0.0688486  |     0.00143781 |   0.405143 |     0.537423    |   0.15254     | False         |
| microprice_dev_mean_10 |     60 |       60 |    -0.0131674  |    -0.00446213 |   0.526094 |     0.778862    |  -0.116093    | False         |
| volatility_10          |     60 |       60 |     0          |     0.316228   |   0        |     2.80186e-27 |  -4.37568e+16 | True          |
| update_speed_10        |     60 |       60 |     4          |     4          | nan        |     1           | nan           | False         |
