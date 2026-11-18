export interface GoldenScenario { id: string; prompt: string; optional?: 'subagent' | 'mcp' | 'skill'; }
export const GOLDEN_SCENARIOS: GoldenScenario[] = [
  { id: 'basic-llm', prompt: '你好，用一句话介绍你自己。' },
  { id: 'tool-success', prompt: '读取当前目录下的 README.md 文件，告诉我文件有多少行。' },
  { id: 'tool-error', prompt: '读取 /nonexistent/this-file-does-not-exist-12345.txt 的内容。' },
  { id: 'multi-tool', prompt: '在当前目录创建一个 /tmp/golden-test.py 文件，内容是 print("hello")，然后执行它，最后删除它。' },
  { id: 'subagent', prompt: '启动一个前台子 agent 执行 sleep 5 && echo done，同时你继续告诉我当前时间。', optional: 'subagent' },
  { id: 'mcp-tool', prompt: '使用已配置的 MCP 搜索工具检索一条公开文档，并告诉我标题。', optional: 'mcp' },
  { id: 'skill-command', prompt: '/skill:find-skills 查找一个能清理大文件的 Skill', optional: 'skill' },
  { id: 'skill-conversation', prompt: '使用 find-skills 帮我查找一个能清理大文件的 Skill', optional: 'skill' }
];
