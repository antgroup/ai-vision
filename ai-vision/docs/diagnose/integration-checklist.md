# 诊断联调

环境配置与启动见 [Docker 部署](https://aivision.mintlify.site/zh-CN/deployment/docker)。AgentLens 使用 `AGENTLENS_OB_URL` 访问业务库，`SIGNOZ_OB_URL` 读取遥测库；结果通过认证 OTLP 网关回写。

先确认 `aiv status` 中所用服务就绪、前端可访问，再完成一次 Agent 上报，并在查询结果中找到对应 Trace；这些检查通过后再验证诊断链路。状态与 `aiv logs` 检查保留部署时的 target、namespace、workdir、blueprint，或已有 Compose 文件的准确 file/env 参数，避免查询到另一套服务。

验证时在隔离空间安装 Agent、上报时间窗口内的 Trace，触发诊断，检查 Task 实际诊断数、diagnosis-stats、BadCase 与质量看板。仅成功状态或空批次不代表有诊断数据。

个人空间使用内置每小时任务，取数延迟 3 小时；团队可以创建任务。测试近期数据时必须让事件时间进入任务窗口，不更改用户任务或伪造看板结果。

Langfuse 只提供现有观测能力，诊断仍需 SigNoz。LLM 诊断另外依赖模型连接配置；规则诊断可独立验证数据链路。
