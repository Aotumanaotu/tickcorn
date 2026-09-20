# MicroTerm — 期货微观结构研究终端

基于 CTP 行情的专业期货研究、微观结构分析与量化交易工作台。核心能力：行情采集 →
Bid-Ask Bounce / True Move 分类 → 实时微观结构指标 → 研究报告 → 历史回放（Replay）。

当前交付：**F0/F1 阶段**（新架构骨架 + 认证 + 实时行情链路 + 前端终端）。
Microstructure Lab（F3）、Reports（F4）、Replay（F5）、SimNow 交易与模型为后续阶段，
架构已预留。

## 架构总览

```
Vue 3 前端 (frontend/)          Dark/Light 双主题研究终端
   │  REST /api/v1 (JWT)   WS /ws/{market,analysis,system}
   ▼
FastAPI (api/)  ── auth(JWT+RBAC) / instruments / market / system / ws hub
   │
   │  Unix Socket (msgpack 帧: 标准事件 + 控制帧)
   ▼
CTP Gateway 独立进程 (gateway/)  ←→  CTP / SimNow MdApi (原生 shim + ctypes)
   │  MarketDataNormalizer → 标准 MarketEvent
   ├─→ Parquet 不可变归档 (storage/parquet_store, 内容寻址 + sha256 + 0444)
   └─→ TimescaleDB 热层 (ticks / microstructure_events / analysis_metrics)
```

关键设计：

- **业务层只见标准事件**（`core/events.py` 契约），任何模块不直接触碰 CTP 原始结构。
- **核心算法资产原样保留**：`core/classifier`（8 标签决策树 + 向量化双实现 + parity
  测试）、`core/statistics`（1-tick 子集口径的 Bounce/True Move Ratio、状态转移、
  条件概率）、`core/features`（无泄漏特征）。
- **双存储**：Parquet = 不可变原始真相（审计/回放/重建源）；TimescaleDB = 查询服务层。
- **实时与离线同源**：实时流和离线分析用同一个分类器，机制上杜绝"面板和报告对不上"。
- 旧版 `http.server` 面板保留在 `app/legacy/`（`app collect/serve` 仍可用），F2 后移除。

## 目录结构

```
frontend/                  Vue 3 + Vite + TS + Pinia 研究终端
src/app/
├── core/                  领域层（零框架依赖）：events / normalizer /
│                          classifier / statistics / features
├── gateway/               CTP Gateway 独立进程：native shim / publisher /
│                          archiver / simulate（无 SDK 模拟源）/ process
├── ingest/                API 侧：socket 协议 / EventBus / IngestClient
├── api/                   FastAPI：app factory / deps / routers
├── ws/                    WebSocket：ticket 鉴权 / 订阅路由 hub
├── auth/                  Argon2 + JWT + Refresh 家族轮换 + RBAC
├── storage/               parquet_store / models(PG) / timescale / db
├── analysis/              pipeline(清洗) / realtime(滚动指标+Regime) /
│                          replay / session_report
├── trading/ modelsvc/     P2/P3 占位（Risk Engine / Transformer 扩展点）
├── legacy/                旧面板与旧采集（过渡期保留）
└── common/                config / runtime / schema / exceptions
```

## 快速开始（本地开发，无需 CTP SDK）

```bash
python3.10 -m venv .venv
.venv/bin/pip install --require-hashes -r requirements.lock
.venv/bin/pip install -e '.[dev]'
bash scripts/build_ctp_shim.sh        # 无 SDK 时跳过；模拟源不需要

# 终端 1：网关（模拟行情，玉米 C2611）
env -u PYTHONPATH .venv/bin/python -m app gateway --simulate -i C2611 --simulate-rate 5

# 终端 2：API + 前端
env -u PYTHONPATH .venv/bin/python -m app init-db --admin-user admin --admin-password admin123
env -u PYTHONPATH .venv/bin/python -m app api --port 8000

# 终端 3（可选）：前端开发模式
cd frontend && npm install && npm run dev    # http://localhost:5173，代理到 :8000
```

打开 `http://127.0.0.1:8000`，用 `admin / admin123` 登录。总览 应显示 Gateway
“行情推送中”（模拟源）。生产模式前端由 API 直接服务（`frontend/dist` 静态目录）。

> 未设 `DATABASE_URL` 时自动使用 `data/app.db`（SQLite）。模拟源标签为
> `simulate`，数据仅用于链路开发验证，不构成市场样本。

## Docker 部署（推荐，域名直连 HTTPS）

依赖 Linux x86_64、Docker Engine 与 Compose 插件。默认方案：**caddy 容器做
公网 HTTPS 入口**（自动申请/续期 Let's Encrypt 证书），浏览器直接访问
`https://www.<域名>`，账号密码登录。

1. **准备**：域名 A 记录指向服务器 IP；阿里云安全组放行 **80/443**（保留 22）；
   从 [SimNow 官方 API 下载页](https://www.simnow.com.cn/static/apiDownload.action)
   获取有权使用的 CTP **v6.7.13 Linux x86_64 MdApi** SDK，按 `third_party/README.md`
   放入本地目录（SDK 不随源码分发；无 SDK 可用 simulate profile 演示）。

2. **配置环境**：

   ```bash
   cp .env.example .env && vim .env   # DOMAIN / ACME_EMAIL / 数据库与 admin 密码
   ```

3. **构建并启动**：

   ```bash
   bash scripts/deploy.sh             # db + api + gateway + caddy
   # 或无 SDK 演示模式：
   docker compose --profile simulate up -d --build --wait
   ```

4. **访问**：浏览器打开 `https://www.<域名>`（首次签发证书约 1 分钟），用 `.env`
   中的 admin 账号登录。公网安全：登录按 IP 限流（5 次失败/5 分钟）、
   Argon2 密码哈希、HttpOnly+Secure Cookie、HSTS/CSP 安全头。

5. **接入 SimNow 行情**：登录终端 → **系统管理** 页 → 填写行情前置 / BrokerID /
   SimNow 账号 / 订阅合约（如 `C2611`）→ Connect。凭据默认只存网关进程内存；
   勾选 "remember" 才写入数据卷 0600 文件（明文，主机管理员可读）。

服务组成：`db`（timescale/timescaledb:pg16，卷 `microterm-pg`）、`api`
（FastAPI + 前端静态，仅容器网络内可达）、`gateway`（CTP 行程进程，与 api
共享 `microterm-data` 卷：socket + Parquet）、`caddy`（80/443 公网入口，
卷 `caddy-data` 存证书）。无域名的 SSH 隧道备选方案见部署指南。

升级 / 备份 / 故障排查见 [部署指南](docs/deployment.md)。

## 认证与权限

- JWT Access Token（30 分钟）+ Refresh Token（14 天，HttpOnly Cookie，SHA-256
  入库，家族轮换 + 重放检测：旧 token 二次出现即吊销全家族）。
- 密码 Argon2id 哈希。首次部署用 `.env` 的 `ADMIN_USERNAME/ADMIN_PASSWORD`
  引导，或 `python -m app admin create --username u --password p --role RESEARCHER`。
- 角色：`ADMIN / RESEARCHER / TRADER / VIEWER`；权限矩阵见 `src/app/auth/rbac.py`
  （网关连接与用户管理仅 ADMIN）。

## 实时数据流与 WebSocket

- 三通道：`/ws/market`（quote + micro 事件，按合约订阅）、`/ws/analysis`
  （滚动指标 + Market Regime）、`/ws/system`（连接状态 + 心跳）。
- 鉴权：REST `POST /api/v1/system/ws/ticket` 换一次性 ticket（30s）连接。
- 订阅协议：`{"action":"subscribe","instrument":"c2611"}`；未订阅不推送。
- quote 类事件允许 conflate（慢客户端丢最旧留最新），micro/analysis 不丢。

## 数据分析（Phase-1 管线，CLI 保留）

```bash
docker compose exec api python -m app info
docker compose exec api python -m app verify
docker compose exec api python -m app analyze -i <INSTRUMENT> --date <YYYY-MM-DD>
docker compose cp api:/app/data/reports ./data/reports
```

输出 `summary.html/md`、CSV 统计、图表与元数据。统计占比的分子分母都限定在
`|ΔLastPrice| = 1 tick` 子集；分段规则（60s 间隙 / 日 / 批次）与既有研究口径一致，
详见原 README 的研究说明。结构化 JSON 报告 + 网页报告页在 F4 交付。

## 本地开发

```bash
.venv/bin/python -m pytest                     # 112+ 测试（无需 SDK）
cd frontend && npm run typecheck && npm run build
env -u PYTHONPATH .venv/bin/python -m app selftest
```

依赖锁定：`requirements.in` → `uv pip compile --generate-hashes -o requirements.lock`
（见 `scripts/lock_dependencies.sh`）。Python >= 3.10 / Linux amd64（CTP SDK 限制）。

## 数据来源与使用边界

仅接入已获授权的官方 SimNow MdApi，不调用交易接口（P2 阶段接入 SimNow 模拟盘），
不绕过认证、地区限制或平台规则。API 测试环境数据不能作为真实市场样本；标准仿真
环境也需核对数据来源、完整性和研究使用授权。跨境部署（如新加坡 ECS）需自行确认
账号服务条款、行情数据许可与所在地使用条件；本项目不提供法律合规保证。详见
[数据与许可说明](docs/data-and-licensing.md)。源码许可证见 [LICENSE](LICENSE)，
不授予第三方 SDK 或行情数据的使用权。

## 路线图

| 阶段 | 内容 | 状态 |
|---|---|---|
| F0 | 架构重组 / Gateway / 实时链路 / 前端终端 / 双主题 | ✅ |
| F1 | JWT 认证 / RBAC / 用户与网关管理 | ✅ |
| F2 | Contract Workspace 完整版（LWC 实时图 / Intelligence Panel / Timeline） | 进行中 |
| F3 | Microstructure Lab（事件钻取 / 分类依据可视化） | 规划 |
| F4 | Research Reports（结构化报告 / 对比 / 导出） | 规划 |
| F5 | Replay（会话回放 / 变速 / 跳转） | 规划 |
| F6 | SimNow 模拟交易（Risk Engine / Orders / PnL） | 规划 |
| F7 | Transformer 模型研究（P(up/flat/down)，仅研究层） | 规划 |
