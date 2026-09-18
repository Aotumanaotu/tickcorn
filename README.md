# 玉米期货快照微观结构研究

分析 CTP 行情快照里的成交价变化，区分买卖价反弹（Bid-Ask Bounce）和盘口整体移动（Genuine Quote Move）。提供行情采集、不可变 Parquet 存储、回放、特征统计、HTML 报告和网页采集控制。

当前交付范围是第一阶段的研究基础设施。`model/` 和 `backtest/` 是后续规划，尚未实现预测模型、收益回测或下单功能。CTP 快照无法观察两次更新之间的全部交易，因此分类有置信度，研究结果不等于可实现收益。

## 新加坡 ECS 部署

使用 Linux x86_64、Docker Engine 和 Compose 插件。默认方案为 **SSH 隧道访问**；服务器不开放 8800 公网端口。完整安装、验收、备份和升级步骤见 [部署指南](docs/deployment.md)。

1. 克隆项目。仓库不提供运行账号、密码、API Key、行情前置或默认订阅合约。
2. 从 [SimNow 官方 API 下载页面](https://www.simnow.com.cn/static/apiDownload.action) 获取有权使用的 CTP **v6.7.13 Linux x86_64 MdApi** SDK，按 [第三方说明](third_party/README.md) 放入本地目录。SDK 不随源码分发，不会自动从非官方站点下载。
3. 在服务器执行：

   ```bash
   bash scripts/deploy.sh
   ```

4. 在服务器终端读取首次启动自动生成的面板令牌：

   ```bash
   docker compose exec -T corn-tick python -c 'import json; print(json.load(open("/app/data/dashboard-token.local.json"))["token"])'
   ```

5. 在自己的电脑建立隧道（替换占位符）：

   ```bash
   ssh -N -L 8800:127.0.0.1:8800 <SSH_USER>@<SERVER_HOST>
   ```

6. 打开 `http://127.0.0.1:8800`，在网页输入面板令牌，然后填写 SimNow 账号、密码、BrokerID、当前官方行情前置、环境类别和有效合约，点击“开始采集”。

账号密码默认只存在进程内存；勾选“记住”才写入服务器数据卷中的本地文件，权限为 `0600`，**不是加密存储**。取消勾选并保存会删除磁盘中的账号密码；“清除账号密码”也会清除当前配置中的凭据。修改配置前先停止采集。面板令牌只在浏览器页面内存中使用，不写入网址、浏览器持久存储或服务日志。

容器重启后保持空闲，需从网页重新开始采集；未记住的凭据需重新填写。容器健康状态仅表示 HTTP 服务可用，行情连接状态、错误码、数据更新时间需要在面板确认。

## 数据分析

先在网页停止采集，等待数据保存完成。以下操作均在容器里执行，合约与日期替换为已采集的数据：

```bash
docker compose exec corn-tick python -m app info
docker compose exec corn-tick python -m app verify
docker compose exec corn-tick python -m app analyze -i <INSTRUMENT> --date <YYYY-MM-DD>
docker compose cp corn-tick:/app/data/reports ./data/reports
```

输出包含 `summary.html`、`summary.md`、CSV 统计表、图表和分析元数据。报告在本地浏览器打开。实时面板目前用于采集与监控，历史分析通过上述命令执行。

原始数据保存在持久数据卷：`raw/instrument=.../trading_day=.../`。首次归档为 `snapshots.parquet`，同日再次采集追加 `snapshots-<sha256>.parquet`，不修改旧文件。所有分片共同参与回放、清洗和哈希校验。SQLite 记录批次和文件摘要；账号密码不写入数据库。原始快照保留 CTP 字段原值，清洗层才处理哨兵、重复和无效报价。

统计占比的分子、分母都限定在 `|ΔLastPrice| = 1 tick` 子集。报价整体移动事件的总数另外统计。分析在交易日、批次、时段变化或超过 60 秒的数据间隔处分段，避免连接不连续观察；不跨越 `AMBIGUOUS` 计算相邻状态转移，条件概率排除未来观察窗口不完整的样本。当前报告视野单位为**快照数**，尚未实现按真实秒数定义的条件概率视野。

## 本地开发

```bash
python3.10 -m venv .venv
.venv/bin/pip install --require-hashes -r requirements.lock
.venv/bin/pip install -e '.[dev]'
bash scripts/build_ctp_shim.sh
env -u PYTHONPATH .venv/bin/python -m app selftest
env -u PYTHONPATH .venv/bin/python -m pytest
env -u PYTHONPATH .venv/bin/python -m app serve
```

无 SDK 时可以运行纯 Python 分析测试；需要原生 CTP 的用例会跳过。容器构建会实际编译 SDK 包装层并验证结构体布局。依赖清单锁定 Python 3.10 / Linux amd64 的版本和安装包哈希；基础镜像及系统安全更新仍需定期维护。自动检查见 `.github/workflows/checks.yml`。

## 数据来源与使用边界

仅接入已获授权的官方 SimNow MdApi，不调用交易接口，不绕过认证、地区限制或平台规则。前置地址和开放时段应以登录后的官方页面为准，不在代码中固定。API 测试环境的数据不能作为真实市场样本；标准仿真环境也需核对实际数据来源、完整性和研究使用授权，报告记录用户选择的来源，**不自动认证来源真实性**。

新加坡部署不自动获得跨境访问、存储或再分发许可。操作者需确认账号服务条款、行情数据许可和所在地使用条件；本项目不提供法律合规保证。详见 [数据与许可说明](docs/data-and-licensing.md)。源码许可证见 [LICENSE](LICENSE)，不授予第三方 SDK 或行情数据的使用权。
