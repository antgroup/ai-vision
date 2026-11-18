# 安装与使用

AI Vision 提供空间范围内的 OpenAPI，用于查询 Trace 可观测数据。

## API

### 获取 API Key

1. 访问当前 AI Vision 部署，本地示例为 `http://localhost:5174`。
2. 选择需要查询的空间。
3. 点击导航中的 **空间设置**。
4. 进入 **Space Keys**，创建或管理当前空间的 API Key。
5. 创建后立即复制并妥善保存完整 Key；完整 Key 只在创建时展示一次。

调用 OpenAPI 时，在请求头中传入该 Key：

```http
Authorization: Bearer <API_KEY>
Content-Type: application/json
```

请求路径中的 `space_id` 必须与 API Key 所属空间一致。接口地址、认证规则和调用示例参见：

- [OpenAPI 使用文档](./README.md)
- [认证与权限](./1_authentication.md)
- [curl 示例](./5_api_curl.md)

## Agent 采集插件

采集插件从当前部署的 Agent 市场安装：选择空间 → Agent 接入 → 查看接入说明 → 生成并复制安装命令。命令中的授权与当前空间、Agent 绑定。

此流程用于采集；读取 OpenAPI 使用上面的空间 Key。无需下载外部平台的历史 CLI 安装器。
