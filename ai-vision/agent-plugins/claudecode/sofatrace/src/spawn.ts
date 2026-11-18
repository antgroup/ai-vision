import { execFile, spawn } from "node:child_process";
import { promisify } from "node:util";
import { randomUUID } from "node:crypto";
import { type ClaudeResponse, analysisSchema } from "./shared/types.js";

const execFileAsync = promisify(execFile);

const prompt = process.argv[2] ?? "用一句话解释什么是 TypeScript";

/** 为每次 claude -p 调用创建独立的 trace 环境变量 */
function createTraceEnv(): NodeJS.ProcessEnv {
  return {
    ...process.env,
    SOFA_TRACEID: `0a1b2c3d${Date.now()}${Math.floor(Math.random() * 100000)}`,
    SOFA_RPCID: "0.1.2.3",
    // CCHOOKS_DEBUG: "true",
    SCENE: "spawn",
  };
}

// ---------------------------------------------------------------------------
// 场景 A: 基础调用 — execFile + JSON 输出
// ---------------------------------------------------------------------------
async function basicCall(prompt: string): Promise<ClaudeResponse> {
  console.log("=== 场景 A: 基础调用 (execFile + JSON) ===\n");

  const traceEnv = createTraceEnv();
  const { stdout } = await execFileAsync("claude", [
    "-p", prompt,
    "--output-format", "json",
    "--session-id", randomUUID(),
  ], { maxBuffer: 10 * 1024 * 1024, env: traceEnv });

  const response: ClaudeResponse = JSON.parse(stdout);

  console.log("回答:", response.result);
  console.log("费用: $" + response.total_cost_usd);
  console.log("Session:", response.session_id);
  console.log();

  return response;
}

// ---------------------------------------------------------------------------
// 场景 B: 结构化输出 — --json-schema
// ---------------------------------------------------------------------------
async function structuredCall(prompt: string): Promise<void> {
  console.log("=== 场景 B: 结构化输出 (--json-schema) ===\n");

  const traceEnv = createTraceEnv();
  const { stdout } = await execFileAsync("claude", [
    "-p", prompt,
    "--output-format", "json",
    "--json-schema", JSON.stringify(analysisSchema),
    "--session-id", randomUUID(),
  ], { maxBuffer: 10 * 1024 * 1024, env: traceEnv });

  const response: ClaudeResponse = JSON.parse(stdout);

  console.log("结构化结果:", JSON.stringify(response.structured_output, null, 2));
  console.log("费用: $" + response.total_cost_usd);
  console.log();
}

// ---------------------------------------------------------------------------
// 场景 C: 流式输出 — spawn + stream-json
// ---------------------------------------------------------------------------
async function streamCall(prompt: string): Promise<void> {
  console.log("=== 场景 C: 流式输出 (spawn + stream-json) ===\n");

  return new Promise((resolve, reject) => {
    const traceEnv = createTraceEnv();
    const child = spawn("claude", [
      "-p", prompt,
      "--output-format", "stream-json",
      "--verbose",
      "--session-id", randomUUID(),
    ], { env: traceEnv });

    let buffer = "";

    child.stdout.on("data", (chunk: Buffer) => {
      buffer += chunk.toString();
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";

      for (const line of lines) {
        if (!line.trim()) continue;
        try {
          const event = JSON.parse(line);
          if (event.type === "assistant" && event.message?.content) {
            for (const block of event.message.content) {
              if ("text" in block) {
                process.stdout.write(block.text);
              }
            }
          } else if (event.type === "result") {
            console.log("\n\n--- 完成 ---");
            console.log("费用: $" + event.total_cost_usd);
          }
        } catch {
          // skip malformed lines
        }
      }
    });

    child.stderr.on("data", (chunk: Buffer) => {
      process.stderr.write(chunk);
    });

    child.on("close", (code) => {
      if (code === 0) resolve();
      else reject(new Error(`claude exited with code ${code}`));
    });

    child.on("error", reject);
  });
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------
async function main() {
  console.log(`Prompt: "${prompt}"\n`);
  console.log("每次调用生成独立的 SOFA_TRACEID / session-id\n");

//   await basicCall(prompt);
//   await structuredCall(`请分析以下主题并给出摘要和要点: ${prompt}`);
  await streamCall(prompt);
}

main().catch((err) => {
  console.error("Error:", err.message);
  process.exit(1);
});
