# AI Vision · Claude Code 工作说明

> 项目 facts、工程守卫、长期规则唯一权威是 `./AGENTS.md`。本文档不重复 facts，
> 只记录 Claude Code 工作方式相关的事项（红线、seed 安全、验证建议、prompt 模板）。

## 强制红线

1. **绝对不要修改 `vfe/src/components/ui/`**。这是 shadcn/ui 组件库，只能经 `pnpm dlx shadcn@latest add/update` 升级。自定义组件放 `vfe/src/components/`（不在 `ui/` 内）或 `vfe/src/agents/`；不要 wrap/override 库内组件。
2. **不要不经用户许可运行任何 database seed 命令**（如 `seed_antcc_dashboard`、`seed_*_dashboard`）——会覆盖既有数据库配置。可改 seed 文件供未来部署；在线调整应直接通过 Django ORM / shell 修改 records，而不是 seed 全量重置。

## 工作方式

### 需求接入

Phase 1 · 需求理解（**不要先动刀**）：

- Requirement Summary
- Acceptance Criteria
- Constraints & Dependencies
- Affected Areas
- Ambiguities / Open Questions
- Suggested Implementation Direction
- Architecture Notes

要点：
- 区分显式要求与推断默认；不要靠想象补产品细节。
- 早期把架构敏感风险摆出来，不藏在实现里。
- 用户没明确"实现"前，只输出分析，不动代码。

Phase 2 · 实现：

- 按 Phase 1 的 scope 和 acceptance baseline 做最小架构一致的实现。
- 先看近旁模式（models / services / views / API helpers / 共享类型 / page）再动。
- 跨层改动必须同步：models / serializers / views / migrations（如必要）/ 前端 API helpers / 共享类型 / 具体调用方 / 权限边界。
- 出现隐含假设要显式指出，不要静默带入。

### 前端改动验证

- 如果 browser / devtools 工具可用，打开本地前端的改动路径。Vite 默认地址为 `http://localhost:5174/`。
- 检查渲染、console 错误、网络失败、关键交互。
- 发现问题就继续修，不要停在"代码看起来对"。
- **不要声明 "已浏览器验证"，除非确实打开页面验证过**。

### 完成时的汇报

最终总结要给出：

- 改了什么 / 为什么选这条实现路径
- 隐含假设
- 实际跑了什么验证（typecheck、单测、回归集、浏览器）
- 仍需人工核对的风险点

不要夸大胆能力，没跑过的不要写成跑了。

## AI Vision 跨层改动 checklist

涉及行为变化的改动，至少把以下层串起来过一遍：

- Django models
- serializers
- views / query views
- migrations（如需）
- 前端 API helper
- 共享前端类型
- 具体调用方（page / component / hook）
- 权限与 space 接入边界

任何一层"悄悄不同步"，任务都不算完成。

## Prompt 模板

### A. 仅分析需求，不动代码

```text
对照当前 AI Vision 仓库分析这个需求。
不要修改代码。

请输出：
1. Requirement Summary
2. Acceptance Criteria
3. Constraints & Dependencies
4. Affected Areas
5. Ambiguities / Open Questions
6. Suggested Implementation Direction
7. Architecture Notes

需求内容或链接：
<贴这里>
```

### B. 实现需求

```text
对照 AGENTS.md 的规则 在当前 AI Vision 仓库实现这个需求。

要求：
- 不硬编码数据，不引入 ticket-specific hack
- 不改变现有架构
- 复用现有 models / serializers / views / API helpers / 共享类型 / page 模式
- 跨层一致（models / serializers / views / migrations / 前端 API / 共享类型 / 调用方）
- 不要声明没跑过的验证

需求内容：
<贴这里>
```

### C. 实现 + 浏览器自验证

```text
在 AI Vision 仓库实现这个需求，并用 browser/devtools 自验证本地页面。

要求同上 +：
- 用本地前端的实际地址验证改动路径（Vite 默认 http://localhost:5174/）
- 发现渲染 / console / 网络 / 交互问题就继续修
- 总结里列出 changed files、assumptions、实际跑过的验证、剩余手动核对点

需求内容：
<贴这里>
```
