import { spawnSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const result = spawnSync(process.execPath,
  ["--test", join(here, "test_onboarding.mjs"), join(here, "test_lifecycle.mjs"), join(here, "test_verifier.mjs")],
  { stdio: "inherit", env: { ...process.env, LANGFUSE_JS_ENV: process.cwd() } });
if (result.error) throw result.error;
process.exitCode = result.status ?? 1;
