# Docker 部署维护补充

[部署入口](../README.md) · 简体中文 | [English](../en/docker.md)

安装、配置、手动部署、验收与停止服务统一维护在 [Docker 部署文档](https://aivision.mintlify.site/zh-CN/deployment/docker)。本页保留与源码和部署实现相关的补充说明，参数见 [aiv CLI 参考](../../../aivision-cli/README.md#commands)。

将 `aiv` 安装在固定目录，并[验证新终端可直接使用](../../../aivision-cli/README.md#make-aiv-available-in-new-terminals)。日常部署、升级、状态、日志、重建与保留数据停止分别使用 `aiv cast`、`aiv status`、`aiv logs`、`aiv redeploy`、`aiv destroy`，每次保留原 target、namespace、workdir 和 blueprint；已有文件的 Compose 保留准确的 file/env 组合。组件开发和明确的运维修复命令只适用于各自的局部操作。

<a id="install-aiv"></a>

客户端的下载、校验与 PATH 配置见[安装 aiv CLI](https://aivision.mintlify.site/zh-CN/guides/install-cli)。

<a id="reuse-existing-ob"></a>

复用已有 OceanBase 或 seekdb 的账号、权限与连接要求见 [CLI 数据库参考](../../../aivision-cli/README.md#existing-oceanbase)。权威[变量文件示例](../../../deploy/config/aivision.vars.example.yaml)位于 `deploy/config/`；含凭证的实际配置放在版本控制之外。

## 产品资产与可选 Compose

默认 Docker 保持受支持的 Go CLI 运行时。可选 Compose 同样由 Go CLI 通过 `--target compose` 调用原生 Docker Compose；需要 Compose 2.27+ 与 Docker，无额外宿主 Python 或控制脚本。容器 bootstrap 的 Python 仍在应用镜像内运行。CLI 私有不可变 bundle 与已有文件的 `--compose-file`／`--compose-env-file` 方式见[Go／原生 Compose 契约](../../../deploy/docker/README.md)。

用原生 `config --quiet`、`ps --all`、`logs --tail 100 frontend` 检查，指定原项目名、绝对文件与 env。CLI 解析后 bundle 用 `/dev/null`；参数化模板用原实际私有 env。变更保留 Go 的身份／所有权、锁、seekdb 隔离初始化、初始化任务重跑和退出码／OOM 检查。裸 up/down 尚不能视为等价。默认 Docker 不自动转换，独立项目不自动导入 CLI vars。Compose purge 保留 bind-mounted metastore／身份，默认 Docker purge 仍删除原托管卷和 namespace 状态。

根 deploy 维护权威模板／bootstrap 和角色映射；修改后运行 make assets 与 make assets-check。使用包含 SQL readiness、组织文件／bootstrap 和有界停机的匹配镜像。源码合并、dev 标签和此前控制层的证据都不能证明新 Go 路径或已发布 CLI 已包含该 target。

<a id="use-seekdb"></a>

## 数据库选择与源码版本

Docker 默认使用 OceanBase CE，个人轻量部署可选择 **OceanBase seekdb**。请确认所用 CLI 支持 `OB_BACKEND=seekdb`。如果发布版尚未支持，可按 [CLI 源码构建说明](../../../aivision-cli/README.md#source-build-pointing-at-a-different-repo-or-branch)构建。当前源码支持的参数和部署命令见 [CLI 数据库参考](../../../aivision-cli/README.md#choose-the-docker-datastore)。

维护时先核对以下边界，具体默认值和实现以链接中的源码为准：

- seekdb 默认的 2 CPU、2G 内存是数据库配置，整套服务仍需额外资源；固定镜像为 Linux/amd64，不能据此宣称原生 ARM 已验证。
- 托管 seekdb 可由 Docker `aiv cast` 或当前源码的产品 Compose 生命周期管理；旧版通用 Compose 材料缺少初始化，不是安装入口。通用 Kubernetes/systemd 生成与固定 Kubernetes 路径仍不支持 seekdb。
- seekdb 使用无租户后缀的 `root`，默认 Docker 的首启凭证和重试见 [datastore.go](../../../aivision-cli/internal/deploy/runtime/datastore.go)及其[测试](../../../aivision-cli/internal/deploy/runtime/datastore_test.go)；可选 Compose 使用 [Go／原生契约](../../../deploy/docker/README.md)。修改变量不会轮换已有数据卷的密码。
- seekdb 卷与 OceanBase CE 卷独立；同一 namespace 不能直接切换 backend。更换数据库需新建部署并显式迁移数据。

## 镜像读取权限

镜像需要授权时，按文档站的 Docker 指南执行 `docker login ghcr.io -u YOUR_GITHUB_USERNAME`。Token 在终端隐藏提示中输入。

<a id="create-pat"></a>

出现 `Password:` 时填写 PAT。没有时，点击 **[直接创建 PAT（classic）](https://github.com/settings/tokens/new)**，按下面操作：

1. **Note**：填写 `AI Vision GHCR pull`。
2. **Expiration**：下拉选择有效期，例如 `30 days`。
3. **Select scopes**：只勾选 **`read:packages`**（第三部分里面第一个），用于下载镜像。
4. 点击页面底部的 **Generate token**。
5. 点击生成的 Token 旁的复制图标，回到终端粘贴到 `Password:` 后并回车。

## 原生 v1 历史扫描边界

默认 Docker Go 中，`AIVISION_NATIVE_TRACE_HISTORY_START` 不作为其他平台功能的必填项。可选受控 Compose 生命周期要求安装前填写非空固定 UTC 下界，不自动使用当天日期。
留空时部署会提示；原生 v1 SDK 读取会明确返回能力错误
（HTTP 503，code 3038），不会将近期窗口宣称为全部历史。
该值仅限定查询范围，不会改变保留或删除策略；若起点晚于已保留数据，
更早的数据不会被纳入查询，因此需要运维准确维护。

原生读取按每窗最多 31 天扫描，跨窗口合并、去重后返回结果。
上界为当前时间加 `AIVISION_NATIVE_TRACE_FUTURE_SKEW_SECONDS`（默认 `300` 秒）。
`AIVISION_NATIVE_TRACE_MAX_WINDOWS`（默认 `120`）限制扫描工作量；超限明确失败，
不会返回部分历史。默认近期 93 天也不能代表全部保留数据。
这些设置需要使用已实现原生 v1 历史扫描的后端镜像。固定 Kubernetes CLI 不接受历史起点配置，详见其[当前限制](kubernetes.md#更新数据与能力边界)。

## 自定义 Docker 网络

需要指定 Docker 网络时，在任一 `cast` 命令中加入
`--var DOCKER_NETWORK=aivision-custom`。CLI 会在外部 OB 预检或服务启动前，
按需创建实际生效的网络，预检和服务使用同一个网络，无需手工执行
`docker network create`。已有共享网络会直接复用；`destroy` 不删除网络。

## 自定义 blueprint 的日志

指定 blueprint 时使用 `--file`，例如 `aiv logs backend -f --file blueprint.yaml`。`-f` 跟随日志，Ctrl+C 退出。

## 外部反向代理

如另加外部反向代理，请仅为 `/api/v1/spaces/<space_id>/diagnose/diagnosers/llm/trial` 和 `/api/v1/spaces/<space_id>/diagnose/specialists/trial` 配置至少 963 秒的上游读取等待，以覆盖诊断试运行的有限预算；其它 API 保持原等待设置。

对于发往 `/api/public/otel/v1/traces` 的 OTLP 请求，内置 Nginx 前端允许 64 MiB 的 HTTP 请求体，与 Django 和 SigNoz 的默认入口上限一致。其他前端路由仍限制为 32 MiB。若有外层代理，也应仅为此路由配置对应上限；批次大小按编码后的请求字节计算，而不是按每个附件解码后的 16 MiB 上限计算。入口上限不改变后端的解压、单文件与空间配额检查。

若外层代理覆盖静态资源的 MIME 类型，请将 `/assets/` 下 PDF.js 的 `.mjs` worker 作为 JavaScript 提供。当前端通过 Nginx 在 `http://127.0.0.1:5174` 运行时，可在 `ai-vision/vfe/` 下执行 `AIVISION_E2E_BASE_URL=http://127.0.0.1:5174 npx playwright test traceMediaPdf.spec.ts` 验证构建产物。此检查使用合成的 Trace API 夹具，但 worker 由 Nginx 提供，浏览器不会覆盖其响应。

### Trace 查询内存预算

固定的 seekdb 1.4 镜像使用 `SEEKDB_MEMORY_BUDGET` 配置 `memory_budget`（缓存和缓冲区的逻辑预算）。未设置时兼容旧变量 `SEEKDB_MEMORY_LIMIT`，再默认 `2G`；不再传递已被引擎忽略的 `memory_limit`。`SEEKDB_CONTAINER_MEMORY=4G` 独立限制托管数据库及初始化容器。Python 后端和 SigNoz 容器分别默认 `BE_CONTAINER_MEMORY=2G`、`SIGNOZ_CONTAINER_MEMORY=2G`。逻辑预算不等于进程 RSS 上限，需要留出额外空间。已有容器需通过受支持的 CLI 生命周期重新创建才会获得新限制。源码模式的宿主进程不受 Docker 限制。Kubernetes 已有独立资源限制，不支持托管 seekdb。

Python 后端流式读取原生查询响应，默认限制单个解压响应 32 MiB、一次请求累计 64 MiB、每个 worker 同时处理两个查询请求。可通过部署变量 `AIVISION_TRACE_RESPONSE_MAX_BYTES`、`AIVISION_TRACE_REQUEST_MAX_BYTES`、`AIVISION_TRACE_QUERY_CONCURRENCY` 调整。并发名额覆盖结果渲染到响应关闭；批量子查询共享请求预算。超限明确返回资源或并发错误（3036/3037），不会把截断数据作为成功结果。worker 和副本数量会放大总并发，需据此配置数据库容量。这些默认值是保守起点，不代表任意遥测数据都能容纳；调高前应测量 RSS、cgroup 峰值、空闲内存和延迟。

配套 SigNoz 改动在原始 Span 表增加 STORED 生成列 `page_dimensions_v1`、`page_source_bytes_v1`。增量迁移会回填历史数据，需要 ALTER 权限、磁盘空间，大表应安排维护窗口。分页严格校验生成列定义；迁移未完成时明确报错，采集继续可用。授权范围、元数据体积和当前页编码体积估算在同一可重复读事务中检查，通过后才聚合原始页 JSON。全窗筛选读取小型元数据，仅为当前页读取原始负载。两个仓库需配套升级；只改产品源码不会替换已发布的 SigNoz 镜像。

后台投影同样使用累计源数据预算：批次超限后释放已读对象，再拆小重试；单条超大的 Trace 保持 dirty、明确延后处理，并继续后面的 Trace，避免阻塞整个空间。待发布缓冲限制为 16 MiB。SigNoz 分片恢复共用原生查询并发门禁，构造 JSON 前检查原始体积（每 Trace 128 MiB、每 Span 32 MiB），仍保留原有转义后源数据限制；完整窗口恢复响应限制为 24 MiB。受支持的 Kubernetes CLI 入口也接受三个 `AIVISION_TRACE_*` 变量及后端、数据服务容器内存变量，Docker 风格的 `G`/`M` 在 Kubernetes 中按二进制单位转换。
