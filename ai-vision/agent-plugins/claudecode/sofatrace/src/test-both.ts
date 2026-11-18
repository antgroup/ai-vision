import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { query } from "@anthropic-ai/claude-agent-sdk";

const execFileAsync = promisify(execFile);

async function testSpawn() {
  console.log("=== 1. Spawn 方式 (SCENE=spawn) ===\n");

  const { stdout } = await execFileAsync("claude", [
    "-p", "回答: hello spawn",
    "--output-format", "json",
    "--no-session-persistence",
  ], {
    maxBuffer: 10 * 1024 * 1024,
    env: {
      ...process.env,
      SOFA_TRACEID: "0a1b2c3d161716800000010012345",
      SOFA_RPCID: "0.1.2.3",
      TRACEPARENT: "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01",
      SCENE: "spawn",
    },
  });

  const response = JSON.parse(stdout);
  console.log("回答:", response.result);
  console.log("费用: $" + response.total_cost_usd);
  console.log();
}

async function testSdk() {
  console.log("=== 2. SDK 方式 (SCENE=sdk) ===\n");

  for await (const message of query({
    prompt: "回答: hello sdk",
    options: {
      permissionMode: "dontAsk",
      maxTurns: 3,
      env: {
        ...process.env,
        SOFA_TRACEID: "0a1b2c3d161716800000010012345",
        SOFA_RPCID: "0.1.2.3",
        TRACEPARENT: "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01",
        SCENE: "sdk",
      },
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
      }
    }
  }
  console.log();
}

async function main() {
  await testSpawn();
  await testSdk();
  console.log("=== 检查 cchooks-sdk.log ===\n");
}

main().catch(console.error);
