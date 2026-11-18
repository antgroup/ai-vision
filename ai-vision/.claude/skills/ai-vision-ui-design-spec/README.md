# AI Vision UI 设计规范

让 AI Coding 工具按照设计师定义的 UI 规范生成前端代码。

覆盖：**颜色、字体、圆角、间距** 四大设计系统，含可直接使用的 CSS 变量模板。

## 安装

根据你使用的 AI Coding 工具，选择对应方式：

### Claude Code

```bash
cp -r ai-vision-ui-design-spec ~/.claude/skills/
```

使用时输入 `/ai-vision-ui-design-spec` 加载规范。

### Codex CLI / OpenCode

将以下文件复制到项目根目录：

```bash
cp AGENTS.md 你的项目/
cp -r references 你的项目/
```

### Cursor

```bash
cp AGENTS.md 你的项目/.cursorrules
cp -r references 你的项目/
```

## 验证是否生效

安装后让 AI 写一个按钮，检查输出是否符合：

- 圆角 `10px`（不是 4px 或 8px）
- 主按钮背景 `rgba(0, 10, 26, 0.88)`（不是蓝色）
- 字号 `14px`，字重 `500`

## 文件说明

| 文件 | 作用 |
|------|------|
| `SKILL.md` | Claude Code 入口 |
| `AGENTS.md` | Codex / OpenCode / Cursor 入口 |
| `references/design-tokens.md` | 完整设计 Token（所有工具共用） |

## 规范更新

如设计师更新了规范，替换 `references/design-tokens.md` 即可，入口文件无需修改。
