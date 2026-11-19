import { createConnection } from "node:net"
import { test as base, type BrowserContextOptions } from "@playwright/test"

const maxAuthBytes = 65536

async function readBrowserAuth(
  socketPath: string
): Promise<BrowserContextOptions["storageState"]> {
  const connection = createConnection(socketPath)
  connection.setTimeout(5_000, () => {
    connection.destroy(new Error("Browser authentication state timed out"))
  })
  const chunks: Buffer[] = []
  let bytes = 0
  try {
    for await (const chunk of connection) {
      const buffer: Buffer = chunk
      bytes += buffer.length
      if (bytes > maxAuthBytes)
        throw new Error("Browser auth state is too large")
      chunks.push(buffer)
    }
    const state = JSON.parse(Buffer.concat(chunks).toString("utf8"))
    if (
      !Array.isArray(state?.cookies) ||
      !state.cookies.length ||
      !Array.isArray(state.origins)
    )
      throw new Error("Invalid browser authentication state")
    return state
  } catch {
    // A malformed payload must not be echoed into Playwright reports.
    throw new Error("Private browser authentication state unavailable")
  } finally {
    connection.destroy()
  }
}

export const test = base.extend({
  // Read in each worker, outside Playwright config and reporter metadata.
  storageState: async ({ storageState }, provide) => {
    const socketPath = process.env.AIVISION_BROWSER_AUTH_SOCKET
    await provide(socketPath ? await readBrowserAuth(socketPath) : storageState)
  },
})
