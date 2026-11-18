# VCS Resource 属性

E1 Git 仓库信息识别复用以下已存在的 Resource 属性。它们只在 runtime 或显式 replay 输入提供可信事实时条件上报；没有事实时省略，不写空值或推测值。

| 属性 | 类型 | 说明 |
|---|---|---|
| `vcs.repository.url.full` | string | 当前工作区选定的 Git 远端完整 URL |
| `vcs.ref.head.name` | string | 当前 HEAD 的分支/引用名；detached HEAD 可为 `HEAD` |
| `vcs.ref.head.revision` | string | 当前 HEAD commit revision；可选增强字段 |

多远端仓库优先选择 `origin` 的 fetch 地址，再按远端名称与地址稳定排序；不按托管服务商名称调整优先级。

隐私边界：这些属性可能暴露仓库地址、组织名、项目名、分支名或 commit 关联信息。生产采集应遵守组件的 collection mode 和部署数据使用策略；离线 replay 不得从 replay 进程当前工作目录探测并改变输入事实。

`vcs.*` Resource namespace 已纳入 `docs/semconv/attribute_groups.yaml` 的 `registry.vcs`，当前属性均为条件/可选字段，不要求所有 agent 强制发射。E2 的 W3C TRACEPARENT 传播规则见 [`docs/capabilities/E-upstream-trace-context.md`](../capabilities/E-upstream-trace-context.md)；E3 的外部触发归因规则见 [`docs/capabilities/E-meta-trigger.md`](../capabilities/E-meta-trigger.md)。后续若把 E1/E2/E3/E4 升级为机器强制验证，应先以所有纳入矩阵的 replay 做 dry-run。
