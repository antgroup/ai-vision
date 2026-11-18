# AI Vision API Documentation v1.0

> 基于语雀文档《AI Vision第一期原始沟通记录》需求设计
> 
> **版本**: v1.0 (330 交付版本)

---

## 1. API 设计原则

### 1.1 基础规范

- **基础路径**: `/api/v1`
- **认证方式**: Bearer Token (Header: `Authorization: Bearer <token>`)
- **请求格式**: `application/json`
- **响应格式**: `application/json`
- **时间格式**: ISO 8601 (`2026-03-18T10:30:00Z`)

### 1.2 统一响应结构

```typescript
// 成功响应
interface SuccessResponse<T> {
  code: 0;
  message: "success";
  data: T;
}

// 错误响应
interface ErrorResponse {
  code: number;  // 非0表示错误
  message: string;
  error?: {
    type: string;
    details?: any;
  };
}

// 分页响应
interface PagedResponse<T> {
  code: 0;
  message: "success";
  data: {
    items: T[];
    pagination: {
      page: number;
      pageSize: number;
      total: number;
      totalPages: number;
    };
  };
}
```

### 1.3 错误码定义

| 错误码 | 说明 |
|--------|------|
| 0 | 成功 |
| 1001 | 参数错误 |
| 1002 | 认证失败 |
| 1003 | 权限不足 |
| 1004 | 资源不存在 |
| 1005 | 资源已存在 |
| 2001 | API Key 无效 |
| 2002 | API Key 已过期 |
| 3001 | 空间不存在 |
| 3002 | 不是空间成员 |
| 4001 | Agent 不存在 |
| 5001 | 内部错误 |

---

## 2. 用户认证 API

### 2.1 获取当前用户信息

获取当前登录用户的详细信息，包括个人空间信息。

**请求**

```
GET /api/v1/user/me
```

**响应**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "id": 1,
    "workId": "100001",
    "name": "示例用户",
    "email": "testuser@example.com",
    "avatarUrl": "https://example.com/avatar.jpg",
    "personalSpace": {
      "id": 1,
      "name": "示例用户的个人空间",
      "type": 1,
      "createdAt": "2026-03-17T10:00:00Z"
    },
    "createdAt": "2026-03-17T10:00:00Z"
  }
}
```

### 2.2 初始化用户（首次登录）

首次登录时自动创建用户和默认个人空间。

**请求**

```
POST /api/v1/user/init
```

**请求体**

```json
{
  "workId": "100001",
  "name": "示例用户",
  "email": "testuser@example.com"
}
```

**响应**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "id": 1,
    "workId": "100001",
    "name": "示例用户",
    "email": "testuser@example.com",
    "personalSpace": {
      "id": 1,
      "name": "示例用户的个人空间",
      "type": 1,
      "createdAt": "2026-03-17T10:00:00Z"
    }
  }
}
```

---

## 3. 空间管理 API

### 3.1 获取空间详情

获取指定空间的详细信息。

**请求**

```
GET /api/v1/spaces/:spaceId
```

**响应**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "id": 1,
    "name": "示例用户的个人空间",
    "type": 1,
    "description": null,
    "creatorId": 1,
    "creator": {
      "id": 1,
      "workId": "100001",
      "name": "示例用户"
    },
    "memberCount": 1,
    "agentCount": 5,
    "myRole": 1,
    "createdAt": "2026-03-17T10:00:00Z"
  }
}
```

### 3.2 获取空间成员列表

获取空间的所有成员（330版本：仅查看，不支持增删）。

**请求**

```
GET /api/v1/spaces/:spaceId/members
```

**Query 参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| page | int | 否 | 页码，默认1 |
| pageSize | int | 否 | 每页数量，默认20 |

**响应**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "items": [
      {
        "id": 1,
        "userId": 1,
        "user": {
          "id": 1,
          "workId": "100001",
          "name": "示例用户",
          "avatarUrl": "https://example.com/avatar.jpg"
        },
        "role": 1,
        "joinedAt": "2026-03-17T10:00:00Z"
      }
    ],
    "pagination": {
      "page": 1,
      "pageSize": 20,
      "total": 1,
      "totalPages": 1
    }
  }
}
```

### 3.3 获取用户所有空间

获取当前用户可访问的所有空间列表。

**请求**

```
GET /api/v1/spaces
```

**响应**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "items": [
      {
        "id": 1,
        "name": "示例用户的个人空间",
        "type": 1,
        "agentCount": 5,
        "myRole": 1,
        "createdAt": "2026-03-17T10:00:00Z"
      }
    ],
    "pagination": {
      "page": 1,
      "pageSize": 20,
      "total": 1,
      "totalPages": 1
    }
  }
}
```

---

## 4. Agent 接入 API

### 4.1 获取支持的 Agent 类型

获取系统支持的所有 Agent 类型及其接入方式。

**请求**

```
GET /api/v1/agent-types
```

**响应**

```json
{
  "code": 0,
  "message": "success",
  "data": [
    {
      "type": "ANT_CLAUDE_CODE",
      "name": "Ant Claude Code",
      "category": "BUILT_IN",
      "description": "内置版Claude Code",
      "icon": "https://example.com/icons/ant-claude-code.png",
      "cliCommand": "ant ai-vision connect --template \"ANT_CLAUDE_CODE\" --instance \"{instance_name}\" --key {api_key}",
      "sortOrder": 1,
      "platform": "ANT_CLOUD_CODE",
      "onboardingGuide": {
        "command": "ant ai-vision connect --template \"ANT_CLAUDE_CODE\" --instance \"{instance_name}\" --key {api_key}",
        "envVar": "AIVISION_API_KEY",
        "docs": "https://docs.example.com/ant-claude-code"
      }
    },
    {
      "type": "CLAUDECODE",
      "name": "Claude Code",
      "category": "BUILT_IN",
      "description": "Claude Code 原生客户端",
      "icon": "https://example.com/icons/claudecode.png",
      "cliCommand": "claude --config api-key={api_key} --instance \"{instance_name}\"",
      "sortOrder": 2,
      "platform": "CLOUD_CODE",
      "onboardingGuide": {
        "command": "export AIVISION_API_KEY=<YOUR_KEY> && claude",
        "envVar": "AIVISION_API_KEY",
        "docs": "https://docs.example.com/claudecode"
      }
    },
    {
      "type": "HOMICLAW",
      "name": "HomiClaw",
      "category": "BUILT_IN",
      "description": "HomiClaw 智能助手",
      "icon": "https://example.com/icons/homiclaw.png",
      "cliCommand": "homeclaw --connect --api-key {api_key} --instance \"{instance_name}\"",
      "sortOrder": 3,
      "platform": "HOMO_CLOUD",
      "onboardingGuide": {
        "command": "homeclaw --connect --api-key {api_key} --instance \"{instance_name}\"",
        "envVar": "AIVISION_API_KEY",
        "docs": "https://docs.example.com/homiclaw"
      }
    },
    {
      "type": "CUSTOM",
      "name": "自定义接入",
      "category": "CUSTOM",
      "description": "面向技术用户手动埋点接入",
      "icon": null,
      "cliCommand": null,
      "sortOrder": 99,
      "platform": "CUSTOM",
      "onboardingGuide": {
        "command": "# 使用 SDK 上报数据\naivision-sdk --api-key <YOUR_KEY>",
        "envVar": "AIVISION_API_KEY",
        "docs": "https://docs.example.com/custom"
      }
    }
  ]
}
```

**字段说明**

| 字段 | 类型 | 说明 |
|------|------|------|
| type | string | Agent类型编码 |
| name | string | Agent类型名称 |
| category | string | 分类: BUILT_IN(开箱即用), CUSTOM(自定义接入) |
| description | string | 描述 |
| icon | string | 图标URL |
| cliCommand | string | CLI接入命令模板，支持变量: {api_key}, {instance_name} |
| sortOrder | int | 排序权重，越小越靠前 |

### 4.2 创建 API Key

为指定空间创建新的 API Key，用于 Agent 接入。

**请求**

```
POST /api/v1/spaces/:spaceId/api-keys
```

**请求体**

```json
{
  "name": "我的 Claude Code Key",
  "agentType": "CLAUDECODE",
  "sourcePlatform": "CLOUD_CODE"
}
```

**响应**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "id": 1,
    "name": "我的 Claude Code Key",
    "key": "aiv_sk_abc123def456ghi789jkl012mno345pqr",  // 完整 Key，仅在创建时返回一次
    "keyPrefix": "aiv_sk_abc...",  // 用于展示的前缀
    "agentType": "CLAUDECODE",
    "sourcePlatform": "CLOUD_CODE",
    "status": 1,
    "expiresAt": null,
    "createdAt": "2026-03-18T10:00:00Z"
  }
}
```

> **重要**: 完整的 API Key 仅在创建时返回一次，请妥善保存！

### 4.3 获取空间的 API Key 列表

获取指定空间下的所有 API Key。

**请求**

```
GET /api/v1/spaces/:spaceId/api-keys
```

**响应**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "items": [
      {
        "id": 1,
        "name": "我的 Claude Code Key",
        "keyPrefix": "aiv_sk_abc...",
        "agentType": "CLAUDECODE",
        "sourcePlatform": "CLOUD_CODE",
        "status": 1,
        "lastUsedAt": "2026-03-18T09:30:00Z",
        "createdAt": "2026-03-18T10:00:00Z"
      }
    ],
    "pagination": {
      "page": 1,
      "pageSize": 20,
      "total": 1,
      "totalPages": 1
    }
  }
}
```

### 4.4 删除 API Key

删除指定的 API Key（软删除）。

**请求**

```
DELETE /api/v1/spaces/:spaceId/api-keys/:keyId
```

**响应**

```json
{
  "code": 0,
  "message": "success",
  "data": null
}
```

### 4.5 获取接入指南

获取 Agent 接入的详细指南（图文/视频）。

**请求**

```
GET /api/v1/agent-types/:agentType/onboarding-guide
```

**响应**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "agentType": "CLAUDECODE",
    "name": "Claude Code",
    "steps": [
      {
        "step": 1,
        "title": "创建 API Key",
        "description": "在 AI Vision 中创建一个 API Key",
        "action": "CREATE_KEY"
      },
      {
        "step": 2,
        "title": "复制配置命令",
        "description": "复制以下命令到终端执行",
        "action": "COPY_COMMAND",
        "command": "export AIVISION_API_KEY=<YOUR_KEY> && claude"
      },
      {
        "step": 3,
        "title": "开始使用",
        "description": "数据将自动上报到 AI Vision",
        "action": "COMPLETE"
      }
    ],
    "videoUrl": "https://example.com/videos/claudecode-onboarding.mp4"
  }
}
```

---

## 5. Agent 管理 API

### 5.1 获取空间下的 Agent 列表

获取指定空间下已注册的所有 Agent。

**请求**

```
GET /api/v1/spaces/:spaceId/agents
```

**Query 参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| agentType | string | 否 | 按类型过滤 |
| status | int | 否 | 按状态过滤 |
| page | int | 否 | 页码，默认1 |
| pageSize | int | 否 | 每页数量，默认20 |
| sort | string | 否 | 排序字段: lastActiveAt, name, createdAt |
| order | string | 否 | 排序方向: asc, desc |

**响应**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "items": [
      {
        "id": 1,
        "name": "Claude Code",
        "agentType": "CLAUDECODE",
        "customName": null,
        "version": "1.0.0",
        "instanceId": "MacBook-Pro",
        "status": 1,
        "lastActiveAt": "2026-03-18T09:30:00Z",
        "createdAt": "2026-03-17T10:00:00Z"
      }
    ],
    "pagination": {
      "page": 1,
      "pageSize": 20,
      "total": 5,
      "totalPages": 1
    }
  }
}
```

### 5.2 获取 Agent 详情

获取指定 Agent 的详细信息。

**请求**

```
GET /api/v1/spaces/:spaceId/agents/:agentId
```

**响应**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "id": 1,
    "name": "Claude Code",
    "agentType": "CLAUDECODE",
    "customName": null,
    "version": "1.0.0",
    "instanceId": "MacBook-Pro",
    "status": 1,
    "lastActiveAt": "2026-03-18T09:30:00Z",
    "apiKey": {
      "id": 1,
      "name": "我的 Claude Code Key",
      "keyPrefix": "aiv_sk_abc..."
    },
    "stats": {
      "totalCalls": 1234,
      "successRate": 98.5,
      "totalTokens": 567890,
      "lastActiveAt": "2026-03-18T09:30:00Z"
    },
    "createdAt": "2026-03-17T10:00:00Z"
  }
}
```

### 5.3 更新 Agent 自定义名称

更新 Agent 的自定义名称（用于多实例区分）。

**请求**

```
PATCH /api/v1/spaces/:spaceId/agents/:agentId
```

**请求体**

```json
{
  "customName": "工作电脑上的 Claude Code"
}
```

**响应**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "id": 1,
    "name": "Claude Code",
    "customName": "工作电脑上的 Claude Code",
    "agentType": "CLAUDECODE"
  }
}
```

### 5.4 删除 Agent

删除指定的 Agent（软删除）。

**请求**

```
DELETE /api/v1/spaces/:spaceId/agents/:agentId
```

**响应**

```json
{
  "code": 0,
  "message": "success",
  "data": null
}
```

---

## 6. 看板数据 API

### 6.1 获取看板聚合指标

获取空间下的聚合指标数据。

**请求**

```
GET /api/v1/spaces/:spaceId/dashboard/metrics
```

**Query 参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| startTime | string | 否 | 开始时间，ISO 8601格式，默认24小时前 |
| endTime | string | 否 | 结束时间，ISO 8601格式，默认当前时间 |
| agentType | string | 否 | 按Agent类型过滤 |

**响应**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "timeRange": {
      "start": "2026-03-17T10:00:00Z",
      "end": "2026-03-18T10:00:00Z"
    },
    "metrics": {
      "totalCalls": 5678,
      "successCalls": 5567,
      "failedCalls": 111,
      "successRate": 98.04,
      "totalTokens": 1234567,
      "inputTokens": 456789,
      "outputTokens": 777778,
      "agentCount": 5
    },
    "trend": {
      "callsChange": 12.5,
      "tokensChange": 8.3,
      "successRateChange": 1.2
    }
  }
}
```

### 6.2 获取 Top 消耗 Agent 排行

获取按 Token 消耗排序的 Agent 列表。

**请求**

```
GET /api/v1/spaces/:spaceId/dashboard/top-agents
```

**Query 参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| startTime | string | 否 | 开始时间 |
| endTime | string | 否 | 结束时间 |
| limit | int | 否 | 返回数量，默认10 |
| sortBy | string | 否 | 排序字段: tokens, calls，默认tokens |

**响应**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "timeRange": {
      "start": "2026-03-17T10:00:00Z",
      "end": "2026-03-18T10:00:00Z"
    },
    "items": [
      {
        "rank": 1,
        "agent": {
          "id": 1,
          "name": "Claude Code",
          "customName": null,
          "agentType": "CLAUDECODE"
        },
        "tokens": 456789,
        "calls": 1234,
        "successRate": 99.2
      },
      {
        "rank": 2,
        "agent": {
          "id": 2,
          "name": "OpenCode",
          "customName": "本地开发环境",
          "agentType": "OPENCODE"
        },
        "tokens": 234567,
        "calls": 567,
        "successRate": 97.5
      }
    ]
  }
}
```

### 6.3 获取最近异常会话

获取最近发生异常的会话列表。

**请求**

```
GET /api/v1/spaces/:spaceId/dashboard/recent-anomalies
```

**Query 参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| limit | int | 否 | 返回数量，默认10 |

**响应**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "items": [
      {
        "sessionId": "sess_abc123",
        "traceId": "trace_def456",
        "agent": {
          "id": 1,
          "name": "Claude Code",
          "agentType": "CLAUDECODE"
        },
        "errorType": "timeout",
        "errorMessage": "Connection timeout after 30s",
        "occurredAt": "2026-03-18T09:30:00Z",
        "traceUrl": "https://longfuse.example.com/traces/trace_def456"
      }
    ]
  }
}
```

### 6.4 获取调用量趋势

获取指定时间范围内的调用量趋势数据。

**请求**

```
GET /api/v1/spaces/:spaceId/dashboard/trend
```

**Query 参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| startTime | string | 否 | 开始时间 |
| endTime | string | 否 | 结束时间 |
| granularity | string | 否 | 粒度: hour, day，默认hour |
| agentType | string | 否 | 按Agent类型过滤 |

**响应**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "timeRange": {
      "start": "2026-03-17T10:00:00Z",
      "end": "2026-03-18T10:00:00Z"
    },
    "granularity": "hour",
    "dataPoints": [
      {
        "timestamp": "2026-03-17T10:00:00Z",
        "calls": 45,
        "tokens": 12345,
        "successRate": 100.0
      },
      {
        "timestamp": "2026-03-17T11:00:00Z",
        "calls": 52,
        "tokens": 14567,
        "successRate": 98.1
      }
    ]
  }
}
```

### 6.5 获取 Token 消耗分布

获取按 Agent 类型分组的 Token 消耗分布。

**请求**

```
GET /api/v1/spaces/:spaceId/dashboard/token-distribution
```

**Query 参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| startTime | string | 否 | 开始时间 |
| endTime | string | 否 | 结束时间 |

**响应**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "timeRange": {
      "start": "2026-03-17T10:00:00Z",
      "end": "2026-03-18T10:00:00Z"
    },
    "total": 1234567,
    "distribution": [
      {
        "agentType": "CLAUDECODE",
        "agentTypeName": "Claude Code",
        "tokens": 456789,
        "percentage": 37.0
      },
      {
        "agentType": "OPENCODE",
        "agentTypeName": "OpenCode",
        "tokens": 345678,
        "percentage": 28.0
      }
    ]
  }
}
```

---

## 7. 设置 API

### 7.1 获取空间设置

获取空间的设置信息（330版本：仅查看）。

**请求**

```
GET /api/v1/spaces/:spaceId/settings
```

**响应**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "space": {
      "id": 1,
      "name": "示例用户的个人空间",
      "type": 1
    },
    "api_key": {
      "key_prefix": "aiv_live_xiao",
      "status": 1
    },
    "langfuse": {
      "project_id": "IF_PROJ_XIAOYANG_PERSONAL",
      "project_name": "xiaoyang-personal",
      "mapped": true
    },
    "permissions": {
      "canManageMembers": false,
      "canDeleteSpace": false,
      "canManageKeys": true
    },
    "features": {
      "memberManagement": false,
      "teamDashboard": false,
      "smartDiagnosis": false
    }
  }
}
```

**字段说明**

| 字段 | 类型 | 说明 |
|------|------|------|
| space | object | 空间基础信息 |
| api_key | object | API Key信息 |
| api_key.key_prefix | string | Key前缀（用于展示） |
| api_key.status | int | 状态: 1-激活, 0-禁用 |
| langfuse | object | Langfuse集成信息 |
| langfuse.project_id | string | Langfuse项目ID |
| langfuse.project_name | string | Langfuse项目名称 |
| langfuse.mapped | bool | 是否已映射 |
| permissions | object | 权限信息 |
| features | object | 功能开关 |

---

## 8. Trace 跳转 API

### 8.1 获取 Trace 跳转 URL

生成跳转到 Long Fuse 的 Trace 详情页 URL。

**请求**

```
GET /api/v1/traces/:traceId/url
```

**Query 参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| spaceId | int | 是 | 空间ID |
| sessionId | string | 否 | 会话ID |

**响应**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "traceId": "trace_def456",
    "url": "https://longfuse.example.com/traces/trace_def456?spaceId=1",
    "expiresAt": "2026-03-18T11:00:00Z"
  }
}
```

---

## 9. 数据上报 API (内部)

> 以下 API 供 Agent SDK 上报数据使用，不直接暴露给前端

### 9.1 上报 Session 数据

Agent SDK 上报 Session 使用数据。

**请求**

```
POST /api/v1/ingest/session
```

**Headers**

```
Authorization: Bearer aiv_sk_xxx
X-Agent-Type: CLAUDECODE
X-Agent-Version: 1.0.0
X-Instance-Id: MacBook-Pro
```

**请求体**

```json
{
  "sessionId": "sess_abc123",
  "traceId": "trace_def456",
  "status": "success",
  "inputTokens": 1234,
  "outputTokens": 5678,
  "totalTokens": 6912,
  "durationMs": 5432,
  "model": "claude-3-opus",
  "errorType": null,
  "errorMessage": null,
  "timestamp": "2026-03-18T09:30:00Z"
}
```

**响应**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "received": true,
    "agentId": 1
  }
}
```

---

## 10. 健康检查 API

### 10.1 服务健康检查

**请求**

```
GET /api/v1/health
```

**响应**

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "status": "healthy",
    "version": "1.0.0",
    "timestamp": "2026-03-18T10:00:00Z",
    "components": {
      "database": "healthy",
      "cache": "healthy"
    }
  }
}
```

---

## 11. 前端页面路由与 API 映射

| 前端页面 | 调用的 API |
|----------|-----------|
| 首页/看板 | GET /user/me, GET /spaces/:id/dashboard/metrics, GET /spaces/:id/dashboard/top-agents, GET /spaces/:id/dashboard/recent-anomalies |
| Agent 接入 | GET /agent-types, POST /spaces/:id/api-keys, GET /spaces/:id/api-keys |
| Agent 列表 | GET /spaces/:id/agents |
| Agent 详情 | GET /spaces/:id/agents/:agentId, PATCH /spaces/:id/agents/:agentId |
| 设置 | GET /spaces/:id/settings, GET /spaces/:id/members |
| Trace 跳转 | GET /traces/:traceId/url |

---

## 12. 版本历史

| 版本 | 日期 | 修改内容 |
|-----|------|---------|
| v1.0 | 2026-03-18 | 初版设计，基于330需求范围 |
