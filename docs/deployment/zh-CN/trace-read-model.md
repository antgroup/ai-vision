# 原生 Trace 与会话查询：运行和恢复

本文适用于包含自动投影维护的 AI Vision 与 SigNoz 配套源码版本，旧发布镜像可能没有这些命令或接口。外部 Langfuse 保持原有查询路径。原始 Span 是事实来源，摘要、修订记录和覆盖信息都可以重建。

## 只保留两个应急控制

| 进程 | 配置 | 默认值 | 作用 |
|---|---|---|---|
| AI Vision BE | `AIVISION_QUERY_MODE` | `auto` | 自动使用通过校验的索引页，必要时读取完整混合/原始快照；`raw` 让轨迹、canonical 会话及候选值统一绕过投影，保留筛选语义。 |
| SigNoz | `SIGNOZ_AIVISION_PROJECTION_MAINTENANCE_ENABLED` | `true` | `false` 在未跟踪写入之前，持久关闭各实例的投影维护与读取资格；仅改回 `true` 不会解除关闭状态，需明确执行恢复。 |

修改环境变量后重启对应进程。Docker 镜像部署、源码部署和 Kubernetes 模板均传递这两个配置。有限长度的列表预览、会话摘要批量读取和 Token 查询优化是标准实现，不再各设开关。旧的 Trace/会话 `legacy`、`shadow`、`prefer` 和独立 SQL 策略配置不再控制运行路径。

部署配置由 [`deploy/`](../../../deploy/README.md) 统一维护。CLI vars 与产品 Compose `.env` 优先使用 `BACKEND_QUERY_MODE`、`DATA_BACKEND_PROJECTION_MAINTENANCE_ENABLED`，分别映射到上表的应用环境变量；它们是同两个控制的别名。修改权威源后，在 `aivision-cli/` 执行 `make assets`，不要手改嵌入或 Compose 生成副本。应用变更时复用原配置、命名空间及部署入口。

## 自动迁移与投影维护

SigNoz 创建控制表及源修订、投影、覆盖表，通过有版本记录的串行迁移补齐结构。标准投影表包含 STORED 生成列 `session_trace_tokens_v1`（`DECIMAL(65,0)`）与 `session_billable_v1`（`TINYINT`）。数据库根据 `row_json` 计算，包括旧发布进程的写入；保留原有 SQL CASE 和数值转换规则，不承诺任意大整数精度。

已有数据的迁移会消耗写入和存储资源。迁移使用独立、有限时长的连接，不提高普通接口超时或数据库全局参数。失败后投影不获得读取资格，并自动重试；只要原始表与数据库连接正常，原始采集和原始快照恢复仍可使用。权限、通信或成功响应格式错误会明确报错，不能当作空结果。

首次托管迁移会重建无法证明可信的历史派生状态，保留原始 Span。派生清理每个事务最多提交 256 行，让原始写入可在批次间继续；中断后保持关闭，并从剩余记录续做。明确的运维恢复操作单独使用四分钟服务端、245 秒客户端期限，客户端自动发送已有的 `timeout: 240` 请求头，让主 API 中间件采用对应期限；不提高普通查询超时。上述期限只针对一次恢复 HTTP 请求；管理命令还会执行一轮有界投影维护，整条命令可能耗时更长，随后由受托管进程持续回填。当前结构版本的正常重启保留覆盖状态和回填游标。多个实例通过迁移租约及共享维护状态协调；中断后可以按实际结构继续迁移。

BE 标准入口共同管理 API 和 `maintain_trace_read_model`，发现有效 Agent、空间及已启用的原生轨迹数据源，自动回填历史并刷新脏 Trace。新增路由、凭据或价格上下文变化不需要逐空间手动创建进程。发布时检查源修订，发生并发变化则留待重试。批量快照/发布减少传输次数，各 Trace 仍独立检查修订。

如果绕过标准入口手动运行源码 Web 服务，在相同 BE 环境中启动：

```sh
python manage.py maintain_trace_read_model
```

`--once` 执行一轮发现，`--limit` 限制每路由一轮处理量。输出包含回填、发布、延期及冲突；历史登记完成不等于全部投影已就绪。失败的路由会重试，其它路由继续处理。原有逐空间 `project_trace_read_model` 可用于排查。

## 回填期间如何查询

框架观测看板与 Agent 轨迹页复用同一套 canonical 轨迹和会话查询。会话按每页 10、20、50 条在服务端分页，打开详情时保持相同分组。操作趋势可使用下文的类型化 SQL 聚合；普通指标在 `statistics-v1` 聚合缺少完整费用来源证据时使用现有摘要 JSON 中带版本的紧凑调用数据，保留调用身份、父子关系、时间、类型、模型/提供方及标准化用量和费用，使嵌套模型调用的歧义、未知用量与原始查询口径一致。

统计专用窗口中的就绪摘要不传消息正文或 IO 预览，脏数据或能力缺失继续使用完整原始数据回退；内容筛选仍走完整内容路径。使用恢复路径、存储凭据、物理范围和固定时间窗均相同的并发区块只共享正在进行的快照读取，不缓存已完成快照；读取完成后再次查询会重新读取，同一固定窗口的并发刷新可合并到正在进行的读取。事件流最多读取最近 200 个操作的定位信息，再以每批最多 100 个完整源快照补齐对应 Trace。源修订和维护代次都必须与定位信息一致；任一变化都会弃用整批选择，重新读取一次完整原始窗口。旧紧凑窗口缺少代次证明时，分步内容补齐采用同样的安全原始恢复；单快照统计、列表及原始读取不新增派生状态依赖。

新增统计数据保留现有 canonical/计价投影上下文，回填时已就绪的轨迹和会话索引继续可用。配套数据平面接受维护进程可选的 `required_supplement` 条件，按有限批次补齐缺少统计数据的摘要。这项 JSON 补充不需要数据表迁移或开关；下文的可选类型化统计结构单独执行增量迁移。维护输出 `supplementSupported=false` 表示旧数据平面：普通脏数据维护继续工作，但升级数据平面前不会主动补齐历史统计数据。追赶期间缺少统计数据的看板读取完整原始快照，鉴权、通信和格式错误仍明确报错。`AIVISION_QUERY_MODE=raw` 也覆盖看板的紧凑统计路径，继续使用原部署别名及两个应急控制。

就绪时使用 canonical 摘要上的 SQL 筛选和会话聚合。脏数据、无法投影或跨时间边界的 Trace 不要求逐个读取整个窗口：回退在同一个可重复读事务中获取可信摘要及其余已授权原始 Span，由 Python 按相同 canonical 规则合并。`raw` 模式直接读取完整授权窗口，不依赖投影。恢复会预检摘要大小；摘要传输超出响应预算时，在同一事务内改读完整原始窗口。资源上限不变；原始恢复也超限时仍明确失败。

完整快照每窗口最多 50,000 个 Trace、500,000 个原始 Span，所选源字段最多 512 MiB、单 Span 最多 16 MiB、紧凑响应最多 256 MiB。传输时只移除重复副本，不截断 canonical IO 或属性，并逐行检查。超过上限明确失败并提示缩小时间范围，不返回截断的成功列表。它覆盖测试中 10,000 个会话对应的 30,000 个 Trace，不是无限扫描或生产延迟保证。旧数据平面没有快照接口时，仍受原兼容扫描上限限制。

索引会话页保持 50 个会话、5,000 个所选 Trace 和 16 MiB 上限，超出后走完整快照恢复。列表每个序列化 IO 预览最多 1,024 个 Unicode 字符；内容检索及详情/导出继续使用完整记录内容。

## OceanBase 统计聚合

当前 `statistics-v1` 协议只有金额和计数，无法还原 `costCoverage` 中的计价来源、未知原因、订阅标记和公开价格等价值。普通指标校验此响应后回退完整的紧凑统计窗口；`framework.events.timeseries` 继续使用经过校验的类型化操作计数，最近操作分页仍按源修订补齐内容。紧凑数据保留 canonical 费用来源，与原始恢复共享请求内的 Space 价格快照及投影上下文。此前类型化指标耗时仅对应其记录的源码，不作为本次兼容定价语义后的性能结果。

配套数据平面可从独立的 Trace 和操作统计表执行常用整窗聚合。新表默认采用显式行存，已有混存/列存继续可用，不自动转换。分组键同时包含缺失值和显式空串时，继续使用原窗口以保留 canonical 分组顺序。SQL 返回数量、用量累加器、模型/提供方分组及时间桶，并提供有界的耗时数值数组以保留原有精确耗时计算；这条读取不传消息正文、预览或逐节点 JSON。内容及暂不支持的筛选条件继续使用已校验的完整窗口。

可选结构使用独立的增量迁移锁，不改变基础投影的就绪状态、代次或 canonical/计价上下文。现有投影文档上的 STORED 校验回执列会随每次写入重新计算，旧版发布进程也不能让文档变更后的统计继续有效。正常维护进程按批补齐缺失统计，并在现有源修订及维护锁下原子替换该 Trace 的旧贡献。不适合类型化列的维度/数值或超出投影发布大小上限的统计补充保留普通摘要，并记录不可加速回执，不对同一文档持续重复投影。自动路径面向 OceanBase 4.4 及以上版本，包含 CE 和企业版；旧版本继续使用现有紧凑窗口恢复。本次实际 SQL 和存储布局验证使用 CE 4.4.2.1。聚合包含三次查询：合并的完整性/Trace 汇总、有界耗时读取，以及用 `GROUPING SETS` 一次生成五组用量和操作统计。

脏数据、过期、缺失或跨时间边界时继续走统计窗口/原始恢复。未知 Token、嵌套调用歧义、已上报小计、覆盖率和 Decimal 费用保持原口径。应急恢复按同样的有限批次和锁清理统计表，旧部署缺少这些表时仍可恢复。没有新增环境开关：`AIVISION_QUERY_MODE=raw` 绕过此路径，原有维护关闭状态继续生效。存储布局的实测结果及本地验证范围单独记录，不作为生产容量承诺。

四框架和 10k 会话的同数据结果、行存/混存/列存对比、新增投影写入成本及并发列表竞争见 [OceanBase 聚合验证报告](../../testing/ob-ap-dashboard-aggregation-20261026.md)。这些本地准备环境的接口观测不代表生产容量或浏览器绘制保证。

### seekdb 轻量兼容

OceanBase CE 仍是团队部署和持续增长观测数据的默认推荐，seekdb 面向个人、本地开发和轻量使用。已识别的 seekdb 1.x 版本（1.4 及以上）可复用类型化行存统计表、增量维护，以及最近操作分页和有限内容补齐；建表使用不带 OceanBase 显式列组后缀的普通行存 DDL，聚合使用分组轴、`CASE` 和 `GROUP BY`，不依赖 `GROUPING SETS`。两条路径返回相同的 canonical 结构，并保留完整性、源修订和维护代次校验。

查询形态在内部选择，不新增用户开关。较旧或无法识别的引擎、缺少可选统计时继续使用完整窗口恢复。[seekdb 兼容验证报告](../../testing/seekdb-dashboard-compatibility-20261026.md)单独记录实际版本、数据、成本和边界，不套用 OceanBase 的性能结果，也不代表默认个人资源配置的容量保证。

### 升级时的数据库变更

需要配套升级 SigNoz 数据后端和 AI Vision Python 后端。受支持的 OceanBase 4.4+ 和已识别的 seekdb 1.x（1.4 及以上）部署会在启动时自动创建 `<前缀>_trace_statistics`、`<前缀>_call_statistics`（含时间索引），并在 `<前缀>_trace_projections` 增加 `statistics_receipt_v1` STORED 生成列。现有数据库账号需要 `CREATE`、`ALTER`、常规读写及结构元数据访问权限。普通升级无需手工执行 SQL，也不重写原始 Span 表。较旧或无法识别的引擎继续使用原查询路径，不创建这套可选结构。

正常维护进程会逐批补齐历史统计；缺失或不可加速的数据采用完整窗口恢复，已就绪轨迹和会话页的基础状态保持不变。已有大表新增 STORED 列可能消耗 IO、存储并遇到 DDL 锁等待，应预留升级窗口；12 条 Trace 的初始化实测不能代表生产历史迁移耗时。可选初始化失败会记录并重试；结构缺失或不兼容可使用恢复路径，但真实的元数据权限、连接或 SQL 错误仍明确报错。功能回退使用现有 raw 查询控制，不要求删除新增列或表。

## 筛选口径与核对依据

任务用户、环境、标签及输入/输出内容需同时命中同一个 Trace。匹配会话保留所选时间范围内的全部轮次，用于数量、用量、状态、耗时及展开；缺少会话 ID 不凭空生成会话。SDK 任务用户和环境不替代物理授权身份。

Token 下限包含零阈值，也要求模型用量完整可知。缺失用量不是零；仅工具 Trace 不会破坏其它轮次已知的模型用量。生成列定义一致时才使用 STORED 值，列缺失或定义不匹配时使用内部 JSON_TABLE 兼容策略。元数据权限及数据库错误仍明确失败。

| 响应字段 | 含义 |
|---|---|
| `meta.queryMode=read_model` | 通过校验的索引页。 |
| `meta.queryMode=hybrid_snapshot` | 可信摘要与原始数据组成的完整快照。 |
| `meta.queryMode=raw_snapshot` | 只使用原始 Span 的完整快照。 |
| `meta.sessionPageSummaryStrategy=batch` | 标准会话摘要批量读取。 |
| `meta.sessionTokenQueryStrategy=generated_columns` | 使用校验后的标准 Token 生成值。 |
| `meta.readModel.reason` | 索引页不可用的原因，不等于完整回退也失败。 |

一份响应中的数量、筛选、候选值和排序来自同一存储快照；不同请求可能看到之后完成的新修订。认证失败、格式错误及通信失败保持可见。

## 应急恢复顺序

1. 部署配置设置 `BACKEND_QUERY_MODE=raw` 后，通过原入口重新应用，统一绕过索引并保留筛选。完整快照可能更慢，仍受明确资源上限约束。
2. 派生结构或写入有问题时，设置 `DATA_BACKEND_PROJECTION_MAINTENANCE_ENABLED=false` 并重新应用数据后端写入实例。共享关闭状态阻止其它仍使用本地开启配置的实例信任旧摘要。原始修改仍需通过受控采集路径。
3. 修复原因，在全部写入实例将 `DATA_BACKEND_PROJECTION_MAINTENANCE_ENABLED` 改回 `true`。明确恢复同时要求现有 SigNoz 管理员凭据和部署内部采集密钥，普通组织读取权限不能重置整个数据平面的状态。
4. 在运维终端从原有私有部署配置导出 `SIGNOZ_INGEST_INTERNAL_KEY`，再执行：

```sh
docker exec --workdir /app --env SIGNOZ_INGEST_INTERNAL_KEY aivision-be   /app/.venv/bin/python manage.py maintain_trace_read_model --resume-maintenance --once
```

容器名从 `aiv status` 或原 `aiv status --target compose --compose-file ... --compose-env-file ...` 获取后替换；Compose 的角色名为 `backend`，容器不一定叫 `aivision-be`。只传环境变量名，从运维终端继承值，避免把密钥值放进参数或日志；普通维护进程不需要这个密钥。恢复会移除可重建的派生记录，包括已删除 Trace 的残留投影，并提高共享代次，保留原始 Span。随后自动重新登记和投影，期间仍可通过恢复查询读取。

5. 用同一时间范围核对原有查询、完整数量及零阈值。修复后将同一个部署配置 `BACKEND_QUERY_MODE` 改回 `auto`。功能回退不要求删除生成列。

手动 SQL 导入、保留策略、恢复备份、替换分区/表或直接写原始表绕过采集跟踪。执行前关闭维护，结束后明确恢复重建；不能声称系统会自动识别这些外部数据库变化。CE 和 seekdb 数据卷不兼容，切换数据库需独立部署并明确迁移数据。

### Kubernetes 恢复进程

先在原私有配置中将全部写入实例的 `DATA_BACKEND_PROJECTION_MAINTENANCE_ENABLED` 改回 `true`，沿用原 context、namespace 和 vars 文件重新应用固定 CLI 部署。按已有 Secret 凭据流程取得单行内部密钥，仅在获授权的运维终端导出。替换下例 context 与 namespace，通过标准输入交给一次性 exec 进程；不要把值放进参数、Pod 环境、历史输出或日志。此命令不重启初始化 Job 或正常服务进程。

```sh
test -n "${SIGNOZ_INGEST_INTERNAL_KEY:-}" &&
printf '%s\n' "$SIGNOZ_INGEST_INTERNAL_KEY" |
  kubectl --context YOUR_CONTEXT --namespace YOUR_NAMESPACE \
    exec -i deployment/aivision-be -c be -- /bin/sh -c \
    'IFS= read -r SIGNOZ_INGEST_INTERNAL_KEY || exit 1; export SIGNOZ_INGEST_INTERNAL_KEY; cd /app && exec /app/.venv/bin/python manage.py maintain_trace_read_model --resume-maintenance --once'
```

为兼容已有部署，实际 Deployment / 容器名称仍为 `aivision-be` / `be`。密钥仅进入恢复进程。核对固定查询与完整覆盖后，将同一个 `BACKEND_QUERY_MODE` 改回 `auto`，再通过原 CLI 应用。此处提供运维步骤，不代表已完成真实集群验证。

## 验证范围

见[同一批数据的性能与恢复报告](../../testing/standard-query-defaults-20261025.md)及[上一轮可选优化对照](../../testing/session-query-recovery-20261025.md)。准备好的本地环境、真实数据库 SQL、外部测试传输与完整 SigNoz 认证、发布镜像、Kubernetes 实际运行、生产部署分别记录。
