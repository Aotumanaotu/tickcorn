# corn-tick 监控 / 每日简报

独立的 Node 服务，定时检查玉米快照采集服务的运行状态与当日数据量，并通过**飞书官方机器人 SDK**（`@larksuiteoapi/node-sdk`）每天推送简报。

它只读，不写数据、不改配置，与主服务通过同一 compose 网络和数据卷协作。

## 监控内容

| 项目 | 来源 | 说明 |
| --- | --- | --- |
| 服务可用性 | `GET /healthz` | 面板 HTTP 是否存活、延迟 |
| 采集进程 | `GET /api/state` → `manager.running` | 是否在运行中 |
| 行情连接 | `GET /api/state` → `connection` | `logged_in` / `login_failed` / `waiting_front` 等 |
| 实时行情 | `/api/state` → `instruments` | last、买卖一、更新延迟、msg/min、反弹比 |
| 当日数据 | 扫描 `data/raw/instrument=*/trading_day=*` | 文件数、体积、最新时间、staging 残留 |
| 磁盘 | `statfs(/data)` | 剩余空间、使用率（默认 85% 告警）|

默认发送时间 `08:00,12:30,21:30`（东八区），可配置。

示例消息：

```
【玉米快照采集】状态简报 21:05

总体: ✅ 正常
服务: ✅ HTTP ok (8ms)
采集: 🟢 运行中 · batch batch-8e94ad956d6f
连接: 🟢 logged_in · trading_day=20260918
CTP交易日: 20260918

合约:
  • C2611  last=2345  bid/ask=2344/2345
     更新 21:04:59  延迟 0.4s  58 msg/min  快照 234567
     反弹比 63.2% / 真实移动比 21.0%

今日数据 (交易日 20260918 / 2026-09-18):
  ✅ C2611  2 文件 · 12 MB · 最新 2026/9/18 21:05:01
磁盘: ✅ /data 剩余 38 GB / 60 GB (已用 37%)
```

## 一、准备飞书机器人

1. 打开 [飞书开放平台](https://open.feishu.cn/app) → 创建**企业自建应用**。
2. 应用能力里添加**机器人**。
3. 权限管理，开通发送消息相关权限（至少 `im:message`，通常再加 `im:message:send_as_bot`）。
4. 创建版本并发布，等管理员审批通过。
5. 把机器人拉进一个群（或用你自己的账号作为接收方）。
6. 拿到 `chat_id`（推荐）：用 `tenant_access_token` 调接口列出机器人所在群：

   ```bash
   TOKEN=$(curl -s -X POST \
     https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal \
     -H 'Content-Type: application/json' \
     -d '{"app_id":"cli_xxx","app_secret":"xxx"}' | jq -r .tenant_access_token)

   curl -s "https://open.feishu.cn/open-apis/im/v1/chats?page_size=20" \
     -H "Authorization: Bearer $TOKEN" | jq '.data.items[] | {name, chat_id}'
   ```

   也可以不用群，直接把 `FEISHU_RECEIVE_ID_TYPE` 设为 `email`，`FEISHU_RECEIVE_ID` 填你的飞书邮箱。

7. 剩下的凭证**在网页面板里填**（推荐），不需要改 `.env`：见下方步骤三。

## 二、启动 monitor 容器（推荐）

在仓库根目录：

```bash
docker compose --profile monitor build monitor
docker compose --profile monitor up -d monitor
docker compose --profile monitor logs -f monitor
```

- 该服务挂在 `monitor` profile 下，**普通的 `docker compose up` 不会启动它**，不影响原部署。
- 同网络内通过服务名 `corn-tick:8800` 访问面板，并只读挂载 `corn-data` 卷以读取令牌、数据和面板写入的设置。
- 此时还没填飞书凭证也没关系，日志会提示缺失，补齐后即可发送。

## 三、在网页面板里填写（推荐）

打开面板（SSH 隧道 + 令牌登录），在**“每日简报（飞书机器人）”**一栏填写：

| 输入框 | 填什么 |
| --- | --- |
| 飞书应用 App ID | 开放平台的 `cli_...` |
| App Secret | 开放平台的 App Secret（留空 = 保持已保存值） |
| 接收方类型 / 接收方 ID | 群聊填 `chat_id`（推荐）；也可用 `email` 等 |
| 每天发送时间 | `HH:MM` 逗号分隔，如 `08:00,12:30,21:30` |
| 简报标题 | 自定义标题 |
| 启用简报 | 勾选后才会按时间发送；**“发送测试消息”不受此限制** |

点“保存简报设置”，再点“发送测试消息”，约 20 秒内 monitor 容器会向飞书推送一条测试消息，确认链路。

配置保存在数据卷里的 `monitor-settings.local.json`（0600，明文），monitor 每次轮询自动重读，**改完即时生效，无需重启容器**。优先级：**面板设置 > `.env` 环境变量**。

## 四、不用 Docker / 无面板（可选）

需要 Node ≥ 18.15（`--env-file` 需 ≥ 20.6）：

```bash
cd ops/monitor
npm install
cp .env.example .env && vi .env
node --env-file=.env src/index.js --check   # 检查
node --env-file=.env src/index.js            # 常驻定时
```

## 命令行调试

```bash
# 只检查并打印报告，不发送
docker compose --profile monitor run --rm monitor node src/index.js --check

# 立即发一条测试消息
docker compose --profile monitor run --rm monitor node src/index.js --once

# 干跑：打印将要发送的内容，不真正发送
docker compose --profile monitor run --rm monitor node src/index.js --once --dry-run
```

## 配置项

面板里能填的项（`FEISHU_*`、`REPORT_TIMES`、`ALERT_ONLY`、标题、时区）以**面板设置为准**；环境变量是回退值，适用于无面板/纯 headless 部署。

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `FEISHU_APP_ID` / `FEISHU_APP_SECRET` | 无 | 自建应用凭证，必填 |
| `FEISHU_RECEIVE_ID` | 无 | `chat_id` / `open_id` / `email` 等，必填 |
| `FEISHU_RECEIVE_ID_TYPE` | `chat_id` | 接收方类型 |
| `FEISHU_DOMAIN` | `feishu` | 国际版填 `lark` |
| `REPORT_TIMES` | `08:00,12:30,21:30` | 每天发送时刻（容器本地时间） |
| `TZ` | `Asia/Shanghai` | 时区 |
| `ALERT_ONLY` | `false` | `true` 时仅异常才发送 |
| `SEND_ON_START` | `false` | 启动即发一条 |
| `DASHBOARD_URL` | `http://corn-tick:8800` | 面板地址 |
| `DASHBOARD_TOKEN_FILE` | `/data/dashboard-token.local.json` | 令牌文件（与主服务共用数据卷） |
| `DASHBOARD_TOKEN` | 空 | 直接指定令牌，优先级高于文件 |
| `DATA_DIR` | `/data` | 数据目录（扫描 `raw/` 与磁盘占用） |
| `INSTRUMENTS` | 空 | 只看指定合约，逗号分隔；空=全部 |
| `DISK_WARN_PERCENT` | `85` | 磁盘使用率告警阈值 |
| `DRY_RUN` | `false` | `true` 时不真正发送 |

## 注意

- 本服务**不会**自动重启采集。容器重启后主服务会回到空闲，简报会以 ⚠️ 提示“采集未在运行”，需你从网页重新开始。
- 面板令牌轮换后，若主服务已生成新令牌文件，本服务会自动读取到；若用 `DASHBOARD_TOKEN` 覆盖，记得同步更新。
- 飞书消息里会包含合约、交易日等信息，注意群成员可见性。
