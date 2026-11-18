import { fileURLToPath } from "node:url"
import { defineConfig, mergeConfig } from "vite"
import base from "../../../vite.config"

// This override is used only by this manual acceptance entry. Production builds
// keep the original detail module and contain no fault-injection switches.
export default defineConfig((env) =>
  mergeConfig(base(env), {
    plugins: [
      {
        name: "trace-detail-test-fault",
        enforce: "pre",
        resolveId(source, importer) {
          if (
            source === "./SpanDetailPanel" &&
            importer?.endsWith("/traces-otel/NativeTraceDetail.tsx")
          ) {
            return fileURLToPath(new URL("./FaultyDetail.tsx", import.meta.url))
          }
        },
      },
    ],
    server: { host: "127.0.0.1", port: 64710, strictPort: true },
  })
)
