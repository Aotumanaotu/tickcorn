# 部署指南（新加坡 ECS / 任意 Linux x86_64 服务器）

默认方案：**域名直连（HTTPS）**——caddy 容器做公网入口，自动申请/续期
Let's Encrypt 证书，浏览器直接访问 `https://<域名>` 用账号密码登录。
安全边界：后端 api/gateway 不直接暴露公网；密码 Argon2 哈希；登录接口
按 IP 限流（默认 5 次失败 / 5 分钟）；Refresh Cookie HttpOnly + Secure +
SameSite=strict；CSP/HSTS 安全头。

如需 SSH 隧道方式（无域名场景），见文末「备选：SSH 隧道访问」。

## 域名直连的前置条件

1. **DNS**：域名 A 记录指向服务器公网 IP（如 `47.82.105.121`）。
2. **安全组**：阿里云控制台 → ECS 实例 → 安全组 → 入方向添加规则：
   - 80/443（TCP），源 0.0.0.0/0（80 用于证书签发与 HTTP→HTTPS 跳转）
   - 22（TCP）保留给 SSH 管理
3. `.env` 中配置 `DOMAIN` 与 `ACME_EMAIL`（证书通知邮箱）。

## 从旧版（corn-tick 面板）升级

```bash
cd Bid-Ask_Bounce

# 1. 先停旧栈（此时旧 compose 文件还在；120s 宽限等采集归档）。不要加 -v！
docker compose down

# 2. 拉取新架构
git pull

# 3. 确认 CTP SDK 仍在（gitignored，pull 不会动它）
ls third_party/ctp/v6.7.13_linux64/lib/thostmduserapi_se.so

# 4. 配置环境（必改：ADMIN_PASSWORD、数据库密码、DOMAIN、ACME_EMAIL）
cp .env.example .env && vim .env

# 5. 构建并启动新栈（db + api + gateway + caddy；首次构建需几分钟）
bash scripts/deploy.sh

# 6. 验证
docker compose ps                                        # 四服务 healthy
curl -s https://<域名>/api/v1/health                     # 证书签发需 ~1 分钟
```

说明：

- **若第 1 步前已经 pull 过**：旧 compose 已被替换，用
  `docker compose down --remove-orphans` 移除残留的 corn-tick 容器。
- **旧数据卷保留**：旧 `corn-data` 卷不会被删除（`down` 不带 `-v`）。
  可选迁移历史采集数据到新数据卷（Parquet 分区结构两版一致，自然合并）：

  ```bash
  docker volume ls | grep bid-ask_bounce        # 确认卷名（前缀为目录名小写）
  docker run --rm \
    -v bid-ask_bounce_corn-data:/from \
    -v bid-ask_bounce_microterm-data:/to \
    alpine sh -c 'cp -a /from/raw /to/ 2>/dev/null; \
                   cp -a /from/metadata.db /to/ 2>/dev/null; true'
  # 不要复制 *.local.json（旧面板令牌/凭据文件）
  ```

- **登录方式变化**：旧版面板令牌（dashboard-token）已废弃，浏览器直接
  `https://<域名>` 账号登录（`.env` 中 ADMIN_USERNAME/ADMIN_PASSWORD
  引导的首个管理员）。
- **旧飞书 monitor**：尚未迁移到新 API（计划 F1.5），暂不随 compose 启动。
- **回滚**：`docker compose down --remove-orphans && git checkout e6e90d1
  && docker compose up -d --build`（旧数据卷未动，可完整恢复旧面板）。

## 0. 前置条件

- Linux x86_64（CTP SDK 架构限制）、Docker Engine + Compose 插件
- 已获授权的 SimNow 账号与 CTP v6.7.13 MdApi SDK（见 `third_party/README.md`；
  无 SDK 可用 `simulate` profile 演示）

## 1. 安装

```bash
git clone <repo> && cd Bid-Ask_Bounce
cp .env.example .env
vim .env        # MICROTERM_DB_PASSWORD / ADMIN_PASSWORD 必改
bash scripts/deploy.sh
```

`deploy.sh` 做前置检查（SDK 文件、docker、compose 配置）后
`docker compose up -d --build --wait`。服务组成：

| 服务 | 说明 |
|---|---|
| `db` | timescale/timescaledb:2.17.2-pg16，数据卷 `microterm-pg` |
| `api` | FastAPI + 前端静态（构建于镜像内），仅容器网络内 8000 端口 |
| `gateway` | CTP 行情网关独立进程，与 api 共享 `microterm-data` 卷（socket + Parquet） |
| `caddy` | 公网 HTTPS 入口（80/443），自动证书，反代 api；数据卷 `caddy-data` |

镜像内含自检（`python -m app selftest`，CTP shim 布局校验）。容器健康仅表示
HTTP 服务可用；行情连接状态、错误码、数据更新时间需在网页 System/Overview 确认。

## 2. 验收清单

```bash
docker compose ps                                     # db/api/gateway/caddy healthy
curl -s https://<域名>/api/v1/health                  # {"status":"ok",...}
```

浏览器打开 `https://<域名>`（首次启动 caddy 需约 1 分钟签发证书）：

- [ ] 未登录访问 `/overview` 被重定向到 `/login`
- [ ] admin 登录成功，右上角显示角色徽章；浏览器地址栏为有效证书（无告警）
- [ ] Overview 显示 Gateway 状态（未连接行情时应为 `idle`）
- [ ] System 页填写 SimNow 配置 → Connect → Overview 变为 `streaming`，
      Message/Events 计数增长（真实行情验收，不可用 healthy 状态替代）
- [ ] 连续输错密码 5 次后返回 429（登录限流生效）
- [ ] `docker compose exec api python -m app verify` 通过（归档完整性）

## 3. SimNow 行情接入

登录 → System → Gateway 连接表单：

- **fronts**：行情前置（以 SimNow 登录后官方页面为准，每行一个）
- **broker_id** / **user** / **password**：SimNow 账号
- **instruments**：订阅合约，逗号分隔（如 `C2611, C2701`）
- **remember**：勾选后凭据写入网关数据卷 0600 明文文件（主机管理员可读），
  不勾选则仅存进程内存、容器重启后需重新填写

凭据不进镜像、不进 git、不写服务日志。修改配置前先 Disconnect。

## 4. 运维

```bash
docker compose logs -f api gateway        # 日志
docker compose restart api                # 重启 API（不影响行情采集进程）
docker compose stop gateway               # 优雅停止：网关 finalize Parquet 后退出
docker compose exec api python -m app admin list
docker compose exec api python -m app admin create --username u --password p --role RESEARCHER
docker compose exec api python -m app info
```

`gateway` 停止宽限期 120s（归档落盘）。`microterm-data` 卷 = socket +
Parquet 归档 + 运行时秘密（jwt-secret）；`microterm-pg` 卷 = 业务库与时序数据。

### 备份

```bash
docker compose exec db pg_dump -U microterm microterm | gzip > pg-$(date +%F).sql.gz
docker run --rm -v bid-ask_bounce_microterm-data:/data -v $PWD:/backup alpine \
    tar czf /backup/data-$(date +%F).tar.gz -C /data .
```

Timescale 热层可随时从 Parquet 归档重建；Parquet 是原始真相，优先备份。

### 升级

```bash
git pull
bash scripts/deploy.sh          # 重新构建并滚动重启
```

数据库 schema 由 `init_database` 幂等维护（create_all + 种子），无需手工迁移。

### 故障定位

| 症状 | 排查 |
|---|---|
| https 访问不了/证书告警 | 安全组 80/443 是否放行；DNS 是否解析到本机；`docker compose logs caddy` |
| api 不健康 | `docker compose logs api`（多为 db 未就绪，compose 会自动重试） |
| Overview 显示 gateway offline | `docker compose logs gateway`；socket 卷是否共享 |
| 行情连不上 | System 页看 detail；确认前置地址与 SimNow 账号；`ctp_flow` 目录有连接流水 |
| 数据缺失 | `python -m app info` 看分区；`python -m app verify` 校验哈希 |

## 5. 备选：SSH 隧道访问（无域名场景）

默认部署（APP_ENV=production + Secure Cookie）要求 HTTPS。没有域名时可以
退回隧道模式：在 compose 的 api 服务恢复 `ports: ["127.0.0.1:8800:8000"]`，
`.env` 设 `APP_ENV=dev`（Cookie 不带 Secure 标记），重启后本机执行
`ssh -N -L 8800:127.0.0.1:8800 user@server`，浏览器访问
`http://127.0.0.1:8800`。明文 HTTP 仅建议在回环链路上使用。

## 6. 旧版面板（过渡期）

`python -m app serve`（旧 http.server 面板）与 `app collect` 仍在
`app/legacy/` 保留，供 F2 切换期对照使用；旧 `monitor`（飞书简报）容器
将在 F1.5 迁移到新 `/api/v1/system/status` + service account，此前不再随
compose 启动。
