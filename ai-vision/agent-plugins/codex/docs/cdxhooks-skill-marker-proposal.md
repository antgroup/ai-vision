# Codex Skill 采集增强方案

## 背景

### 当前状态

cdxhooks 已经具备基础的 skill 识别能力，通过 transcript 解析三路检测 skill 名称（命令路径、user message link、`<skill>` 标签），并将该 turn 内的所有工具调用标记为 `agentic.tool.type=skill`。但存在两个问题：

1. **skill 本身没有独立的 span。** 当前 skill 的可见性完全依附于工具调用——一个 turn 里有 10 个 tool Bash，全部被标记为 `skill:xxx`，但用户看不到 skill 从哪里开始、到哪里结束、总共花了多少时间。

2. **skill 的元数据/结果无法记录。** skill 执行过程中的上下文信息（模式、参数、产出、成功/失败状态）没有标准化的记录方式。

### 参考方案

Claude Code cchooks 已通过**文本标记协议**实现了 skill 采集增强（见 Claude Code Skill 采集增强（参见本仓库对应规范） 和 Skill 可观测采集使用手册（参见本仓库对应规范））。核心机制：skill 在正文中输出 `[[skill_start:<name>,...]]` / `[[skill_end:<name>,...]]` 标记，cchooks 在 Stop 时解析 transcript 并创建平铺的 skill span。

### 核心问题

cdxhooks 缺少 skill marker 的解析和 span 构建能力，导致 skill 在 trace 中不可见。

## 方案设计

### 核心机制

**Skill 在正文中输出标记，cdxhooks 在 Stop 时解析 transcript 并创建平铺 skill span。**

```
Skill 执行
  │
  │ 输出: [[skill_start:demo-skill,agentic.skill.meta='{"mode":"poc"}',tags='poc,demo']]
  │ ...（期间的 LLM/工具调用照常）...
  │ 输出: [[skill_end:demo-skill,status.code=OK,agentic.skill.result='{"outcome":"success"}']]
  │
  ▼
transcript JSONL
  │
  │ Stop hook 触发
  ▼
cdxhooks
  │ 解析 assistant 文本中的 marker
  │ 创建 skill:start / skill:end 平铺 span
  │ 跨 turn 耗时计算
  │ 标签注入到 root span
  ▼
Langfuse
```

### Trace 展示效果

```
Turn 1
├── chat gpt-5.4 ────────────────── 2.3s
├── ⚡ skill:start demo-skill          ← 标记 skill 开始
│     agentic.skill.meta = {"mode":"poc"}
├── tool Bash (pwd) ────────────── 0.5s
├── tool Bash (git status) ──────── 0.8s
├── chat gpt-5.4 ────────────────── 3.1s
├── tool Bash (npm test) ────────── 4.2s
├── ✅ skill:end demo-skill            ← 标记 skill 结束
│     agentic.skill.result = {"outcome":"success","tests":42}
│     agentic.skill.duration_ms = 8600ms
│     status = OK
├── chat gpt-5.4 ────────────────── 1.2s
└── tool Bash (echo done) ───────── 0.1s
```

两个 span 都是 turn root 的 sibling，**不重父化**任何子调用。中间的工具调用该怎么展示还怎么展示，skill marker 只是提供了"这里是 skill 的执行范围"的上下文。

### 标记语法

```
[[skill_start:<name>,<k1>=<v1>,<k2>=<v2>,...]]
[[skill_end:<name>,<k1>=<v3>,...]]
```

- 冒号后第一个字段是 `<name>`
- 其余逗号分隔的字段是 `k=v`
- key/value 按**第一个** `=` 切分
- 值含 `,` / `]]` / JSON 时用引号包裹（单引号 `'...'` 或双引号 `"..."`）
- start 和 end 的 `<name>` 一致即可跨 turn 配出整段耗时

### 跨 turn 耗时

通过 state stash 实现：start marker 把启动时间暂存（按 skill name 索引），end marker 回读计算 `end - start`。即使 start/end 分布在不同 turn 也成立。

```
Turn 1:
  skill:start demo-skill → stash: {name: "demo-skill", start_ns: "...", ...}
  (用户 ESC 中断)

Turn 2:
  skill:end demo-skill → 读取 stash → duration_ms = end_ns - start_ns
```

### 三个保留键

| 保留键 | 行为 |
|---|---|
| `status.code` | 设置 `skill:end` span 的状态（`OK` / `ERROR`），不落属性 |
| `status.message` | 设置 `skill:end` span 的状态消息，不落属性 |
| `tags` | split 后渲染到所属 turn 的 root span `langfuse.trace.tags`，不落 marker span 属性 |

### 推荐字段

| 字段 | 用在 | 含义 |
|---|---|---|
| `agentic.skill.meta` | `skill_start` | skill 启动元数据（整段 JSON 字符串） |
| `agentic.skill.result` | `skill_end` | skill 结果（整段 JSON 字符串） |

### 键名策略

- `agentic.skill.*`、`gen_ai.*`、`ant.*`、`service.*`、`telemetry.*`、`user.*` 及 OTel 标准键 → 原名落属性
- 命名空间外的键 → 自动归到 `agentic.skill.meta.<键>`
- 三个保留键 `status.code` / `status.message` / `tags` 被特殊处理，不落属性

### 特殊字符处理

| 特殊字符 | 处理方式 | 示例 |
|---|---|---|
| 逗号 `,` | 用引号包裹值 | `error.type="123,345"` |
| JSON 值 | 用单引号裹整段 | `agentic.skill.meta='{"id":"1"}'` |
| 多值 tags | 用引号包裹 | `tags='A,B,C'` |
| `]]` 在值中 | 用引号包裹值 | `error.type="connect failed]] retry"` |
| 等号 `=` | 无需处理 | `expr=a=b+c` |

### 健壮性

- **缺 end**（模型没输出 / 被截断）→ 只产出 `skill:start` span，不报错、不卡会话
- **孤儿 end**（无对应 start）→ 只产出 `skill:end` span，无 `agentic.skill.duration_ms`，不报错
- **引号未配对**→ 只影响当前 marker，不会吞掉后续 marker（解析器回退到最近字面 `]]`）

## 代码变更

### 新增文件

| 文件 | 说明 |
|---|---|
| `cdxhooks/src/common/skill_marker.rs` | Skill marker 解析器：扫描文本、提取 `[[skill_start:...]]` / `[[skill_end:...]]`、解析 k=v 对 |

### 修改文件

| 文件 | 改动 |
|---|---|
| `cdxhooks/src/common/mod.rs` | 注册 `skill_marker` 模块 |
| `cdxhooks/src/common/otel.rs` | 新增 `build_skill_span()`；在 `build_otel_export_request_for_turn()` 中扫描 assistant 文本、创建 skill span；跨 turn 耗时通过 state stash 计算 |
| `cdxhooks/src/hooks/langfuse/langfuse_handler.rs` | Stop 时读写 skill stash；Stop 后清理过期 stash |
| `cdxhooks/src/hooks/langfuse/state.rs` | 新增 `skill_stash` 字段的读写函数 |

### 解析逻辑

```rust
// skill_marker.rs
struct SkillMarker {
    marker_type: SkillMarkerType,  // Start / End
    name: String,
    fields: HashMap<String, String>,
    position: usize,  // 标记在 assistant 文本中的位置
    assistant_index: usize,  // 属于哪个 assistant
}

// 扫描 assistant 文本，识别 [[skill_start:...]] / [[skill_end:...]]
fn parse_markers(assistants: &[AssistantMessage]) -> Vec<SkillMarker>
```

### Span 构建

```rust
// otel.rs
fn build_skill_span(
    marker: &SkillMarker,
    marker_type: SkillMarkerType,
    turn: &Turn,
    session_id: &str,
    username: &str,
    stash: &SkillStash,  // 跨 turn 耗时
) -> OtelSpan {
    // span name: "skill:start <name>" 或 "skill:end <name>"
    // attributes: 解析 fields 中的 k=v，按键名策略落属性
    // status: 从 status.code 解析
    // duration_ms: end 时从 stash 计算
}
```

### 与现有 skill 检测的关系

cdxhooks 现有的 skill 检测（三路识别 `skill_name`）**保持不变**，继续用于工具调用的 `agentic.tool.type=skill` 标记。Skill marker 是**新增的独立能力**，两者并存：

- **现有 skill 检测**：工具调用级别的 skill 归属（"这个 Bash 是 demo-skill 调用的"）
- **Skill marker**：skill 级别的生命周期标记（"demo-skill 从这开始，到那结束，总计 8.6s"）

## 限制

| 限制 | 说明 |
|---|---|
| **仅解析 assistant 正文** | marker 必须在正文 text block 中，不能放在 thinking/reasoning 里 |
| **marker 必须同一物理行** | `[[...]]` 中间不能换行 |
| **skill 作者需改 SKILL.md** | 需要在 skill 指令中增加 marker 输出要求（参考使用手册） |
| **不改变 span 层级** | marker span 是平铺 sibling，不重父化任何子调用 |

## 示例：Skill 作者接入

skill 作者在 SKILL.md 中增加两段指令：

**最前面：**
```markdown
在你开始执行本 skill 的实质工作之前，在正文中输出：
[[skill_start:demo-skill,agentic.skill.meta='{"mode":"poc"}',tags='poc,demo']]
```

**最后面：**
```markdown
在你完成本 skill 的全部工作之后，在正文中输出：
- 成功：[[skill_end:demo-skill,status.code=OK,agentic.skill.result='{"outcome":"success"}']]
- 失败：[[skill_end:demo-skill,status.code=ERROR,status.message="<错误原因>"]]
```

## 验证要点

- [ ] `[[skill_start:...]]` 被正确解析为 `skill:start <name>` span
- [ ] `[[skill_end:...]]` 被正确解析为 `skill:end <name>` span
- [ ] `agentic.skill.meta` / `agentic.skill.result` 原样落属性
- [ ] `status.code=OK/ERROR` 正确设置 span 状态
- [ ] `tags='A,B,C'` 正确渲染到 root span 的 `langfuse.trace.tags`
- [ ] 跨 turn 耗时正确（start/end 在不同 turn）
- [ ] 缺 end 时不报错、不卡会话
- [ ] 孤儿 end 时不报错
- [ ] 无 marker 的 turn 行为完全不变
