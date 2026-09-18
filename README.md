# 玉米期货 1-Tick 微观结构分析系统（第一阶段）

研究中国玉米期货（DCE，`C` 品种）CTP 快照行情中 1-Tick 价格跳动的构成：
**Bid-Ask Bounce** vs **Genuine Quote Move**，并产出可复现的研究报告。

> **术语声明（论文用）**：CTP 行情是**快照型行情（Market Snapshot）**，
> 不是逐笔订单流（Tick-by-Tick Order Flow）。两个快照之间的订单、撤单、
> 成交不可观察。因此 Bid-Ask Bounce 的判定必须分置信度：
> `HIGH_CONFIDENCE_BOUNCE` / `LIKELY_BOUNCE` / `GENUINE_QUOTE_MOVE` /
> `AMBIGUOUS`。

---

## 阶段范围

```
第一阶段（本代码库，已完成）:
  CTP 行情采集 → Parquet 原始数据 → 历史 Replay → 跳点分类
  → Bounce/Genuine Move 统计 → OBI/Microprice 分析 → Transition Matrix
  → HTML 研究报告 + 实时数据面板

第二阶段（未开始）: 真实报价移动的短周期预测（见 src/app/model/）
第三阶段（未开始）: 交易模拟与 Walk-Forward 回测（见 src/app/backtest/）
```

第一阶段完成后暂停，等待基于真实数据的分析结果决定后续。

---

## 快速开始

### 1. 环境

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .            # 或 pip install -e ".[dev,accel,model]"
bash scripts/build_ctp_shim.sh   # 编译 CTP shim（需要 g++）
python -m app selftest       # 自检: 配置 / shim / 分类器 / 存储目录
```

> 注意：如机器上有 ROS 等 PYTHONPATH 污染，命令前加 `env -u PYTHONPATH`。

### 2. 采集 SimNow 行情（需要合法账号）

前置条件（均通过合法途径获取）：
1. 在 **simnow.com.cn** 免费注册 SimNow 账号；
2. 登录后从官网获取**当前有效**的行情前置地址（地址会轮换），
   更新 `config/settings.yaml` 的 `ctp.fronts` 或用 `--front` 传入；
3. 本机网络需允许出站 TCP 到前置 `host:port`（测试：
   `timeout 3 bash -c "echo > /dev/tcp/<ip>/<port>" && echo OPEN`）。

```bash
export SIMNOW_USER="你的账号"        # 不要把密码写进代码或配置
export SIMNOW_PASSWORD="你的密码"
python -m app collect --instrument C2701 --instrument C2705   # 默认附带实时面板
# 面板: http://127.0.0.1:8800
```

说明：
- SimNow 标准环境仅交易时段有数据（玉米夜盘 21:00-23:00，日盘 09:00-15:00）；
- Ctrl+C 停止时自动合并 staging → 不可变 `snapshots.parquet`（sha256 入库、
  文件权限 0444）；
- 断线自动重连、重登录、重订阅；重复/异常行情计数但不丢弃（原始层忠实保存）。

### 3. 分析与报告

```bash
python -m app analyze --instrument C2701 --date 2026-09-18
# 输出 data/reports/<instrument>_<day>_run-xxxx/:
#   summary.html / summary.md          三大核心问题 + 全部表格
#   event_statistics*.csv              事件统计（按日/时段/开收盘窗口）
#   transition_matrix.csv              P(next|current) 5态矩阵（+8态细化版）
#   obi_probability.csv                P(QUOTE_UP/DOWN | OBI 区间) × 视野
#   feature_statistics.csv             事件前特征 UP vs DOWN 显著性检验
#   preevent_features.csv, intraday_time_bins.csv, run_metadata.json
#   figures/*.png                      价格/盘口、Bounce Ratio、OBI 等图
# data/reports/latest -> 最新一次运行
```

### 4. 其他命令

```bash
python -m app replay  -i C2701 --date 2026-09-18 --speed realtime --dashboard
python -m app finalize                       # 手动合并遗留 staging
python -m app info                           # 查看已有数据/批次/运行
python -m app verify                         # 原始文件 sha256 完整性校验
python -m app dashboard --port 8800          # 独立面板（尾随 live 文件）
env -u PYTHONPATH .venv/bin/python -m pytest # 运行测试（54 项）
```

---

## 数据与可复现性

```
data/
  raw/instrument=C2701/trading_day=2026-09-18/snapshots.parquet   # 不可变
  processed/ .../clean-<hash>.parquet                             # 清洗缓存
  features/  reports/  models/  live/  ctp_flow/
  metadata.db              # SQLite: 批次/文件索引/实验参数/分析运行
```

- **原始层**：完整 CTP DepthMarketData（48 字段含五档、涨跌停、带价等）
  + 采集簿记（sequence_id、local_receive_time_ns、trading_session、
  raw_source、batch_id）。数值原样保存（含 DBL_MAX 哨兵），落盘后
  chmod 0444 + sha256 注册，永不修改。
- **清洗层**（processed）：哨兵→NaN、精确去重、按交易所时间排序、
  无效盘口行剔除（计数入报告）。
- **实验记录**：每次 analyze 把完整参数（分类器模式、tick、配置哈希、
  输入文件哈希、代码版本）写入 `run_metadata.json` 与 SQLite。

## 跳点分类规则（严格模式，默认）

对每个相邻快照对 (t-1 → t)，db/da/dl/dm 为 bid/ask/last/mid 变化（tick 数）：

| 条件 | 标签 |
|---|---|
| db=da=dl=0 | `NO_MOVE` |
| db=da≠0（盘口整体平移） | `GENUINE_QUOTE_MOVE_UP/DOWN` |
| 盘口不变，Last 在 Bid↔Ask 两端切换 | `HIGH_CONFIDENCE_BOUNCE_UP/DOWN` |
| 盘口不变，Last ±1 tick 且在盘口内 | `LIKELY_BOUNCE_UP/DOWN` |
| Last ±1 tick，mid 不变，盘口非同向整体移动 | `LIKELY_BOUNCE_UP/DOWN` |
| 同向不同幅 / 单边移动使 mid≥1 tick | 严格: `AMBIGUOUS`（宽松模式: GENUINE） |
| 其余复杂变化 / 盘口无效 / 非法价格 | `AMBIGUOUS`（带 reason） |

核心比率：`bounce_ratio = (HIGH+LIKELY) / 1-tick LastPrice 变化数`。
分类器有成对（参考实现）与向量化两套实现，随机化一致性测试保证完全一致。

## 云服务器部署（Docker，推荐：阿里云新加坡）

企业内网通常拦截 CTP 前置端口；新加坡/香港服务器出站自由，适合采集。
**所有运维配置（账号/密码/前置/合约）都在网页面板输入**，不用编辑 YAML。

### 1. 推送到 GitHub（建议私有仓库）

```bash
git init && git add . && git commit -m "Phase 1: collect/replay/classify/report"
# 在 github.com 建好私有仓库后:
git remote add origin git@github.com:<你>/<repo>.git
git push -u origin main      # (或 master)
```

### 2. 服务器上部署（Ubuntu 示例）

```bash
# 装 Docker（已装可跳过）: 官方脚本 install.docker.com 或 apt install docker.io docker-compose-plugin
git clone https://github.com/<你>/<repo>.git && cd <repo>
echo "DASHBOARD_TOKEN=<随机长字符串>" > .env     # 面板访问令牌
docker compose up -d --build
docker logs -f corn-tick                         # 看到 listening 即成功
```

### 3. 放行端口（阿里云安全组，二选一）

- **SSH 隧道（最安全，推荐）**：安全组不开 8800；本地执行
  `ssh -L 8800:localhost:8800 root@<服务器IP>`，然后访问
  `http://localhost:8800/?token=<DASHBOARD_TOKEN>`
- **直连**：安全组入方向放行 8800，来源限制为你的本地公网 IP/32，
  访问 `http://<服务器IP>:8800/?token=<DASHBOARD_TOKEN>`

### 4. 网页操作

1. 打开面板 → 「采集控制」区
2. 填写：合约（如 `C2701 C2705`）、SimNow 账号/密码、前置地址（已预填官方
   7×24 与标准环境地址，可从 simnow.com.cn 获取最新）
3. 可勾选「记住账号密码」（存于服务器 `data/settings.local.json`，0600 权限，
   永不进入 git/日志/数据库；不勾选则仅存内存，重启后需重输）
4. 点「开始采集」→ 下方实时面板出数据（交易时段）；「停止采集」自动
   finalize 成不可变 parquet 并登记 sha256

### 5. 数据取回与本地分析

```bash
# 本地机器:
rsync -avP root@<服务器IP>:<repo>/data/raw ./data/
rsync -avP root@<服务器IP>:<repo>/data/metadata.db ./data/
python -m app analyze --instrument C2701 --date <交易日>
```

服务器 1核2G 足够（采集+面板资源占用极低）；跨境到 SimNow 的链路波动由
CTP 自动重连 + 本系统重登录/重订阅兜底。SimNow 标准环境仅交易时段有行情
（玉米夜盘 21:00–23:00、日盘 09:00–15:00）；7×24 环境供链路联调，
 其数据为模拟撮合，**论文统计请用标准环境真实行情**。

## 合规与数据来源

- CTP API：用户自备的官方 SimNow PC API 包（v6.7.13，md5 已核对）；
- 行情来源：仅官方 SimNow（上期技术仿真平台），需自行注册账号并获取
  当前前置地址；**系统不包含任何绕过或非官方数据通道**；
- 凭证仅通过环境变量/命令行/网页表单传入本地进程，不写入仓库与数据库
  （SQLite 仅记录脱敏用户名，如 `13***89`；网页记住的密码仅存服务器本地
  `data/settings.local.json`，0600 权限且被 .gitignore 排除）。

## 目录结构

```
config/          settings.yaml, instruments.yaml（tick size 等来自配置）
scripts/         build_ctp_shim.sh
src/app/         common/ storage/ collector/ dashboard/ replay/ classifier/
                 features/ statistics/ visualization/ report/ model/ backtest/
third_party/ctp/ 官方 API 头文件与库（v6.7.13 linux64）
tests/           54 项测试（分类器/特征/存储/转移矩阵/端到端/采集与面板）
```

## 已知限制（第一阶段）

- SimNow 标准环境非交易时段无行情；7×24 环境数据为模拟，不建议用于论文统计；
- 本机网络若拦截前置端口（常见于企业网），需放行后在能连网的机器上采集，
  回放/分析/报告全部可离线进行；
- CTP 快照频率约 0.5s/条；"0.5s/1s 视野" 必须用真实时间戳计算（已实现），
  不得假设均匀到达。
