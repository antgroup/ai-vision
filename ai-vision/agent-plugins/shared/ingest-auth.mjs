/** Production-only ingest authentication; refresh before exporting. */
import { readFileSync } from "node:fs";
const tokens = /* @__PURE__ */ new Map();
const pending = /* @__PURE__ */ new Map();
async function getToken(filename, endpoint) {
  const source = readFileSync(filename, "utf8");
  const config = JSON.parse(source);
  const baseEndpoint = config.endpoint.replace(/\/$/, "").replace(/\/v1\/(traces|logs|metrics)$/, "");
  if (!["traces", "logs", "metrics"].some((signal) => endpoint === `${baseEndpoint}/v1/${signal}`)) {
    throw new Error("AI Vision endpoint changed; reinstall the plugin configuration");
  }
  const cached = tokens.get(filename);
  if (cached?.config === source && cached.expiresAt > Date.now() + 6e4) return cached.token;
  const requestKey = `${filename}\0${source}`;
  const inflight = pending.get(requestKey);
  if (inflight) return inflight;
  const request = (async () => {
    try {
      const response = await fetch(`${config.base_url.replace(/\/$/, "")}/openapi/v1/ingest-tokens`, {
        method: "POST",
        redirect: "error",
        signal: AbortSignal.timeout(2e3),
        headers: {
          Authorization: `Bearer ${config.agent_credential || config.space_key}`,
          "Content-Type": "application/json"
        },
        body: JSON.stringify({ agent_uuid: config.agent_uuid })
      });
      if (!response.ok) throw new Error("AI Vision ingest authentication failed");
      const payload = await response.json();
      const data = payload.data;
      if (payload.code !== 0 || !data?.token || data.space_id !== config.space_id || data.agent_uuid !== config.agent_uuid || data.endpoint.replace(/\/$/, "") !== config.endpoint.replace(/\/$/, "")) {
        throw new Error("AI Vision ingest configuration changed; reinstall to reconnect");
      }
      tokens.set(filename, { token: data.token, expiresAt: Date.now() + data.expires_in * 1e3, config: source });
      return data.token;
    } catch {
      if (cached?.config === source && cached.expiresAt > Date.now()) return cached.token;
      throw new Error("AI Vision ingest authentication unavailable");
    } finally {
      pending.delete(requestKey);
    }
  })();
  pending.set(requestKey, request);
  return request;
}
function ingestHeaders(filename, endpoint, fallback = {}) {
  if (!filename) return fallback;
  return async () => ({ Authorization: `Bearer ${await getToken(filename, endpoint)}` });
}
export {
  ingestHeaders
};
