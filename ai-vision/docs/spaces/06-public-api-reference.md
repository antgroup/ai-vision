# 06. AI Vision Public API 接口文档

## 概述

AI Vision 对外提供兼容 Langfuse Public API 的只读查询接口，供业务方通过 API 拉取空间内的观测数据。

### 基本信息

- Base URL: `https://{aivision-host}/api/public`
- 鉴权方式: HTTP Basic Auth
- 响应格式: JSON（直接透传 Langfuse 格式，不包装 `{code, message, data}`）

### 鉴权

使用空间的**读取 Key（READ scope）**进行 Basic Auth 认证：

```
Authorization: Basic base64({public_key}:{secret_key})
```

示例：

```bash
# 标准 header 方式
curl -H "Authorization: Basic $(echo -n 'pk-lf-xxxx:sk-lf-xxxx' | base64)" \
  https://{aivision-host}/api/public/traces

# 或使用 curl -u 快捷写法（自动编码为 Basic Auth header）
curl -u "pk-lf-xxxx:sk-lf-xxxx" https://{aivision-host}/api/public/traces
```

#### 凭证获取

1. 管理员在 AI Vision 空间设置页面查看读取 Key（需点击「查看」取消脱敏）
2. 或通过 Django Admin 后台查看

#### 错误码

| HTTP 状态码 | 含义 | 场景 |
|------------|------|------|
| 401 | 未认证 | 缺少 Authorization header、public_key 不存在、secret_key 不匹配 |
| 403 | 权限不足 | 使用上报 Key（WRITE scope）访问只读接口 |
| 502 | 上游异常 | Langfuse 服务不可用 |

---

## 接口列表

### 1. 查询 Trace 列表

```
GET /api/public/traces
```

#### 请求参数（Query String）

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| page | int | 否 | 页码，默认 1 |
| limit | int | 否 | 每页条数，默认 10，最大 100 |
| name | string | 否 | 按 trace name 过滤 |
| fromTimestamp | string | 否 | 起始时间（ISO 8601），如 `2026-04-15T00:00:00Z` |
| toTimestamp | string | 否 | 结束时间（ISO 8601） |
| orderBy | string | 否 | 排序字段，如 `timestamp.desc`（默认） |
| filter | string | 否 | Langfuse 原生 filter JSON 字符串 |
| fields | string | 否 | 返回字段控制，如传 `io` 可返回 input/output |

#### 请求示例

```bash
# 方式 1：-H header
curl -H "Authorization: Basic $(echo -n 'pk-lf-xxxx:sk-lf-xxxx' | base64)" \
  "https://{aivision-host}/api/public/traces?page=1&limit=5&name=invoke.invoke"

# 方式 2：-u 快捷写法
curl -u "pk-lf-xxxx:sk-lf-xxxx" \
  "https://{aivision-host}/api/public/traces?page=1&limit=5&name=invoke.invoke"
```

#### 响应示例

```json
{
  "data": [
    {
      "id": "2dfdc141a59a2c68b34b1bb4135960d1",
      "name": "invoke.invoke",
      "timestamp": "2026-04-16T02:15:47.440Z",
      "metadata": {
        "attributes": {
          "ant.username": "461670",
          "gen_ai.agent.name": "eddy",
          "gen_ai.agent.id": "eddy-default"
        }
      },
      "input": [...],
      "output": [...],
      "observations": ["a0f95750603c1dc6", "145fb97e32d1e987"],
      "totalCost": 0,
      "latency": 1.454
    }
  ],
  "meta": {
    "page": 1,
    "limit": 5,
    "totalItems": 1,
    "totalPages": 1
  }
}
```

---

### 2. 查询 Trace 详情

```
POST /api/public/traces/{trace_id}
```

#### 路径参数

| 参数 | 类型 | 说明 |
|------|------|------|
| trace_id | string | Trace 唯一标识 |

#### 请求示例

```bash
curl -H "Authorization: Basic $(echo -n 'pk-lf-xxxx:sk-lf-xxxx' | base64)" \
  -X POST \
  -H "Content-Type: application/json" \
  -d '{"fromTimestamp":"2026-06-25T00:00:00+08:00","toTimestamp":"2026-06-25T06:45:00+08:00"}' \
  "https://{aivision-host}/api/public/traces/2dfdc141a59a2c68b34b1bb4135960d1"

# 或
curl -u "pk-lf-xxxx:sk-lf-xxxx" \
  -X POST \
  -H "Content-Type: application/json" \
  -d '{"fromTimestamp":"2026-06-25T00:00:00+08:00","toTimestamp":"2026-06-25T06:45:00+08:00"}' \
  "https://{aivision-host}/api/public/traces/2dfdc141a59a2c68b34b1bb4135960d1"
```

#### 响应示例

```json
{
  "id": "2dfdc141a59a2c68b34b1bb4135960d1",
  "name": "invoke.invoke",
  "timestamp": "2026-04-16T02:15:47.440Z",
  "metadata": {...},
  "input": [...],
  "output": [...],
  "observations": [
    {
      "id": "a0f95750603c1dc6",
      "traceId": "2dfdc141a59a2c68b34b1bb4135960d1",
      "type": "SPAN",
      "name": "chat",
      "startTime": "2026-04-16T02:15:47.500Z",
      "endTime": "2026-04-16T02:15:48.800Z",
      "model": "gpt-4",
      "usage": {
        "input": 150,
        "output": 80,
        "total": 230
      }
    }
  ],
  "totalCost": 0,
  "latency": 1.454
}
```

---

### 3. 查询 Observation 详情

```
GET /api/public/observations/{observation_id}
```

#### 路径参数

| 参数 | 类型 | 说明 |
|------|------|------|
| observation_id | string | Observation（Span）唯一标识 |

#### 请求示例

```bash
curl -H "Authorization: Basic $(echo -n 'pk-lf-xxxx:sk-lf-xxxx' | base64)" \
  "https://{aivision-host}/api/public/observations/a0f95750603c1dc6"

# 或
curl -u "pk-lf-xxxx:sk-lf-xxxx" \
  "https://{aivision-host}/api/public/observations/a0f95750603c1dc6"
```

#### 响应示例

```json
{
  "id": "a0f95750603c1dc6",
  "traceId": "2dfdc141a59a2c68b34b1bb4135960d1",
  "type": "SPAN",
  "name": "chat",
  "startTime": "2026-04-16T02:15:47.500Z",
  "endTime": "2026-04-16T02:15:48.800Z",
  "model": "gpt-4",
  "input": {...},
  "output": {...},
  "usage": {
    "input": 150,
    "output": 80,
    "total": 230
  },
  "metadata": {...}
}
```

---

### 4. 查询 Observation 列表

```
GET /api/public/observations
```

#### 请求参数（Query String）

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| page | int | 否 | 页码，默认 1 |
| limit | int | 否 | 每页条数，默认 10，最大 100 |
| name | string | 否 | 按 observation name 过滤 |
| type | string | 否 | 按 observation type 过滤，如 `SPAN`、`GENERATION` |
| traceId | string | 否 | 按所属 trace ID 过滤 |
| fromStartTime | string | 否 | 起始时间（ISO 8601） |
| toStartTime | string | 否 | 结束时间（ISO 8601） |

#### 请求示例

```bash
curl -u "pk-lf-xxxx:sk-lf-xxxx" \
  "https://{aivision-host}/api/public/observations?page=1&limit=10&type=SPAN"
```

#### 响应示例

```json
{
  "data": [
    {
      "id": "a0f95750603c1dc6",
      "traceId": "2dfdc141a59a2c68b34b1bb4135960d1",
      "type": "SPAN",
      "name": "chat",
      "startTime": "2026-04-16T02:15:47.500Z",
      "endTime": "2026-04-16T02:15:48.800Z",
      "model": "gpt-4",
      "usage": {
        "input": 150,
        "output": 80,
        "total": 230
      }
    }
  ],
  "meta": {
    "page": 1,
    "limit": 10,
    "totalItems": 1,
    "totalPages": 1
  }
}
```

---

### 5. 查询 Session 列表

```
GET /api/public/sessions
```

#### 请求参数（Query String）

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| page | int | 否 | 页码，默认 1 |
| limit | int | 否 | 每页条数，默认 10，最大 100 |
| fromCreatedAt | string | 否 | 起始时间（ISO 8601） |
| toCreatedAt | string | 否 | 结束时间（ISO 8601） |

#### 请求示例

```bash
curl -u "pk-lf-xxxx:sk-lf-xxxx" \
  "https://{aivision-host}/api/public/sessions?page=1&limit=10"
```

#### 响应示例

```json
{
  "data": [
    {
      "id": "session-abc-123",
      "createdAt": "2026-04-16T02:00:00.000Z",
      "projectId": "proj_xxx"
    }
  ],
  "meta": {
    "page": 1,
    "limit": 10,
    "totalItems": 1,
    "totalPages": 1
  }
}
```

---

### 6. 查询 Session 详情

```
GET /api/public/sessions/{session_id}
```

#### 路径参数

| 参数 | 类型 | 说明 |
|------|------|------|
| session_id | string | Session 唯一标识 |

#### 请求示例

```bash
curl -u "pk-lf-xxxx:sk-lf-xxxx" \
  "https://{aivision-host}/api/public/sessions/session-abc-123"
```

#### 响应示例

```json
{
  "id": "session-abc-123",
  "createdAt": "2026-04-16T02:00:00.000Z",
  "projectId": "proj_xxx",
  "traces": [
    {
      "id": "2dfdc141a59a2c68b34b1bb4135960d1",
      "name": "invoke.invoke",
      "timestamp": "2026-04-16T02:15:47.440Z"
    }
  ]
}
```

---

## SDK 接入示例

业务方使用 Langfuse SDK 时，只需将 host 改为 AI Vision 地址，使用读取 Key 即可查询：

### Python

```python
from langfuse import Langfuse

client = Langfuse(
    public_key="pk-lf-xxxx",
    secret_key="sk-lf-xxxx",
    host="https://{aivision-host}"
)

# 查询 traces
traces = client.fetch_traces(limit=10)

# 查询单条 trace
trace = client.fetch_trace("trace_id_here")

# 查询单条 observation
observation = client.fetch_observation("observation_id_here")
```

### cURL

```bash
# 列表查询
curl -u "pk-lf-xxxx:sk-lf-xxxx" "https://{aivision-host}/api/public/traces?limit=10"

# 详情查询
curl -u "pk-lf-xxxx:sk-lf-xxxx" \
  -X POST \
  -H "Content-Type: application/json" \
  -d '{"fromTimestamp":"2026-06-25T00:00:00+08:00","toTimestamp":"2026-06-25T06:45:00+08:00"}' \
  "https://{aivision-host}/api/public/traces/{trace_id}"

# Observation 详情
curl -u "pk-lf-xxxx:sk-lf-xxxx" "https://{aivision-host}/api/public/observations/{observation_id}"
```

---

## 兼容性说明

- 本接口兼容 Langfuse Public API 的查询子集，不承诺完整镜像全部接口和参数
- 成功响应尽量透传 Langfuse 原始格式
- 认证失败、权限不足等错误由 AIVision 网关返回，格式为 `{"detail": "错误信息"}`
- 一期仅开放读取接口，写入接口（如 `/api/public/otel`）后续扩展
