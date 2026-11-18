import { query, tool, createSdkMcpServer } from "@anthropic-ai/claude-agent-sdk";
import { randomBytes } from "node:crypto";
import { fileURLToPath } from "node:url";
import { z } from "zod";
import { analysisSchema } from "./shared/types.js";

const envhooksPath = fileURLToPath(new URL("../../scripts/envhooks.py", import.meta.url));
const envhooksCommand = `python3 '${envhooksPath.replaceAll("'", "'\\''")}'`;
const prompt = process.argv[2] ?? `请用 Bash 工具执行 ${envhooksCommand} 三次，每次执行后告诉我返回结果`;

/** 生成 W3C TRACEPARENT，每次调用独立的 trace_id + parent_span_id */
function generateTraceparent(): string {
  const traceId = randomBytes(16).toString("hex");
  const parentSpanId = randomBytes(8).toString("hex");
  return `00-${traceId}-${parentSpanId}-01`;
}

/** 为每次 query() 调用创建独立的 trace 环境变量（process.env + trace 增量） */
function createTraceEnv(): Record<string, string> {
  const traceparent = generateTraceparent();
  console.log(`[TRACEPARENT] ${traceparent}`);
  return {
    ...Object.fromEntries(
      Object.entries(process.env).filter((e): e is [string, string] => e[1] != null),
    ),
    SOFA_TRACEID: `0a1b2c3d${Date.now()}${Math.floor(Math.random() * 100000)}`,
    SOFA_RPCID: "0.1.2.3",
    TRACEPARENT: traceparent,
    // CCHOOKS_DEBUG: "true",
    SCENE: "sdk",
  };
}

// ---------------------------------------------------------------------------
// 场景 A: 基础调用 — query + for await
// ---------------------------------------------------------------------------
async function basicCall(prompt: string): Promise<void> {
  console.log("=== 场景 A: 基础调用 (query + for await) ===\n");

  for await (const message of query({
    prompt,
    options: {
      permissionMode: "dontAsk",
      allowedTools: ["Bash(python3*)"],
      maxTurns: 5,
      env: createTraceEnv(),
      settingSources: ["user"],
    },
  })) {
    if (message.type === "assistant" && message.message?.content) {
      for (const block of message.message.content) {
        if ("text" in block) {
          console.log("回答:", block.text);
        }
      }
    } else if (message.type === "result") {
      if (message.subtype === "success") {
        console.log("费用: $" + message.total_cost_usd);
      } else {
        console.error("错误:", message.subtype);
      }
    }
  }

  console.log();
}

// ---------------------------------------------------------------------------
// 场景 B: 结构化输出 — outputFormat + json_schema
// ---------------------------------------------------------------------------
async function structuredCall(prompt: string): Promise<void> {
  console.log("=== 场景 B: 结构化输出 (outputFormat) ===\n");

  for await (const message of query({
    prompt,
    options: {
      permissionMode: "dontAsk",
      maxTurns: 5,
      env: createTraceEnv(),
      settingSources: ["user"],
      outputFormat: {
        type: "json_schema",
        schema: analysisSchema,
      },
    },
  })) {
    if (message.type === "result") {
      if (message.subtype === "success") {
        console.log("结构化结果:", JSON.stringify(message.structured_output, null, 2));
        console.log("文本结果:", message.result);
        console.log("费用: $" + message.total_cost_usd);
      } else {
        console.error("错误:", message.subtype);
      }
    }
  }

  console.log();
}

// ---------------------------------------------------------------------------
// 场景 C: 自定义工具 — tool + createSdkMcpServer
// ---------------------------------------------------------------------------
async function customToolCall(prompt: string): Promise<void> {
  console.log("=== 场景 C: 自定义工具 (MCP tool) ===\n");

  const getTimestamp = tool(
    "get_timestamp",
    "获取当前时间戳和格式化时间",
    { timezone: z.string().describe("时区，如 Asia/Shanghai").optional() },
    async ({ timezone }) => {
      const now = new Date();
      const formatted = now.toLocaleString("zh-CN", {
        timeZone: timezone ?? "Asia/Shanghai",
      });
      return {
        content: [{
          type: "text" as const,
          text: JSON.stringify({ timestamp: now.toISOString(), formatted }),
        }],
      };
    },
  );

  const server = createSdkMcpServer({
    name: "demo-tools",
    tools: [getTimestamp],
  });

  for await (const message of query({
    prompt,
    options: {
      mcpServers: { "demo-tools": server },
      allowedTools: ["mcp__demo-tools__get_timestamp"],
      permissionMode: "dontAsk",
      maxTurns: 3,
      env: createTraceEnv(),
      settingSources: ["user"],
    },
  })) {
    if (message.type === "assistant" && message.message?.content) {
      for (const block of message.message.content) {
        if ("text" in block) {
          console.log("回答:", block.text);
        } else if ("name" in block) {
          console.log(`[调用工具: ${block.name}]`);
        }
      }
    } else if (message.type === "result") {
      if (message.subtype === "success") {
        console.log("费用: $" + message.total_cost_usd);
      } else {
        console.error("错误:", message.subtype);
      }
    }
  }

  console.log();
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------
async function main() {
  console.log(`Prompt: "${prompt}"`);
  console.log("每次调用生成独立的 TRACEPARENT / SOFA_TRACEID\n");

  await basicCall(prompt);
//   await structuredCall(`请分析以下主题并给出摘要和要点: ${prompt}`);
//   await customToolCall("现在几点了？请调用 get_timestamp 工具获取当前时间并告诉我");
}

main().catch((err) => {
  console.error("Error:", err.message);
  process.exit(1);
});
