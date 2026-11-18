import assert from "node:assert/strict"
import { createServer as createHttpServer } from "node:http"
import { once } from "node:events"
import path from "node:path"
import { fileURLToPath } from "node:url"
import { test } from "node:test"
import { createServer, loadConfigFromFile } from "vite"

const root = fileURLToPath(new URL("..", import.meta.url))

test("source browser proxy preserves Host and Origin only when explicitly enabled", async () => {
  const original = {
    target: process.env.VITE_DEV_API_TARGET,
    preserve: process.env.VITE_DEV_API_PRESERVE_HOST,
  }
  const upstream = createHttpServer((request, response) => {
    response.setHeader("content-type", "application/json")
    response.end(
      JSON.stringify({
        host: request.headers.host,
        origin: request.headers.origin,
      })
    )
  })
  upstream.listen(0, "127.0.0.1")
  await once(upstream, "listening")
  const targetHost = `127.0.0.1:${upstream.address().port}`
  try {
    for (const preserve of [undefined, "1"]) {
      process.env.VITE_DEV_API_TARGET = `http://${targetHost}`
      if (preserve) process.env.VITE_DEV_API_PRESERVE_HOST = preserve
      else delete process.env.VITE_DEV_API_PRESERVE_HOST
      const loaded = await loadConfigFromFile(
        { command: "serve", mode: "test" },
        path.join(root, "vite.config.ts")
      )
      assert.ok(loaded)
      // Exercise the actual config's three proxy rules over loopback. Frontend
      // transforms are unrelated to the HTTP Host/Origin contract under test.
      const server = await createServer({
        configFile: false,
        root,
        optimizeDeps: { noDiscovery: true, include: [] },
        server: {
          middlewareMode: true,
          hmr: false,
          proxy: loaded.config.server.proxy,
        },
      })
      const entry = createHttpServer(server.middlewares)
      try {
        entry.listen(0, "127.0.0.1")
        await once(entry, "listening")
        const browserHost = `127.0.0.1:${entry.address().port}`
        const origin = `http://${browserHost}`
        for (const endpoint of [
          "/api/proxy-contract",
          "/openapi/proxy-contract",
          "/project/proxy-contract",
        ]) {
          const response = await fetch(origin + endpoint, {
            method: "POST",
            headers: { Origin: origin, "Content-Type": "application/json" },
            body: "{}",
          })
          assert.equal(response.status, 200)
          assert.deepEqual(
            await response.json(),
            {
              host: preserve ? browserHost : targetHost,
              origin,
            },
            `${endpoint}: preserving Host must not rewrite the browser Origin`
          )
        }
      } finally {
        await new Promise((resolve) => entry.close(resolve))
        await server.close()
      }
    }
  } finally {
    await new Promise((resolve) => upstream.close(resolve))
    for (const [key, value] of [
      ["VITE_DEV_API_TARGET", original.target],
      ["VITE_DEV_API_PRESERVE_HOST", original.preserve],
    ]) {
      if (value === undefined) delete process.env[key]
      else process.env[key] = value
    }
  }
})
