# OceanBase 连接方式

AgentLens 使用两个独立的数据库连接：

- `AGENTLENS_OB_URL`：任务、调度和诊断器等业务数据。
- `SIGNOZ_OB_URL`：SigNoz 原始遥测和诊断结果；`SIGNOZ_ORG_ID` 指定组织隔离范围。

整套服务部署见 [Docker 部署](https://aivision.mintlify.site/zh-CN/deployment/docker)，独立配置见 [配置参数](configuration.md)。

## 配置连接

```bash
export AGENTLENS_OB_URL='mysql+pymysql://<USER>:<PASSWORD>@<HOST>:2881/agentlens?charset=utf8mb4'
export SIGNOZ_OB_URL='mysql+pymysql://<USER>:<PASSWORD>@<HOST>:2881/signoz_telemetry?charset=utf8mb4'
export SIGNOZ_ORG_ID='<SIGNOZ_ORGANIZATION_ID>'
```

用户名、密码中的 `@`、`:` 等特殊字符需要 URL 编码，例如 `root@tenant` 写为 `root%40tenant`。地址及凭据按实际部署填写，存入仓库外的私有配置。

`AGENTLENS_OB_URL` 为空时，从 `DB_ENGINE`、`DB_HOST`、`DB_PORT`、`DB_USER`、`DB_PASSWORD`、`DB_NAME` 拼接连接串。环境变量优先于 `env/` 模板；部署时可覆盖全部参数。

## SigNoz row adaptation

The SigNoz OceanBase reader converts database `date` and `datetime` values in ordinary
columns, such as `updated_at`, to ISO-8601 strings before constructing a Trace. This retains
fractional seconds and any timezone offset without mutating the source row, so raw Prompt
expressions and Custom Rule inputs can cross JSON boundaries. The existing `timestamp`/`period`
mapping, JSON attribute conversion, and byte decoding retain their established behavior. Other
unsupported Python types are not automatically stringified.

## 业务表迁移

在 `agentlens/` 目录，使用具有迁移权限的数据库账号执行：

```bash
.venv/bin/alembic -c obschema/alembic.ini upgrade head
.venv/bin/alembic -c obschema/alembic.ini current
```

业务表以 `agentlens_` 开头。迁移只管理业务表，SigNoz 遥测表由相应服务管理。生产环境先备份业务库；若运行账号没有 DDL 权限，由数据库管理员执行迁移或审核后的 `obschema/ddl.sql`。

## 验证

确认迁移版本为当前 head、AgentLens 的 `/internal/healthz` 返回成功，再验证任务查询和诊断结果查询。连接失败时核对主机、端口、租户用户名、数据库名及访问权限。

## 事务模式

共享 `make_engine` 在每个 MySQL/MariaDB 新物理连接上，通过公开 DBAPI cursor 显式设置
`SET SESSION autocommit = 0`。OceanBase 协议状态可能与 SQL 实际变量不一致，不能只依赖驱动
根据状态位优化后的 `autocommit(False)`。初始化失败则连接失败，不在活动业务事务内再次设置。

明确的 `AUTOCOMMIT` isolation 或 autocommit=True 请求保留开启；明确 False 则设置为 0。
Engine execution_options 的 isolation 优先于初始 isolation，之后才是 connect_args/URL 模式。
后续 `Connection.execution_options` 和连接归还 reset 仍遵守 SQLAlchemy 合同。原子发布与 ledger
必须使用默认事务连接；显式 AUTOCOMMIT 仅适用于调用方明确需要逐语句提交的操作。
