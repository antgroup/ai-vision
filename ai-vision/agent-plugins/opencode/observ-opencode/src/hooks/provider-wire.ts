/** OpenCode public provider fetch seam. Never retain headers or response text. */
import { randomUUID } from 'node:crypto';

const HEADER = 'x-observ-opencode-correlation';
const WRAPPED = Symbol('observ-opencode-fetch');
const LIMIT = 64 * 1024;
const MAX_PENDING = 1024;
const TTL_MS = 10 * 60 * 1000;
export interface WireUsage { input?: number; output?: number; total?: number; cacheRead?: number; cacheWrite?: number; reasoning?: number }
export interface WireIdentity {
  sessionID: string; agent: string; message: { id: string };
  model: { id: string; providerID: string; api?: { npm?: string; id?: string } };
  assistantMessageId?: string;
}
export interface WireResult { identity: WireIdentity; usage: WireUsage; durationMs: number; startedAt: number; statusCode?: number; errorType?: string; finishReason?: string }
interface ObserverOptions { correlate(input: WireIdentity): WireIdentity; start?(attempt: WireResult): void; publish(result: WireResult): void }
const finite = (v: unknown): v is number => typeof v === 'number' && Number.isSafeInteger(v) && v >= 0;
function extract(value: unknown): WireUsage {
  if (!value || typeof value !== 'object') return {};
  const raw = value as Record<string, any>;
  const usage: WireUsage = {};
  for (const [from, to] of [['prompt_tokens','input'],['completion_tokens','output'],['total_tokens','total']] as const)
    if (finite(raw[from])) usage[to] = raw[from];
  if (finite(raw.prompt_tokens_details?.cached_tokens)) usage.cacheRead = raw.prompt_tokens_details.cached_tokens;
  if (finite(raw.prompt_tokens_details?.cache_write_tokens)) usage.cacheWrite = raw.prompt_tokens_details.cache_write_tokens;
  if (finite(raw.completion_tokens_details?.reasoning_tokens)) usage.reasoning = raw.completion_tokens_details.reasoning_tokens;
  return usage;
}

/** One bounded line, discarded until newline when large. Content is not stored. */
class Parser {
  usage: WireUsage = {};
  finishReason?: string;
  private decoder = new TextDecoder();
  private buffer = '';
  private dropping = false;
  private oversizedJson = false;
  constructor(private sse: boolean) {}
  private accept(line: string): void {
    const raw = this.sse ? (line.startsWith('data:') ? line.slice(5).trim() : '') : line;
    if (!raw || raw === '[DONE]' || (!raw.includes('"usage"') && !raw.includes('"finish_reason"'))) return;
    try {
      const value = JSON.parse(raw);
      if (value.usage && typeof value.usage === 'object') this.usage = extract(value.usage);
      const reason = value.choices?.[0]?.finish_reason;
      if (typeof reason === 'string' && reason.length <= 128) this.finishReason = reason;
    } catch { /* Invalid or oversized provider data stays unavailable. */ }
  }
  push(bytes: Uint8Array): void {
    const text = this.decoder.decode(bytes, { stream: true });
    if (!this.sse) {
      if (!this.oversizedJson && this.buffer.length + text.length <= LIMIT) this.buffer += text;
      else { this.buffer = ''; this.oversizedJson = true; }
      return;
    }
    let offset = 0;
    while (offset < text.length) {
      const newline = text.indexOf('\n', offset);
      const end = newline < 0 ? text.length : newline;
      if (!this.dropping) {
        const piece = text.slice(offset, end);
        if (this.buffer.length + piece.length > LIMIT) { this.buffer = ''; this.dropping = true; }
        else this.buffer += piece;
      }
      if (newline < 0) break;
      if (!this.dropping) this.accept(this.buffer.replace(/\r$/, ''));
      this.buffer = ''; this.dropping = false; offset = newline + 1;
    }
  }
  end(): void {
    if (!this.dropping && !this.oversizedJson) this.accept(this.buffer);
    this.buffer = '';
  }
}

export function createProviderWireObserver(options: ObserverOptions) {
  const pending = new Map<string, { identity: WireIdentity; created: number }>();
  const providers = new Set<string>();
  function prune() {
    const now = Date.now();
    for (const [id, value] of pending) if (now - value.created > TTL_MS) pending.delete(id);
    while (pending.size >= MAX_PENDING) pending.delete(pending.keys().next().value!);
  }
  const config = (host: { provider?: Record<string, { npm?: string; options?: Record<string, any> }> }): void => {
    for (const [id, provider] of Object.entries(host.provider ?? {})) {
      // Anthropic/Responses and native LLM protocols have different contracts.
      if (provider.npm !== '@ai-sdk/openai-compatible') continue;
      const opts = provider.options ??= {};
      providers.add(id);
      if (opts.fetch?.[WRAPPED]) continue;
      const original: typeof fetch = typeof opts.fetch === 'function' ? opts.fetch : globalThis.fetch;
      const wrapped: typeof fetch = async (input, init) => {
        const headers = new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined));
        const id = headers.get(HEADER); headers.delete(HEADER);
        const correlation = id ? pending.get(id) : undefined;
        const next = { ...init, headers };
        const url = input instanceof Request ? input.url : String(input);
        if (!correlation || !new URL(url).pathname.endsWith('/chat/completions')) return original(input, next);
        const started = Date.now();
        const result: WireResult = { identity: correlation.identity, usage: {}, durationMs: 0, startedAt: started };
        try { options.start?.(result); } catch { /* Telemetry fails open. */ }
        let published = false;
        let parser: Parser | undefined;
        const signal = init?.signal ?? (input instanceof Request ? input.signal : undefined);
        const aborted = () => publish('AbortError');
        const publish = (errorType?: string) => {
          if (published) return;
          published = true; signal?.removeEventListener('abort', aborted);
          if (parser) { result.usage = parser.usage; result.finishReason = parser.finishReason; }
          result.durationMs = Math.max(0, Date.now() - started);
          if (errorType) result.errorType = errorType;
          try { options.publish(result); } catch { /* Telemetry fails open. */ }
        };
        signal?.addEventListener('abort', aborted, { once: true });
        if (signal?.aborted) aborted();
        try {
          const response = await original(input, next);
          result.statusCode = response.status;
          if (!response.ok) result.errorType = String(response.status);
          if (!response.body) { publish(); return response; }
          const contentType = response.headers.get('content-type') ?? '';
          parser = new Parser(contentType.includes('text/event-stream'));
          const responseParser = parser;
          const reader = response.body.getReader();
          const body = new ReadableStream<Uint8Array>({
            async pull(controller) {
              try {
                const part = await reader.read();
                if (part.done) {
                  responseParser.end();
                  publish(); controller.close(); return;
                }
                responseParser.push(part.value);
                // Forward exact bytes with the consumer's backpressure, no tee.
                controller.enqueue(part.value);
              } catch (error) {
                publish(error instanceof Error ? error.name : 'Error'); controller.error(error);
              }
            },
            async cancel(reason) { publish('AbortError'); await reader.cancel(reason); },
          });
          const observed = new Response(body, { status: response.status, statusText: response.statusText, headers: response.headers });
          for (const key of ['url', 'redirected', 'type'] as const) Object.defineProperty(observed, key, { value: response[key] });
          return observed;
        } catch (error) {
          publish(error instanceof Error ? error.name : 'Error'); throw error;
        }
      };
      Object.defineProperty(wrapped, WRAPPED, { value: true }); opts.fetch = wrapped;
    }
  };
  const headers = (input: WireIdentity, output: { headers: Record<string, string> }): void => {
    if (!providers.has(input.model.providerID) || input.model.api?.npm !== '@ai-sdk/openai-compatible') return;
    if (!input.sessionID || !input.message?.id) return;
    prune();
    // Only these documented identity fields survive; no provider auth/options.
    const identity = options.correlate({ sessionID: input.sessionID, agent: input.agent,
      message: { id: input.message.id }, model: { id: input.model.id, providerID: input.model.providerID,
        api: { npm: input.model.api?.npm, ...(input.model.api?.id ? { id: input.model.api.id } : {}) } } });
    const id = randomUUID(); pending.set(id, { identity, created: Date.now() }); output.headers[HEADER] = id;
  };
  return { config, headers };
}
