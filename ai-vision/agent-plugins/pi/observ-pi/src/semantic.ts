import { messageContent } from '../../../shared/media-content.mjs';
import type { CanonicalEvent, SemanticObservation } from './types.js';
import { resolveSignalAttributes, type PiagentConfig } from './config.js';
import { literalSkillName } from './skill-detection.js';
import { addToolTraceTag } from './trace-tags.js';
import { InternalAttributes, INTERNAL_META_ROOT_VALUE } from './internal-attributes.js';
import { usageAttributes, type ReportedUsage } from './provider-usage.js';

interface TurnState {
  key: string;
  number: number;
  systemPrompt: string;
  prompt: string;
  traceTags: string[];
  nativeSkillChannelObserved: boolean;
  nativeSkillOccurrences: Set<string>;
  pendingDerivedSkills: PendingDerivedSkill[];
  generation?: GenerationState;
  latestOutput?: string;
  commandSkill?: CommandSkillState;
}
interface GenerationState { key: string; startedAt: string; model: string; provider: string; responseStatus?: number; reportedUsage?: ReportedUsage; }
interface CommandSkillState { key: string; startedAt: string; name: string; callId: string; common: Attributes; chatId: string; occurrenceId?: string; }
interface PendingDerivedSkill { tool: ToolState; name: string; at: string; common: Attributes; occurrenceId?: string; }
interface ToolIdentity { rawName: string; logicalName: string; callId: string; type: 'builtin' | 'mcp' | 'skill'; }
interface ToolState { key: string; startedAt: string; identity: ToolIdentity; args: unknown; skillCandidate?: string; skillOccurrenceId?: string; }
type Attributes = Record<string, unknown>;

function json(value: unknown): string {
  try { return JSON.stringify(value ?? null); } catch { return 'null'; }
}

function messageData(payload: Record<string, unknown>): Record<string, unknown> {
  return payload.message && typeof payload.message === 'object' ? payload.message as Record<string, unknown> : {};
}

function assistantMessages(message: Record<string, unknown>): string {
  const blocks = Array.isArray(message.content) ? message.content as Record<string, unknown>[] : [];
  const text = blocks.filter(block => block.type === 'text').map(block => String(block.text ?? '')).join('\n');
  const reasoning = blocks.filter(block => block.type === 'thinking').map(block => String(block.thinking ?? '')).join('\n');
  const toolCalls = blocks.filter(block => block.type === 'toolCall' || block.type === 'tool_use');
  return json([{ role: 'assistant', content: messageContent(message.content, text || (typeof message.content === 'string' ? message.content : '')), ...(reasoning ? { reasoning_content: reasoning } : {}), ...(toolCalls.length ? { tool_calls: toolCalls } : {}), finish_reason: message.stopReason ?? 'unknown' }]);
}

function providerName(value: unknown): string {
  const provider = String(value ?? '').toLowerCase();
  if (provider.includes('theta')) return 'theta';
  if (provider.includes('anthropic')) return 'anthropic';
  if (provider.includes('openai')) return 'openai';
  if (provider.includes('qwen')) return 'qwen';
  if (provider.includes('alibaba')) return 'alibaba';
  if (provider.includes('bedrock')) return 'aws.bedrock';
  if (provider.includes('deepseek')) return 'deepseek';
  return provider || 'unknown';
}

function toolType(name: string): 'builtin' | 'mcp' | 'skill' {
  if (/^mcp$/i.test(name) || /^mcp[-_:]/i.test(name)) return 'mcp';
  if (/^skill$/i.test(name) || /^skills?[-_:]/i.test(name)) return 'skill';
  return 'builtin';
}

function canonicalToolCallId(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined;
  const id = value.trim();
  return id.length > 0 ? id : undefined;
}

function canonicalToolName(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined;
  const name = value.trim();
  return name.length > 0 ? name : undefined;
}

function logicalToolName(
  rawName: string,
  type: ToolIdentity['type'],
  args: unknown,
  evidenceName?: string,
): string {
  if (type === 'mcp' && /^mcp__/i.test(rawName)) {
    const segments = rawName.slice('mcp__'.length).split('__').filter(Boolean);
    return segments.length ? segments.join('.') : rawName;
  }
  if (type === 'mcp') return rawName.replace(/^mcp[-_:]+/i, '') || rawName;
  if (type === 'skill' && /^skills?[-_:]+/i.test(rawName)) return rawName.replace(/^skills?[-_:]+/i, '') || rawName;
  if (type === 'skill') return evidenceName ?? literalSkillName(args) ?? rawName;
  return rawName;
}

function toolIdentity(callId: string, rawName: string, args: unknown, evidenceName?: string): ToolIdentity {
  const type = toolType(rawName);
  return { callId, rawName, type, logicalName: logicalToolName(rawName, type, args, evidenceName) };
}

function nativeSkillEvidenceName(payload: Record<string, unknown>): string | undefined {
  const evidence = payload.skillEvidence && typeof payload.skillEvidence === 'object'
    ? payload.skillEvidence as Record<string, unknown>
    : undefined;
  return evidence?.source === 'native' ? canonicalToolName(evidence.name) : undefined;
}

function skillOccurrenceId(
  payload: Record<string, unknown>,
  expected: { name: string; source: 'native' | 'command' | 'catalog_read'; sourceCallId?: string },
): string | undefined {
  const evidence = payload.skillEvidence && typeof payload.skillEvidence === 'object'
    ? payload.skillEvidence as Record<string, unknown>
    : undefined;
  if (!evidence || evidence.name !== expected.name || evidence.source !== expected.source) return undefined;
  if (typeof evidence.occurrenceId !== 'string' || evidence.occurrenceId.length === 0) return undefined;
  if (expected.source === 'catalog_read' && evidence.sourceCallId !== expected.sourceCallId) return undefined;
  return evidence.occurrenceId;
}

export class SemanticEngine {
  private turn?: TurnState;
  private turnNumber: number;
  private tools = new Map<string, ToolState>();
  private compaction?: { key: string; startedAt: string };
  private model = 'unknown';
  private provider = 'unknown';
  private emittedEventSequence = 0;
  private readonly staticSignalAttributes?: Record<string, string>;

  constructor(
    private readonly config: PiagentConfig,
    private readonly emit: (observation: SemanticObservation) => void,
    initialTurnNumber = 0,
    private readonly allocateEventSequence?: () => number | undefined,
  ) {
    if (!config.dynamicUsername && !config.dynamicUid) {
      this.staticSignalAttributes = { ...config.otelExporter.attributes };
    }
    this.turnNumber = Number.isSafeInteger(initialTurnNumber) && initialTurnNumber >= 0
      ? initialTurnNumber
      : 0;
  }

  restoreTurnNumber(highWater: number): void {
    if (Number.isSafeInteger(highWater) && highWater >= 0) {
      this.turnNumber = Math.max(this.turnNumber, highWater);
    }
  }

  private eventSequenceAttributes(): Attributes {
    if (!this.allocateEventSequence) {
      return { 'event.sequence': ++this.emittedEventSequence };
    }
    const sequence = this.allocateEventSequence();
    if (sequence !== undefined
      && Number.isSafeInteger(sequence)
      && sequence > this.emittedEventSequence) {
      this.emittedEventSequence = sequence;
      return { 'event.sequence': sequence };
    }
    return {};
  }

  private signalAttributes(): Record<string, string> {
    return this.staticSignalAttributes ?? resolveSignalAttributes(this.config);
  }

  private finishGeneration(
    generation: GenerationState,
    at: string,
    common: Attributes,
    signalAttributes: Record<string, string>,
    duration: number,
    eventAttributes: Attributes,
    spanAttributes: Attributes,
    error: { type: string; message: string } | undefined,
    errorCategory: string | undefined,
    order: 'normal' | 'shutdown',
  ): void {
    const metricAttrs = { ...signalAttributes, 'gen_ai.request.model': generation.model };
    const emitMetrics = () => {
      this.emit({ type: 'metric', name: 'agent.api_request.count', value: 1, metricKind: 'counter', attributes: metricAttrs });
      this.emit({ type: 'metric', name: 'agent.api_request.duration', value: duration, metricKind: 'histogram', attributes: metricAttrs });
      const measured = (field: string): number | undefined => {
        const value = spanAttributes[field];
        return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : undefined;
      };
      const input = spanAttributes['agentic.usage.input_tokens.missing'] === 'true' ? undefined : measured('gen_ai.usage.input_tokens');
      const output = spanAttributes['agentic.usage.output_tokens.missing'] === 'true' ? undefined : measured('gen_ai.usage.output_tokens');
      const cached = measured('gen_ai.usage.cache_read.input_tokens');
      const created = measured('gen_ai.usage.cache_creation.input_tokens');
      const fresh = input === undefined ? undefined : input - (cached ?? 0) - (created ?? 0);
      if (fresh !== undefined && fresh >= 0) {
        this.emit({ type: 'metric', name: 'agent.token.usage', value: fresh, metricKind: 'counter', attributes: { ...signalAttributes, type: 'input' } });
      }
      // Provider completion tokens already include reasoning. Counting a
      // separate reasoning bucket here would duplicate the physical output.
      if (output !== undefined) {
        this.emit({ type: 'metric', name: 'agent.token.usage', value: output, metricKind: 'counter', attributes: { ...signalAttributes, type: 'output' } });
      }
      if (cached !== undefined) this.emit({ type: 'metric', name: 'agent.cache.read_tokens', value: cached, metricKind: 'counter', attributes: metricAttrs });
      if (created !== undefined) this.emit({ type: 'metric', name: 'agent.cache.write_tokens', value: created, metricKind: 'counter', attributes: metricAttrs });
      if (errorCategory) {
        this.emit({ type: 'metric', name: 'agent.api_request.error.count', value: 1, metricKind: 'counter', attributes: { ...metricAttrs, 'agentic.error.category': errorCategory } });
      }
    };
    const emitTerminalObservations = () => {
      this.emit({ type: 'event', name: 'api_request', spanKey: generation.key, timestamp: at, attributes: { ...common, 'event.name': 'api_request', ...this.eventSequenceAttributes(), 'gen_ai.operation.name': 'chat', ...eventAttributes } });
      this.emit({ type: 'span.end', key: generation.key, timestamp: at, attributes: spanAttributes, error });
    };
    if (order === 'normal') emitMetrics();
    emitTerminalObservations();
    if (order === 'shutdown') emitMetrics();
    if (this.turn?.generation === generation) this.turn.generation = undefined;
  }

  private finishTool(
    tool: ToolState,
    at: string,
    common: Attributes,
    signalAttributes: Record<string, string>,
    resultValue: unknown,
    error: { type: string; message: string } | undefined,
    errorCategory: string | undefined,
    retainForLink = false,
  ): void {
    const identity = tool.identity;
    const duration = Math.max(0, Date.parse(at) - Date.parse(tool.startedAt));
    const result = json(resultValue);
    this.emit({ type: 'event', name: 'tool_result', spanKey: tool.key, timestamp: at, attributes: { ...common, 'event.name': 'tool_result', ...this.eventSequenceAttributes(), 'gen_ai.operation.name': 'execute_tool', 'agentic.turn.number': this.turn?.number ?? this.turnNumber, 'gen_ai.tool.name': identity.rawName, 'gen_ai.tool.call.id': identity.callId, 'gen_ai.tool.call.arguments': json(tool.args), 'gen_ai.tool.call.result': result, 'agentic.tool.type': identity.type, 'agentic.tool.name': identity.logicalName, 'agentic.tool.raw_name': identity.rawName, 'agentic.tool.id': identity.callId, duration_ms: duration, ...(error ? { 'error.type': error.type, 'error.message': error.message } : {}) } });
    this.emit({ type: 'span.end', key: tool.key, timestamp: at, attributes: { 'gen_ai.tool.call.result': result, 'agentic.tool.duration_ms': duration }, error, retainForLink });
    const metricAttrs = { ...signalAttributes, 'agentic.tool.type': identity.type };
    this.emit({ type: 'metric', name: 'agent.tool_call.count', value: 1, metricKind: 'counter', attributes: metricAttrs });
    this.emit({ type: 'metric', name: 'agent.tool_call.duration', value: duration, metricKind: 'histogram', attributes: metricAttrs });
    if (errorCategory) {
      this.emit({ type: 'metric', name: 'agent.tool_call.error.count', value: 1, metricKind: 'counter', attributes: { ...metricAttrs, 'agentic.error.category': errorCategory } });
    }
    this.tools.delete(identity.callId);
  }

  private emitDerivedSkill(tool: ToolState, name: string, at: string, common: Attributes): void {
    if (!this.turn) return;
    const callId = `skill-load-${tool.identity.callId}`;
    const key = `${this.turn.key}:skill-load:${tool.identity.callId}`;
    const privacy = this.config.collectionMode === 'privacy';
    addToolTraceTag(this.turn, 'skill', name);
    this.emit({
      type: 'span.start', key, parentKey: this.turn.key, linkKeys: [tool.key], name: `skill ${name}`,
      kind: 'TOOL', timestamp: at,
      attributes: this.syntheticSkillAttributes(
        common,
        `${common['gen_ai.conversation.id']}:${this.turn.number}`,
        name,
        'Skill',
        callId,
        tool.identity.rawName,
        tool.identity.callId,
        true,
      ),
    });
    this.emit({
      type: 'span.end', key, timestamp: at,
      attributes: {
        'gen_ai.tool.call.result': privacy ? '[REDACTED]' : json({ status: 'loaded' }),
        'agentic.tool.duration_ms': 0,
      },
    });
  }

  private shouldEmitSynthetic(occurrenceId: string | undefined, turn: TurnState): boolean {
    if (occurrenceId) return !turn.nativeSkillOccurrences.has(occurrenceId);
    return !turn.nativeSkillChannelObserved;
  }

  private syntheticSkillAttributes(
    common: Attributes,
    chatId: string,
    name: string,
    toolName: string,
    callId: string,
    rawName: string,
    toolId: string,
    derived = false,
  ): Attributes {
    return {
      ...common,
      'gen_ai.chat.id': chatId,
      'gen_ai.operation.name': 'execute_tool',
      'gen_ai.span.kind': 'TOOL',
      'gen_ai.tool.name': toolName,
      'gen_ai.tool.call.id': callId,
      'gen_ai.tool.call.arguments': this.config.collectionMode === 'privacy' ? '[REDACTED]' : json({ skill: name }),
      'agentic.tool.type': 'skill',
      'agentic.tool.name': name,
      'agentic.tool.raw_name': rawName,
      'agentic.tool.id': toolId,
      ...(derived ? { 'agentic.tool.derived': true } : {}),
      'agentic.tool.call_id.synthetic': true,
    };
  }

  private emitCommandSkill(skill: CommandSkillState, turn: TurnState, at: string, error?: { type: string; message: string }): void {
    addToolTraceTag(turn, 'skill', skill.name);
    this.emit({
      type: 'span.start', key: skill.key, parentKey: turn.key, name: `skill ${skill.name}`,
      kind: 'TOOL', timestamp: skill.startedAt,
      attributes: this.syntheticSkillAttributes(
        skill.common,
        skill.chatId,
        skill.name,
        `skill ${skill.name}`,
        skill.callId,
        'Skill',
        skill.callId,
      ),
    });
    this.emit({
      type: 'span.end', key: skill.key, timestamp: at,
      attributes: {
        'gen_ai.tool.call.result': turn.latestOutput ?? 'null',
        'agentic.tool.duration_ms': Math.max(0, Date.parse(at) - Date.parse(skill.startedAt)),
      },
      error,
    });
  }

  private finishCompaction(at: string, common: Attributes, error?: { type: string; message: string }): void {
    if (!this.compaction) return;
    const compaction = this.compaction;
    const duration = Math.max(0, Date.parse(at) - Date.parse(compaction.startedAt));
    this.emit({ type: 'event', name: 'compaction_result', spanKey: compaction.key, timestamp: at, attributes: { ...common, 'event.name': 'compaction_result', ...this.eventSequenceAttributes(), 'gen_ai.operation.name': 'execute_tool', 'gen_ai.tool.name': 'compaction', 'agentic.tool.type': 'system', 'agentic.runtime.name': 'pi', 'agentic.turn.number': this.turn?.number ?? this.turnNumber, duration_ms: duration, ...(error ? { 'error.type': error.type, 'error.message': error.message } : {}) } });
    this.emit({ type: 'span.end', key: compaction.key, timestamp: at, attributes: {}, error });
    this.compaction = undefined;
  }

  private finishTurn(at: string, error?: { type: string; message: string }): void {
    if (!this.turn) return;
    const turn = this.turn;
    if (turn.commandSkill && this.shouldEmitSynthetic(turn.commandSkill.occurrenceId, turn)) {
      this.emitCommandSkill(turn.commandSkill, turn, at, error);
    }
    for (const pending of turn.pendingDerivedSkills) {
      if (this.shouldEmitSynthetic(pending.occurrenceId, turn)) {
        this.emitDerivedSkill(pending.tool, pending.name, pending.at, pending.common);
      }
    }
    this.emit({
      type: 'span.end', key: turn.key, timestamp: at,
      attributes: {
        ...(!error && turn.latestOutput ? { 'gen_ai.output.messages': turn.latestOutput } : {}),
        ...(turn.traceTags.length ? { 'langfuse.trace.tags': [...turn.traceTags] } : {}),
      },
      error,
    });
    this.turn = undefined;
  }

  apply(event: CanonicalEvent): void {
    switch (event.hook) {
      case 'model_select': {
        const model = event.payload.model as Record<string, unknown> | undefined;
        this.model = String(model?.id ?? event.payload.model ?? this.model);
        this.provider = providerName(model?.provider ?? this.provider);
        return;
      }
      case 'after_provider_response':
        if (this.turn?.generation) this.turn.generation.responseStatus = Number(event.payload.status ?? 0);
        return;
      case 'provider_stream_event': {
        const generation = this.turn?.generation;
        if (!generation || event.payload.model !== generation.model) return;
        if (event.payload.model === generation.model && typeof event.payload.provider === 'string') {
          generation.provider = providerName(event.payload.provider);
        }
        if (event.payload.reportedUsage && typeof event.payload.reportedUsage === 'object') {
          generation.reportedUsage = { ...generation.reportedUsage, ...event.payload.reportedUsage as ReportedUsage };
        }
        return;
      }
      case 'turn_start':
      case 'message_update':
      case 'tool_execution_update':
        return;
      default:
        break;
    }

    const signalAttributes = this.signalAttributes();
    const common = {
      ...signalAttributes,
      'user.id': signalAttributes['ant.uid'] ?? this.config.userId,
      'gen_ai.conversation.id': event.session_id,
      'gen_ai.chat.id': `${event.session_id}:${this.turn?.number ?? Math.max(this.turnNumber, 1)}`,
      'agentic.runtime.name': 'pi'
    };
    const at = event.timestamp;
    switch (event.hook) {
      case 'session_start':
        this.emit({ type: 'event', name: 'session_start', timestamp: at, attributes: { ...common, 'event.name': 'session_start', ...this.eventSequenceAttributes(), 'gen_ai.operation.name': 'chat', 'gen_ai.session.id': event.session_id } });
        break;
      case 'before_agent_start': {
        this.turnNumber++;
        const key = `turn:${event.session_id}:${this.turnNumber}`;
        this.turn = {
          key,
          number: this.turnNumber,
          systemPrompt: String(event.payload.systemPrompt ?? ''),
          prompt: String(event.payload.prompt ?? ''),
          traceTags: [],
          nativeSkillChannelObserved: false,
          nativeSkillOccurrences: new Set(),
          pendingDerivedSkills: [],
        };
        const chatId = `${event.session_id}:${this.turnNumber}`;
        this.emit({ type: 'span.start', key, name: `Turn ${this.turnNumber}`, kind: 'CHAT', timestamp: at, attributes: { ...common, 'gen_ai.chat.id': chatId, 'gen_ai.operation.name': 'invoke_agent', 'gen_ai.span.kind': 'CHAT', 'agentic.turn.number': this.turnNumber, [InternalAttributes.META_ROOT]: INTERNAL_META_ROOT_VALUE, 'gen_ai.input.messages': json([{ role: 'user', content: messageContent([{ type: 'text', text: String(event.payload.prompt ?? '') }, ...(Array.isArray(event.payload.images) ? event.payload.images : [])], String(event.payload.prompt ?? '')) }]) } });
        this.emit({ type: 'event', name: 'user_prompt', spanKey: key, timestamp: at, attributes: { ...common, 'gen_ai.chat.id': chatId, 'event.name': 'user_prompt', ...this.eventSequenceAttributes(), 'gen_ai.operation.name': 'chat', 'agentic.turn.number': this.turnNumber, 'gen_ai.input.messages': json([{ role: 'user', content: messageContent([{ type: 'text', text: String(event.payload.prompt ?? '') }, ...(Array.isArray(event.payload.images) ? event.payload.images : [])], String(event.payload.prompt ?? '')) }]) } });
        const invocation = event.payload.skillInvocation && typeof event.payload.skillInvocation === 'object'
          ? event.payload.skillInvocation as Record<string, unknown>
          : undefined;
        const skillName = typeof invocation?.name === 'string' ? invocation.name : undefined;
        if (skillName) {
          const callId = `skill-invocation:${event.session_id}:${this.turnNumber}:${skillName}`;
          const skillKey = `${key}:skill-command:${skillName}`;
          const occurrenceId = skillOccurrenceId(event.payload, { name: skillName, source: 'command' });
          this.turn.commandSkill = {
            key: skillKey,
            startedAt: at,
            name: skillName,
            callId,
            common,
            chatId,
            ...(occurrenceId ? { occurrenceId } : {}),
          };
        }
        break;
      }
      case 'before_provider_request': {
        if (!this.turn) break;
        if (this.turn.generation) {
          const generation = this.turn.generation;
          const duration = Math.max(0, Date.parse(at) - Date.parse(generation.startedAt));
          const error = { type: 'cancelled', message: 'superseded by provider retry' };
          this.finishGeneration(
            generation,
            at,
            common,
            signalAttributes,
            duration,
            {
              'agentic.turn.number': this.turn.number,
              duration_ms: duration,
              'gen_ai.request.model': generation.model,
              'gen_ai.provider.name': generation.provider,
              'gen_ai.system_instructions': this.turn.systemPrompt,
              'agentic.usage.input_tokens.missing': 'true',
              'agentic.usage.output_tokens.missing': 'true',
              'error.type': error.type,
              'error.message': error.message,
            },
            {
              'gen_ai.usage.input_tokens': 0,
              'gen_ai.usage.output_tokens': 0,
              'agentic.usage.input_tokens.missing': 'true',
              'agentic.usage.output_tokens.missing': 'true',
              'agentic.llm.duration_ms': duration,
              'gen_ai.response.model': generation.model,
              'gen_ai.provider.name': generation.provider,
              'gen_ai.response.finish_reasons': json(['cancelled']),
            },
            error,
            'cancelled',
            'normal',
          );
        }
        const index = event.correlation.pi_turn_index ?? 0;
        const key = `${this.turn.key}:llm:${index}:${event.sequence}`;
        const payload = event.payload.payload as Record<string, unknown> | undefined;
        const model = String(payload?.model ?? this.model);
        const provider = providerName(payload?.provider ?? this.provider);
        if (payload?.model !== undefined) this.model = model;
        if (payload?.provider !== undefined) this.provider = provider;
        const inputAttributes = event.input_materialization
          ? (event.input_materialization.emitted_messages === undefined
              ? {}
              : { 'gen_ai.input.messages': json(event.input_materialization.emitted_messages) })
          : { 'gen_ai.input.messages': json(payload?.messages ?? [{ role: 'user', content: this.turn.prompt }]) };
        this.turn.generation = { key, startedAt: at, model, provider };
        this.emit({ type: 'span.start', key, parentKey: this.turn.key, name: `chat ${model}`, kind: 'LLM', timestamp: at, attributes: { ...common, 'gen_ai.chat.id': `${event.session_id}:${this.turnNumber}`, 'gen_ai.operation.name': 'chat', 'gen_ai.span.kind': 'LLM', 'gen_ai.request.model': model, 'gen_ai.provider.name': provider, ...inputAttributes, 'gen_ai.system_instructions': json(payload?.system ?? this.turn.systemPrompt) } });
        break;
      }
      case 'message_end': {
        if (!this.turn?.generation) break;
        const message = messageData(event.payload);
        if (String(message.role ?? '') !== 'assistant') break;
        const usage = usageAttributes(message, this.turn.generation.reportedUsage);
        const provider = providerName(message.provider ?? this.turn.generation.provider);
        const duration = Math.max(0, Date.parse(at) - Date.parse(this.turn.generation.startedAt));
        const errorMessage = typeof message.errorMessage === 'string' ? message.errorMessage : undefined;
        const isError = Boolean(errorMessage) || (this.turn.generation.responseStatus ?? 200) >= 400;
        const error = isError ? { type: 'api_error', message: errorMessage ?? `HTTP ${this.turn.generation.responseStatus}` } : undefined;
        const outputMessages = assistantMessages(message);
        this.turn.latestOutput = outputMessages;
        const attrs = { ...usage, 'agentic.llm.duration_ms': duration, 'gen_ai.response.model': String(message.model ?? this.turn.generation.model), 'gen_ai.response.finish_reasons': json([message.stopReason ?? 'unknown']), 'gen_ai.provider.name': provider, 'gen_ai.output.messages': outputMessages, ...(message.responseId ? { 'gen_ai.response.id': String(message.responseId) } : {}) };
        this.finishGeneration(
          this.turn.generation,
          at,
          common,
          signalAttributes,
          duration,
          { 'agentic.turn.number': this.turn.number, duration_ms: duration, 'gen_ai.request.model': this.turn.generation.model, 'gen_ai.response.model': String(message.model ?? this.turn.generation.model), 'gen_ai.provider.name': provider, ...usage, 'gen_ai.input.messages': json([{ role: 'user', content: this.turn.prompt }]), 'gen_ai.output.messages': json([{ role: 'assistant', content: message.content ?? '' }]), 'gen_ai.system_instructions': this.turn.systemPrompt, ...(error ? { 'error.type': error.type, 'error.message': error.message } : {}) },
          attrs,
          error,
          isError ? 'api_error' : undefined,
          'normal',
        );
        break;
      }
      case 'tool_execution_start': {
        if (!this.turn) break;
        const callId = canonicalToolCallId(event.payload.toolCallId);
        const rawName = canonicalToolName(event.payload.toolName);
        if (!callId || !rawName || this.tools.has(callId)) break;
        const identity = toolIdentity(
          callId,
          rawName,
          event.payload.args,
          nativeSkillEvidenceName(event.payload),
        );
        const key = `${this.turn.key}:tool:${callId}`;
        const argsPresent = Object.prototype.hasOwnProperty.call(event.payload, 'args');
        const candidate = event.payload.skillCandidate && typeof event.payload.skillCandidate === 'object'
          ? event.payload.skillCandidate as Record<string, unknown>
          : undefined;
        const skillCandidate = typeof candidate?.name === 'string' ? candidate.name : undefined;
        const candidateOccurrenceId = skillCandidate
          ? skillOccurrenceId(event.payload, { name: skillCandidate, source: 'catalog_read', sourceCallId: callId })
          : undefined;
        if (identity.type === 'skill') {
          this.turn.nativeSkillChannelObserved = true;
          const nativeOccurrenceId = skillOccurrenceId(event.payload, { name: identity.logicalName, source: 'native' });
          if (nativeOccurrenceId) this.turn.nativeSkillOccurrences.add(nativeOccurrenceId);
        }
        this.tools.set(callId, {
          key,
          startedAt: at,
          identity,
          args: event.payload.args,
          ...(skillCandidate ? { skillCandidate } : {}),
          ...(candidateOccurrenceId ? { skillOccurrenceId: candidateOccurrenceId } : {}),
        });
        addToolTraceTag(this.turn, identity.type, identity.logicalName);
        const parentCallId = canonicalToolCallId(event.payload.parentToolCallId);
        const parentKey = parentCallId && parentCallId !== callId ? this.tools.get(parentCallId)?.key : undefined;
        this.emit({ type: 'span.start', key, parentKey: parentKey ?? this.turn.key, name: `${identity.type} ${identity.rawName}`, kind: 'TOOL', timestamp: at, attributes: { ...common, 'gen_ai.chat.id': `${event.session_id}:${this.turnNumber}`, 'gen_ai.operation.name': 'execute_tool', 'gen_ai.span.kind': 'TOOL', 'gen_ai.tool.name': identity.rawName, 'gen_ai.tool.call.id': identity.callId, 'gen_ai.tool.call.arguments': json(event.payload.args), 'agentic.tool.type': identity.type, 'agentic.tool.name': identity.logicalName, 'agentic.tool.raw_name': identity.rawName, 'agentic.tool.id': identity.callId } });
        this.emit({ type: 'event', name: 'tool_call', spanKey: key, timestamp: at, attributes: { ...common, 'event.name': 'tool_call', ...this.eventSequenceAttributes(), 'gen_ai.operation.name': 'execute_tool', 'agentic.turn.number': this.turn.number, 'gen_ai.tool.name': identity.rawName, 'gen_ai.tool.call.id': identity.callId, 'agentic.tool.type': identity.type, 'agentic.tool.name': identity.logicalName, 'agentic.tool.raw_name': identity.rawName, 'agentic.tool.id': identity.callId, ...(argsPresent ? { 'gen_ai.tool.call.arguments': json(event.payload.args) } : { 'agentic.tool.input.missing': 'true' }), ...(argsPresent && event.projection.field_status?.args?.truncated ? { 'agentic.tool.input.truncated': 'true' } : {}) } });
        break;
      }
      case 'tool_execution_end': {
        const id = canonicalToolCallId(event.payload.toolCallId);
        if (!id) break;
        const tool = this.tools.get(id);
        if (!tool) break;
        const isError = event.payload.isError === true;
        const error = isError ? { type: 'tool_error', message: json(event.payload.result) } : undefined;
        const deriveSkill = !isError && this.turn !== undefined && tool.skillCandidate !== undefined;
        this.finishTool(tool, at, common, signalAttributes, event.payload.result, error, isError ? 'tool_error' : undefined, deriveSkill);
        if (deriveSkill && this.turn) {
          this.turn.pendingDerivedSkills.push({
            tool,
            name: tool.skillCandidate!,
            at,
            common,
            ...(tool.skillOccurrenceId ? { occurrenceId: tool.skillOccurrenceId } : {}),
          });
        }
        break;
      }
      case 'session_before_compact': {
        const key = this.turn ? `${this.turn.key}:compaction:${event.sequence}` : `compaction:${event.session_id}:${event.sequence}`;
        this.compaction = { key, startedAt: at };
        this.emit({ type: 'span.start', key, parentKey: this.turn?.key, name: 'Compaction', kind: 'COMPACTION', timestamp: at, attributes: { ...common, 'gen_ai.operation.name': 'execute_tool', 'gen_ai.span.kind': 'COMPACTION', 'agentic.compaction.trigger': String(event.payload.reason ?? 'unknown') } });
        break;
      }
      case 'session_compact': {
        this.finishCompaction(at, common);
        break;
      }
      case 'agent_settled':
        this.finishTurn(at);
        break;
      case 'session_shutdown':
        if (this.turn?.generation) {
          const generation = this.turn.generation;
          const duration = Math.max(0, Date.parse(at) - Date.parse(generation.startedAt));
          const error = { type: 'cancelled', message: 'session shutdown' };
          this.finishGeneration(
            generation,
            at,
            common,
            signalAttributes,
            duration,
            { 'agentic.turn.number': this.turn.number, duration_ms: duration, 'gen_ai.request.model': generation.model, 'gen_ai.provider.name': generation.provider, 'gen_ai.system_instructions': this.turn.systemPrompt, 'agentic.usage.input_tokens.missing': 'true', 'agentic.usage.output_tokens.missing': 'true', 'error.type': error.type, 'error.message': error.message },
            { 'gen_ai.usage.input_tokens': 0, 'gen_ai.usage.output_tokens': 0, 'agentic.usage.input_tokens.missing': 'true', 'agentic.usage.output_tokens.missing': 'true', 'agentic.llm.duration_ms': duration, 'gen_ai.response.model': generation.model, 'gen_ai.response.finish_reasons': json(['cancelled']) },
            error,
            'cancelled',
            'shutdown',
          );
        }
        for (const tool of [...this.tools.values()]) {
          const error = { type: 'cancelled', message: 'session shutdown' };
          this.finishTool(tool, at, common, signalAttributes, '[cancelled: session shutdown]', error, 'cancelled');
        }
        this.finishCompaction(at, common, { type: 'cancelled', message: 'session shutdown' });
        this.finishTurn(at, { type: 'cancelled', message: 'session shutdown' });
        this.emit({ type: 'event', name: 'session_end', timestamp: at, attributes: { ...common, 'event.name': 'session_end', ...this.eventSequenceAttributes(), 'gen_ai.operation.name': 'chat', 'gen_ai.session.id': event.session_id, 'agentic.turn.number': this.turnNumber } });
        break;
      default: break;
    }
  }
}
