# 阿里云新加坡 ECS：SSH 隧道部署

## 准备服务器

使用 Ubuntu/Debian 的 x86_64 实例。本项目所适配的 SDK 是 Linux amd64，不支持直接在 ARM 实例上运行。采集与报告分析的资源需求不同：先完成本机验证，再根据实际订阅量、采集天数和报告大小监测磁盘、内存。

按 [Docker Ubuntu 官方安装文档](https://docs.docker.com/engine/install/ubuntu/) 或 [Debian 文档](https://docs.docker.com/engine/install/debian/) 安装 Docker Engine 与 Compose 插件。执行 `docker version`、`docker compose version`，确认当前 SSH 用户可访问 Docker。

在阿里云安全组中，仅向自己的可信 IP 放行 SSH。无需开放 8800。出站允许访问获授权的官方行情前置；具体连接可用性、服务时间以平台说明和实际测试为准。参考 [阿里云安全组文档](https://www.alibabacloud.com/help/zh/ecs/user-guide/start-using-security-groups)。不要把部署位置作为绕过服务限制的手段。

## 构建与启动

从自己的源码仓库克隆项目，在仓库目录操作。将自行合法获取的 SDK 按 `third_party/README.md` 放好，然后运行：

```bash
bash scripts/deploy.sh --check
bash scripts/deploy.sh
docker compose ps
docker compose logs --tail=100 corn-tick
```

构建在容器内编译原生包装层，执行 `selftest`。运行容器使用 UID/GID 10001、只读根文件系统、临时目录和持久数据卷。无需 `.env` 或阿里云 API Key；不会调用阿里云控制面，也不会创建计费资源。

Compose 默认将端口绑定到 `127.0.0.1:8800`，使用命名卷 `corn-data`。不要用 `docker compose down -v`，它会删除数据卷。不要随意更改 Compose 项目名或仓库目录名，否则 Compose 可能使用另一个卷；用 `docker compose ls` 和 `docker volume ls` 核对。

首次启动自动创建 `/app/data/dashboard-token.local.json`。通过服务器终端读取：

```bash
docker compose exec -T corn-tick python -c 'import json; print(json.load(open("/app/data/dashboard-token.local.json"))["token"])'
```

令牌仅粘贴到网页的密码输入框，勿加入 URL、Git、截图或聊天。网页登录验证失败不会返回任何采集信息。

在自己的电脑执行，连接目标必须替换成自己的 SSH 用户和服务器：

```bash
ssh -N -L 8800:127.0.0.1:8800 <SSH_USER>@<SERVER_HOST>
```

保持隧道窗口开启，访问 `http://127.0.0.1:8800`。面板令牌每次刷新需要重新填写。填写采集表单时：

- 合约：从官方终端确认可用合约，支持大小写输入；发送订阅时按交易所规则处理大小写。
- BrokerID、账号、密码、行情前置：按自己的官方接入说明填写，不要填交易前置。
- 环境：选择与前置对应的 API 测试或标准仿真环境；此选择会记录进数据来源。
- 默认不记住凭据；若勾选则以明文保存到服务器私有数据卷，依赖主机和磁盘访问控制。服务器管理员仍可读取。

## 上线验收

1. `docker compose ps` 显示 healthy；宿主机 `ss -lnt` 中仅 `127.0.0.1:8800` 监听。
2. 未登录访问 `/api/state` 返回 401，根页面仅显示登录界面。
3. 通过 SSH 隧道填写授权账号，启动后观察连接是否登录成功、是否订阅成功、更新时间是否持续变化。healthy 不等于接到了行情。
4. 交易时段采集一小段，然后停止；再次启动、停止同一天的数据，`info` 应能列出数据，`verify` 应通过。
5. 生成分析报告并下载阅读；确认来源类别、样本量与预期一致。测试环境仅用于链路验证。
6. 采集中执行 `docker compose stop`，应在宽限期内完成停止和落盘；重新启动后从网页重新开始采集。

自动测试使用合成数据及本机连接，不替代服务器到官方前置的真实验收。没有授权账号的实测结果时，不能声称真实行情链路已验收。

## 停止、升级与备份

```bash
docker compose stop
# 复制到已被 Git 忽略的 data 目录；停止期间 SQLite 文件保持一致。
mkdir -p data/backup
docker compose cp corn-tick:/app/data/raw ./data/backup/raw
docker compose cp corn-tick:/app/data/metadata.db ./data/backup/metadata.db
# metadata.db-wal / metadata.db-shm 若存在，也一并复制。
```

推荐完整备份持久卷并保留 SQLite 的数据库和 WAL 文件一致快照；完整卷备份可能包含账号密码、面板令牌、CTP 会话文件，必须私有存放、限制权限，不能上传 Git 或公共对象存储。普通报告分享前也应检查是否包含实际合约、日期和研究元数据。

升级先在网页停止采集并备份，执行 `git pull --ff-only`，再运行 `bash scripts/deploy.sh`。数据卷保留，容器启动后不会自动登录或开始采集。旧版 `./data:/app/data` 绑定目录不会自动导入新命名卷：迁移时停止旧服务，备份旧目录，再按 Docker 卷迁移流程复制，并将卷内所有权设为 `10001:10001`；确认 `info`、`verify` 后再使用。

需要轮换面板令牌时先停止容器，通过受控的本机卷维护删除或替换 `dashboard-token.local.json`，下次启动生成新令牌。勿删除原始数据。要清除已保存的 SimNow 凭据，先停采集，在网页点击“清除账号密码”。磁盘快照和旧备份中的副本需要单独按自己的保留策略处理。

## 故障定位

- 构建缺 SDK：核对第三方说明中的文件路径和版本，勿下载来源不明的库。
- 无法连接前置：确认填的是行情端口、服务开放时段、账号有效性、ECS 出站规则；不使用绕过认证或限制的手段。
- 登录成功但没数据：核对有效合约和交易时段；查看网页错误码及数据更新时间。
- 停止失败或磁盘不足：保留 `raw/**/_staging/`。释放空间后，在没有采集进程的情况下运行 `docker compose exec corn-tick python -m app finalize`，再 `verify`。磁盘写入失败时系统停止采集并报告失败，不能保证尚未落盘的内存数据可恢复。
- 强制断电或 SIGKILL：只能恢复已落盘的 staging 分片；默认最多约 5 秒或 5000 行触发一次缓冲写入，不能承诺零丢失。
- 大范围分析：停止采集后执行，提前检查内存；采集落盘可流式处理，分析仍需把选定数据加载到内存。

Docker 的停止宽限期为 120 秒；见 [Compose 服务配置](https://docs.docker.com/reference/compose-file/services/)。数据极大导致停止超时，应扩大本地部署覆盖配置并重新验收，勿直接强杀后假定数据完整。

## 监控与每日简报

`ops/monitor/` 提供一个独立的 Node 服务，定时检查面板可用性、采集运行状态、行情连接、当日数据量与磁盘，并通过飞书官方机器人 SDK 每天推送简报。它挂在 Compose 的 `monitor` profile 下，普通 `docker compose up` 不会启动。飞书凭证、发送时间等直接在网页控制台的“每日简报”里填写（存放在数据卷本地受限文件，monitor 自动重读，无需重启或改 `.env`）。配置与启动步骤见 [ops/monitor/README.md](../ops/monitor/README.md)。该服务只读，不会自动重启采集。
