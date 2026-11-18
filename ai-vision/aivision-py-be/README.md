# AI Vision Django Backend

Django REST API 后端，提供邮箱账号、空间管理、Agent 接入、SigNoz 与 Langfuse 查询和诊断代理。

## 本地运行

整套服务按 [Docker 部署](https://aivision.mintlify.site/zh-CN/deployment/docker) 启动。单独调试后端时，在当前目录执行：

```bash
uv sync --frozen --python 3.12
# 在 shell 或私有环境文件中配置 DB_*、SECRET_KEY 和部署地址
.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver 127.0.0.1:8001
```

`AIVISION_ENV` 选择 `.env.development`、`.env.prepub`、`.env.gray` 或 `.env.prod`；shell 环境变量优先于文件。提交的文件仅为模板，账号与凭据存放在仓库外。前端使用 `VITE_DEV_API_TARGET` 指向后端。

镜像默认入口每次启动先执行幂等迁移、平台配置与 seed，再启动 Gunicorn：一个 `gthread` worker、
四个线程，绑定 `BE_PORT`（默认 8001），请求超时 240 秒、退出排空上限 45 秒。
容器停止宽限应至少为 60 秒；超过排空上限的请求可能中断。显式入口参数仍可覆盖服务命令。
`GET /api/v1/health` 只检查 HTTP 存活；匿名 `GET /api/v1/ready` 执行只读 `SELECT 1`，
数据库可用时返回 `200 {"status":"ok"}`，否则返回 `503 {"status":"unavailable"}`，不输出连接错误或凭据。
MySQL 探针使用现有驱动与连接配置创建独立连接，连接、读取、写入 socket 超时分别为 1 秒，
不修改业务查询的超时。
就绪不代表 SigNoz、网关和诊断全链路可用；需另用写入和回读验证。

## 账号和数据源

浏览器使用邮箱注册、密码登录和 HttpOnly 会话 Cookie。空间与用户通过 UUID 关联，团队成员通过邮箱邀请加入。

数据源配置保存在数据库。SigNoz 用于遥测查询和诊断；Langfuse 保留查询、分享、项目绑定和凭证管理。

`bootstrap_signoz_integration` 在注册 SigNoz 后立即保存非敏感组织 ID，再配置内部查询 key
和数据源；后续失败可重跑命令，完成前 `configured` 保持 false。
部署发现使用 `signoz_integration_status --print-org --require`，只在全部配置完成时输出组织 ID。
内部 key 仅保存在数据源配置中。已有 SigNoz 但后端集成状态丢失时可传 `--org-id`。
源码宿主机进程仍保留已有的 Docker metastore 恢复回退；它需要宿主机可访问 Docker 和对应容器。
容器部署不挂载 Docker socket，也不依赖此回退，正常重试使用持久化组织断点。

Langfuse 扩展开通接口使用 `LANGFUSE_INTERNAL_AUTH_VALUE`、`LANGFUSE_INTERNAL_API_TIMEOUT`。这些变量只影响配置了该扩展接口的项目，不改变已有 Basic 查询凭证。

模型连接测试 `POST /api/v1/spaces/{space_id}/diagnose/model-connection/{uuid}/test`
会同步等待 AgentLens 的单次模型 probe。该操作的上游 timeout 为 195 秒，给模型 probe
的 180 秒 HTTP 阶段 timeout 留出转发余量；其他诊断代理请求保持 15 秒。HTTP 阶段 timeout
不是整个操作的绝对时限，客户端和外层代理也需允许这段等待时间。
若 Django 等待 AgentLens 超时，返回 HTTP 504（业务码 `5001`）；AgentLens 正常返回的
连接 `available` / `unavailable` 状态仍原样放在成功响应中。

模型基础诊断验证使用独立接口
`POST /api/v1/spaces/{space_id}/diagnose/model-connection/{uuid}/validate`。
接口沿用会话与 CSRF 校验，仅允许有模型管理权限的成员操作同空间已保存连接；
请求体只接受可选 `generation_options`，空间由代理注入。可调参数为
`max_tokens`（整数 256–8192）、`enable_thinking`（布尔或 null）、
`temperature`（0–2）、`reasoning_effort`（固定枚举、整数 1–100 或 null）和
`request_timeout_sec`（10–60 秒）；默认值由 AgentLens 定义。

验证代理的 HTTP 阶段 timeout 为 195 秒，不代表绝对墙钟时限。成功响应含可空的
`diagnostic_validation`，通过固定正反例记录诊断输出的检查结果；模型连接列表、详情、
创建、更新和旧连通性测试返回的该字段同样经过嵌套字段白名单筛选。传输错误只返回
`data.validation_error` 安全原因（`timeout` / `connection_failed` / `unavailable`），
输入错误为 `invalid_request`；验证期间连接发生变更时返回 HTTP 409 和 `conflict`。
上游 401/403 不作为浏览器会话错误返回，原始模型响应和密钥不进入验证响应或错误日志。

## Skill 配置导入

搜索结果导入时，`POST /api/v2/spaces/{space_id}/labs/skill/registry/resolve`
接收 `source` 和 `skillId`，从 GitHub 定位选中的 `SKILL.md`。
GitHub API 不可用，或在仓库中无法定位选中的 Skill 时，未指定 Git 引用的结果可以使用 skills.sh 发布的单个 Skill 快照；
快照只提取 `SKILL.md`，不记录未经验证的 Git 引用或仓库路径。
直接指向 `SKILL.md` 的 GitHub 链接通过单文件请求解析。需要区分分支名与文件路径时，GitHub API 最多验证三个候选。
有多个匹配时，请使用固定 Git 提交的文件链接。遇到上游限流时，也可以稍后重试。
远程单文件上限为 1 MiB；仓库或目录链接的归档上限仍为 80 MiB。

## 检查

```bash
.venv/bin/python manage.py check
.venv/bin/python manage.py test apps.core.tests.test_local_auth apps.core.tests.test_space_permissions
```

模型位于 `apps/core/models/`，HTTP 入口位于 `apps/core/views/`。数据库结构由 Django migration 管理，不维护独立初始化 DDL。

## 原生分发构建验证

`scripts/build_native.sh` 在目标 Linux CPython 3.11.14 环境中，用 Cython 3.2.4
和系统 C 编译器生成后端扩展模块及原生管理入口；第三方依赖环境单独按 `uv.lock` 提供。
构建保留包目录、管理命令和 migration 名称，输出不包含项目 Python 源码或字节码。
开发环境仍可直接使用 `python manage.py`。

```bash
NATIVE_BUILD_PYTHON=/path/to/compiler-venv/bin/python \
NATIVE_OUTPUT=/path/to/empty-runtime-stage \
NATIVE_BUILD_ROOT=/path/to/private-build-directory \
BOOTSTRAP_SOURCE_ROOT=/path/to/repo/deploy/bootstrap \
NATIVE_JOBS=1 scripts/build_native.sh
```

输出目录和构建目录必须与源码目录分离。构建目录保存生成的 C 文件，不应进入分发产物；
输出中的 `native-backend-manifest.json` 记录输入哈希、架构、ABI 和入口。
原生入口从同目录的 `.venv` 加载原有第三方依赖。脚本仅构建文件，不创建或发布镜像。
目前使用 `-O0 -g0` 验证功能；实际架构与跨服务验证范围应随交付记录说明。

当插件目录存在 `native-onboarding/` 时，
`GET /api/public/onboarding/scripts/connect?platform=linux-gnu&arch=arm64`
按固定平台目录返回 `connect.tar.gz`，并携带
`X-AIV-Installer-Format: native-tar-v1` 和 `X-AIV-Installer-SHA256`。
支持的平台参数为 `linux-gnu`、`darwin`，架构为 `x64`、`arm64`；未提供的构建返回 404，
不会回退到源码。旧的 `install-<agent>.py` 和 `install.sh` 接口在原生模式返回 409，
引导使用当前 `aiv connect`；Python 支持脚本也不再列出或下载。
没有 `native-onboarding/` 的源码开发环境继续使用原有脚本接口。

## Labs MCP 工具同步

MCP 配置导入会在后端再次脱敏；密钥通过同步请求中的 `headerValues` 临时提供，不写入配置或错误日志。工具快照仅在同步成功后更新；修改连接配置会清空旧快照。

同步请求带有非空 `headerValues` 时，必须同时传入获取配置时返回的 `importedAt`，字段名为 `expectedImportedAt`。版本缺失或与当前配置不一致时返回 HTTP 409，且不会向 MCP 服务发送请求；客户端应刷新配置后重新确认认证信息。没有认证信息的同步请求继续兼容省略该字段。

配置导入、删除和主动同步仅允许空间管理员执行，其他成员可查看配置与统计。HTTP/SSE 会从后端访问配置的地址，因此部署应限制后端可访问的网络，并仅向可信管理员开放此功能；重定向和跨源 SSE 消息地址会被拒绝。

stdio 配置仍可导入和查看，但默认不启动命令。确需在可信单机部署中同步时，设置 `MCP_STDIO_SYNC_ENABLED=true` 并重启后端。进程只继承 PATH、HOME、locale 等基础环境，不继承后端密钥；此开关**不提供进程或文件系统沙箱**，命令仍以后端 OS 账号执行。需要密钥的 stdio 配置不能从已脱敏的配置中恢复凭据，应改用带临时请求头的 HTTP/SSE 服务。
