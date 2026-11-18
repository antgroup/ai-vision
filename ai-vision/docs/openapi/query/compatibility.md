# OpenAPI 对外契约与兼容性策略

本文定义 AI Vision OpenAPI 的对外稳定性边界，以及每次迭代需要守住的回归测试要求。

## 1. 稳定契约范围

对外稳定契约包括：

1. `docs/openapi/query/openapi-external.yaml` 中声明的路径、方法、认证方式、路径参数、常用 query/body 参数、错误响应。
2. Markdown 文档中明确说明的调用规则、过滤语义、分页语义、权限边界、I/O 与 usage 组装行为。
3. 统一响应 envelope：`{ "code": 0, "message": "success", "data": ... }`。
4. 认证与空间隔离：公开文档只承诺 `Authorization: Bearer <Space API Key>`。

以下内容不作为稳定契约：

- 未在文档或 YAML 中声明的内部认证兼容路径。
- 返回对象中的字段顺序。
- 未文档化的 provider 内部字段、临时调试字段。
- SigNoz 原始错误文案的精确字符串；客户端应优先依赖 HTTP 状态码和 `code`。

## 2. 兼容变更与破坏性变更

优先保持向后兼容。通常属于兼容变更的例子：

- 新增可选字段。
- 新增可选 query/body 参数。
- 新增 endpoint。
- 对已有 endpoint 增加更丰富的 `data` 子字段，但不删除旧字段。
- 文档补充说明现有行为。

属于破坏性变更的例子：

- 删除已公开 endpoint、参数或响应字段。
- 改变已公开字段类型，例如 string 改为 object。
- 改变过滤、分页、认证、Space 隔离、`agent_product_filter` 隔离语义。
- 改变错误 `code` 或把认证/授权错误映射到不同 HTTP 状态。
- 让个人空间查询不再自动附加服务端用户过滤。

破坏性变更必须先设计新版本或迁移策略，例如新增 `/openapi/v2`、保留兼容期、提前公告，并补充相应契约测试。

## 3. 认证契约

公开对外认证方式只有：

```http
Authorization: Bearer sk-aiv-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

Space API Key 是 Space 级凭证：

- 路径中的 `space_id` 必须与 API Key 所属 Space 一致。
- 个人空间由服务端自动按 API Key 创建者身份追加 username 过滤。
- 业务空间在 Space 权限范围内查询，可继续使用业务过滤条件缩小范围。

## 4. DataSource 与 Agent 产品隔离

`{agent_product}` 路径参数用于解析 `AgentTypeConfig` 和启用的 `DataSource` 配置。

底层 SigNoz 查询里的 Agent 产品隔离值以 DataSource 配置为准：

```json
{
  "agent_product_filter": "ClaudeCode"
}
```

调用方不能通过 body 中的 `agent_product` 覆盖该隔离值。数据库配置必须使用 `agent_product_filter`；错误字段名不作为兼容目标。

## 5. 过滤与分页契约

列表接口支持两类过滤：

1. 顶层快捷参数：`env`、`scene`、`username`、`tags`。
2. `filters` 数组或 filters map：直接传 SigNoz 表字段。

不是所有字段都能作为顶层参数传入；未声明的字段应通过 `filters` 使用。SigNoz/native Session 列表要求所有条件同时命中同一原始 span，决定 Session 命中及其列表元数据，不支持的字段和操作符会明确报错。其他来源的字段支持由 provider 决定。

分页契约：

- `page` 从 1 开始。
- SigNoz/native Session 列表的 `limit` 默认 50，最大 200，默认按 `last_activity` 降序排列；外部 Langfuse 保留 provider 的默认值和上限。
- 列表返回 `meta.page`、`meta.limit`、`meta.totalItems`、`meta.totalPages`。
- 原生 Session 列表在本地计算分页与 facets；每次请求都验证授权 Space/product 的完整时间窗口，再筛选 Agent UUID。10,000 条物理 traces 的上限在 UUID 筛选前生效，空页也必须完成验证。
- 指定 UUID 时，全部 spans 必须在每条 trace 内携带同一个非空 Agent UUID；缺失或混合身份会使扫描失败。Session facets 对 Session 去重计数，并排除各维度自身的过滤条件。

## 6. I/O 与 usage 契约

列表接口的 `io=true`、Trace 列表的 `usage=true`，以及外部 Langfuse Session 列表的 `usage=true` 是 view 层 enrichment：

1. 先按列表过滤条件查询 page 结果。
2. 再基于当前页 ID 批量加载 I/O 或 usage。
3. 最多 enrichment 当前页前 50 条。

SigNoz/native Session 列表始终返回规范化的 `usage`、`coverage` 和 `input_tokens`、`output_tokens`、`total_tokens`，无需 `usage=true`。未知值保留 `null`，明确上报的零值保留 `0`。

原生 Session 用量包含选中 Session 在该时间窗口、Agent 范围内的全部关联 traces，不会被列表过滤条件截断。成功响应的 `meta.complete` 为 `true`；扫描不完整或超过上限时请求失败。

Trace usage 和外部 Langfuse Session usage 是否有数据取决于 provider；无可用 usage 时不会伪造数据。

## 7. 回归测试要求

每次修改 OpenAPI 行为、auth、datasource routing、过滤、分页、响应 envelope、错误码、文档或 YAML 时，在 monorepo 根目录运行：

```bash
make -C ai-vision check
```

本地运行前，将 `AIVISION_LOG_DIR` 设置为当前用户可写的日志目录。

`ai-vision/Makefile` 的 `check` 包含 `check-openapi-contract`，并在 `ai-vision/` 目录调用：

```bash
./aivision-py-be/scripts/run_openapi_contract_tests.sh
```

需要只运行这一项时，也可以执行 `make -C ai-vision check-openapi-contract`。该脚本运行的是确定性的
Django contract tests：

- 默认设置 `USE_SQLITE_FOR_TESTS=true`，使用本地 Django test database。
- 测试会自己创建 `User`、`Space`、`SpaceKey`、`AgentTypeConfig`、`DataSource` 等 fixture。
- Langfuse 请求指向测试启动的本地 HTTP 服务，SigNoz 网络边界使用模拟；不连接部署中的真实服务。
- 日志中出现的 `Unauthorized` / `Forbidden` 是负向鉴权用例，只有当断言状态码正确时测试才会通过。

契约测试要求：

- 走 Django URL routing、OpenAPI view、真实认证与 Space 校验。
- 使用真实 `AgentTypeConfig` / `DataSource` 解析。
- 使用本地 HTTP 模拟 Langfuse，并模拟 SigNoz 网络边界。
- 断言语义化 `QuerySpec`，不依赖完整 SQL 字符串。
- 覆盖公开 YAML/Markdown 的 Bearer-only 契约。

提交相关改动前手动执行上述检查；需要接入 CI 时可复用同一脚本。
