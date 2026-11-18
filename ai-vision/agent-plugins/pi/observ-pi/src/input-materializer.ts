import { createHash } from 'node:crypto';
import type {
  InputMaterializationRelation,
  InputMaterializationScope,
  PiHook,
} from './types.js';

export interface InputMaterializationDecision {
  relation: InputMaterializationRelation;
  scope: InputMaterializationScope;
  previous_count: number;
  current_count: number;
  emitted_count: number;
  selected_messages?: unknown[];
}

interface ModelSelection {
  provider?: string;
  model?: string;
  source?: string;
}

interface InputMaterializerCheckpoint {
  baseline?: string[];
  scope?: InputMaterializationScope;
  latestModel: ModelSelection;
  sessionId?: string;
  everHadValidRequest: boolean;
  pendingTurnBoundary: boolean;
  pendingScopeReset: boolean;
  pendingPreviousCount: number;
}

type CanonicalContext = 'message' | 'content_block' | 'nested';

function record(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : undefined;
}

function nonEmptyString(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined;
  const normalized = value.trim();
  return normalized || undefined;
}

function modelName(value: unknown): string | undefined {
  const direct = nonEmptyString(value);
  if (direct) return direct;
  const model = record(value);
  return nonEmptyString(model?.id) ?? nonEmptyString(model?.name);
}

function canonicalValue(
  value: unknown,
  context: CanonicalContext,
  ancestors: WeakSet<object>,
): unknown {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return value;
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  if (typeof value === 'bigint') return value.toString();
  if (typeof value === 'undefined' || typeof value === 'function' || typeof value === 'symbol') return undefined;

  const object = value as object;
  if (ancestors.has(object)) throw new Error('circular provider message');
  ancestors.add(object);
  try {
    if (Array.isArray(value)) {
      return value.map(item => canonicalValue(item, 'nested', ancestors) ?? null);
    }

    const source = value as Record<string, unknown>;
    const result: Record<string, unknown> = {};
    for (const key of Object.keys(source).sort()) {
      if (
        (context === 'message' || context === 'content_block')
        && (key === 'cache_control' || key === 'cacheControl')
      ) continue;
      const child = source[key];
      if (context === 'message' && key === 'content' && Array.isArray(child)) {
        result[key] = child.map(item => canonicalValue(item, 'content_block', ancestors) ?? null);
        continue;
      }
      const normalized = canonicalValue(child, 'nested', ancestors);
      if (normalized !== undefined) result[key] = normalized;
    }
    return result;
  } finally {
    ancestors.delete(object);
  }
}

function messageFingerprints(messages: unknown): string[] | undefined {
  if (!Array.isArray(messages)) return undefined;
  try {
    return messages.map(message => {
      const canonical = canonicalValue(message, 'message', new WeakSet<object>());
      return createHash('sha256').update(JSON.stringify(canonical ?? null)).digest('hex');
    });
  } catch {
    return undefined;
  }
}

function sameScope(left: InputMaterializationScope, right: InputMaterializationScope): boolean {
  return left.provider === right.provider && left.model === right.model && left.source === right.source;
}

function sameFingerprints(left: readonly string[], right: readonly string[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index]);
}

function isStrictPrefix(prefix: readonly string[], value: readonly string[]): boolean {
  return prefix.length < value.length && prefix.every((item, index) => item === value[index]);
}

function userTurnSuffix(messages: unknown[], previousCount: number): unknown[] | undefined {
  const appended = messages.slice(previousCount);
  for (let index = appended.length - 1; index >= 0; index--) {
    const role = nonEmptyString(record(appended[index])?.role)?.toLowerCase();
    if (role === 'user') return appended.slice(index);
  }
  return undefined;
}

export class InputMaterializer {
  private baseline?: string[];
  private scope?: InputMaterializationScope;
  private latestModel: ModelSelection = {};
  private sessionId?: string;
  private everHadValidRequest = false;
  private pendingTurnBoundary = false;
  private pendingScopeReset = false;
  private pendingPreviousCount = 0;

  transaction<T>(operation: () => T): T {
    const checkpoint = this.checkpoint();
    try {
      return operation();
    } catch (error) {
      this.restore(checkpoint);
      throw error;
    }
  }

  private checkpoint(): InputMaterializerCheckpoint {
    return {
      baseline: this.baseline ? [...this.baseline] : undefined,
      scope: this.scope ? { ...this.scope } : undefined,
      latestModel: { ...this.latestModel },
      sessionId: this.sessionId,
      everHadValidRequest: this.everHadValidRequest,
      pendingTurnBoundary: this.pendingTurnBoundary,
      pendingScopeReset: this.pendingScopeReset,
      pendingPreviousCount: this.pendingPreviousCount,
    };
  }

  private restore(checkpoint: InputMaterializerCheckpoint): void {
    this.baseline = checkpoint.baseline;
    this.scope = checkpoint.scope;
    this.latestModel = checkpoint.latestModel;
    this.sessionId = checkpoint.sessionId;
    this.everHadValidRequest = checkpoint.everHadValidRequest;
    this.pendingTurnBoundary = checkpoint.pendingTurnBoundary;
    this.pendingScopeReset = checkpoint.pendingScopeReset;
    this.pendingPreviousCount = checkpoint.pendingPreviousCount;
  }

  observe(hook: PiHook, raw: unknown, sessionId: string): InputMaterializationDecision | undefined {
    if (hook === 'session_start') {
      this.startSession(sessionId);
      return undefined;
    }

    this.ensureSession(sessionId);
    if (hook === 'model_select') {
      this.updateModelSelection(raw);
      return undefined;
    }
    if (hook === 'before_agent_start') {
      this.pendingTurnBoundary = this.everHadValidRequest;
      return undefined;
    }
    if (hook === 'session_compact') {
      this.invalidateBaseline();
      return undefined;
    }
    if (hook === 'session_shutdown') {
      this.invalidateBaseline();
      this.latestModel = {};
      this.sessionId = undefined;
      this.pendingTurnBoundary = false;
      return undefined;
    }
    if (hook !== 'before_provider_request') return undefined;
    return this.materialize(raw);
  }

  private startSession(sessionId: string): void {
    this.baseline = undefined;
    this.scope = undefined;
    this.latestModel = {};
    this.sessionId = sessionId;
    this.everHadValidRequest = false;
    this.pendingTurnBoundary = false;
    this.pendingScopeReset = false;
    this.pendingPreviousCount = 0;
  }

  private ensureSession(sessionId: string): void {
    if (this.sessionId === undefined) {
      this.sessionId = sessionId;
      return;
    }
    if (this.sessionId === sessionId) return;
    this.invalidateBaseline();
    this.latestModel = {};
    this.sessionId = sessionId;
  }

  private invalidateBaseline(): void {
    const previousCount = this.baseline?.length ?? this.pendingPreviousCount;
    if (this.everHadValidRequest) {
      this.pendingScopeReset = true;
      this.pendingPreviousCount = previousCount;
    }
    this.baseline = undefined;
    this.scope = undefined;
  }

  private updateModelSelection(raw: unknown): void {
    const payload = record(raw);
    const model = record(payload?.model);
    this.latestModel = {
      provider: nonEmptyString(model?.provider) ?? this.latestModel.provider,
      model: modelName(payload?.model) ?? this.latestModel.model,
      source: nonEmptyString(payload?.source) ?? this.latestModel.source,
    };
  }

  private resolveScope(request: Record<string, unknown>): InputMaterializationScope {
    return {
      provider: nonEmptyString(request.provider) ?? this.latestModel.provider ?? 'unknown',
      model: modelName(request.model) ?? this.latestModel.model ?? 'unknown',
      source: this.latestModel.source ?? 'unknown',
    };
  }

  private materialize(raw: unknown): InputMaterializationDecision {
    const payload = record(raw);
    const request = record(payload?.payload) ?? {};
    const scope = this.resolveScope(request);
    const messages = request.messages;
    const current = messageFingerprints(messages);
    const previousCount = this.baseline?.length ?? this.pendingPreviousCount;

    if (!current) {
      if (this.everHadValidRequest) {
        this.pendingScopeReset = true;
        this.pendingPreviousCount = previousCount;
      }
      this.baseline = undefined;
      this.scope = undefined;
      return {
        relation: 'unavailable_reset',
        scope,
        previous_count: previousCount,
        current_count: 0,
        emitted_count: 0,
      };
    }

    let relation: InputMaterializationRelation;
    let selected: unknown[];
    if (this.pendingScopeReset || (this.scope !== undefined && !sameScope(this.scope, scope))) {
      relation = 'scope_reset';
      selected = messages as unknown[];
    } else if (!this.baseline) {
      relation = 'initial_reset';
      selected = messages as unknown[];
    } else if (this.pendingTurnBoundary && isStrictPrefix(this.baseline, current)) {
      const turnMessages = userTurnSuffix(messages as unknown[], this.baseline.length);
      if (turnMessages) {
        relation = 'turn_boundary';
        selected = turnMessages;
      } else {
        relation = 'append';
        selected = (messages as unknown[]).slice(this.baseline.length);
      }
    } else if (sameFingerprints(this.baseline, current)) {
      relation = 'retry_equal';
      selected = messages as unknown[];
    } else if (isStrictPrefix(this.baseline, current)) {
      relation = 'append';
      selected = (messages as unknown[]).slice(this.baseline.length);
    } else if (current.length < this.baseline.length) {
      relation = 'shrink_reset';
      selected = messages as unknown[];
    } else {
      relation = 'rewrite_reset';
      selected = messages as unknown[];
    }

    this.baseline = current;
    this.scope = scope;
    this.everHadValidRequest = true;
    this.pendingTurnBoundary = false;
    this.pendingScopeReset = false;
    this.pendingPreviousCount = 0;
    return {
      relation,
      scope,
      previous_count: previousCount,
      current_count: current.length,
      emitted_count: selected.length,
      selected_messages: selected,
    };
  }
}
