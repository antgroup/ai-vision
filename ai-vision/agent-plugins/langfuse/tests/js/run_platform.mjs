import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdir, readFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import { loadTarget, verifyPlatform } from "./verify_platform.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const { values } = parseArgs({ options: { targets: { type: "string" }, environment: { type: "string" }, out: { type: "string" },
  help: { type: "boolean", short: "h" } } });
if (values.help) {
  console.log(`Usage: node run_platform.mjs --targets FILE --environment DIRECTORY --out DIRECTORY

Set AIVISION_E2E_TARGETS_JSON instead of --targets to read an in-memory target.
Runs the SDK regressions with a local simulated provider, forwards OTLP to the
dedicated platform target, then checks native and Langfuse v1 readback.
Use tests/js or tests/js6 as the environment and a fresh output directory.
--help, -h  Show this help without reading credentials or contacting any service.`);
  process.exit(0);
}
assert.ok(values.environment && values.out, "Usage: --environment ../js6 --out DIRECTORY");
const target = await loadTarget(values.targets);
delete process.env.AIVISION_E2E_TARGETS_JSON;
const environment = resolve(values.environment), out = resolve(values.out);
await mkdir(out, { recursive: true, mode: 0o700 });
const ledger = join(out, "ledger.json");
const code = await new Promise((done) => {
  const child = spawn(process.execPath, [join(here, "run_tests.mjs")], { cwd: environment, stdio: "inherit", env: {
    ...process.env, AIVISION_OTLP_BASE_URL: target.endpoint ?? target.base_url, AIVISION_AGENT_UUID: target.agent_uuid,
    AIVISION_SPACE_KEY: target.space_key, AIVISION_JS_LEDGER: ledger,
  } });
  child.on("error", () => done(1)); child.on("close", done);
});
assert.equal(code, 0, "Collector or forwarder regression failed; no platform success claim");
const report = await verifyPlatform(target, await Promise.all([ledger, `${ledger}.lifecycle.json`].map(async (path) => JSON.parse(await readFile(path, "utf8")))), join(out, "platform-readback.json"));
console.log(JSON.stringify({ passed: report.passed, traces: report.traces, checks: report.checks, failures: report.failures }));
process.exitCode = report.passed ? 0 : 1;
