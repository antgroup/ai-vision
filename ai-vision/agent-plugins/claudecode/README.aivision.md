# AIVision Identity Patch Support

本目录下的三个 Langfuse hook 现已支持通过环境变量注入 AIVision 身份补丁字段。

## 支持的环境变量

```bash
AIVISION_ANT_USERNAME=100001
```

## 字段映射

依据 [identity-patch.md](../docs/standard/custom/identity-patch.md) 的 Trace 语义定义，这些环境变量会写入 Langfuse trace `resourceAttributes`：

| 环境变量 | 语义字段 | Langfuse resourceAttributes 键 |
| --- | --- | --- |
| `AIVISION_ANT_USERNAME` | `ant.username` | `ant.username` |
| 内置常量 | `ant.agent.type` | `ant.agent.type` |
| 内置常量 | `ant.agent.product` | `ant.agent.product` |
| 主机名自动推导 | `ant.agent.instance` | `ant.agent.instance` |

## 与现有元数据机制的关系

- `LANGFUSE_METADATA` 仍然支持 JSON 批量注入自定义 metadata。
- `LANGFUSE_ATTR_*` 仍然支持注入单个 metadata 字段。
- `AIVISION_ANT_USERNAME` 是接入方唯一需要设置的身份字段。
- Langfuse 连接配置、`ant.agent.type`、`ant.agent.product` 已内置在 Zig 二进制中，`ant.agent.instance` 由主机名自动推导。
- 旧变量 `LANGFUSE_ATTR_USERNAME`、`LANGFUSE_ATTR_ANT_USERNAME`、`AIVISION_AGENT_*`、`AIVISION_ANT_AGENT_*` 不再用于这些预置属性。

如果同名 metadata 同时通过多种方式设置，当前实现会优先使用资源属性里的 `ant.*` 值。

## 示例

```bash
AIVISION_ANT_USERNAME=100001
```

上报后的 trace `resourceAttributes` 示例：

```json
{
  "service.name": "antclaudecode",
  "ant.username": "100001",
  "ant.agent.type": "claudecode",
  "ant.agent.product": "antclaudecode",
  "ant.agent.instance": "your-hostname"
}
```
