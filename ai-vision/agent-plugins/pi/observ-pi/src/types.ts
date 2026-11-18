export type PiHook =
  | 'session_start' | 'session_shutdown' | 'before_agent_start' | 'agent_settled'
  | 'turn_start' | 'before_provider_request' | 'after_provider_response' | 'provider_stream_event'
  | 'message_update' | 'message_end' | 'tool_execution_start'
  | 'tool_execution_update' | 'tool_execution_end'
  | 'session_before_compact' | 'session_compact' | 'model_select';

export interface Correlation {
  chat_id?: string;
  user_turn?: number;
  pi_turn_index?: number;
  generation_id?: string;
  api_attempt_id?: string;
  tool_call_id?: string;
}

export type InputMaterializationRelation =
  | 'initial_reset'
  | 'retry_equal'
  | 'append'
  | 'turn_boundary'
  | 'shrink_reset'
  | 'rewrite_reset'
  | 'scope_reset'
  | 'unavailable_reset';

export interface InputMaterializationScope {
  provider: string;
  model: string;
  source: string;
}

export interface InputMaterialization {
  relation: InputMaterializationRelation;
  scope: InputMaterializationScope;
  previous_count: number;
  current_count: number;
  emitted_count: number;
  emitted_messages?: unknown[];
}

export interface CanonicalEvent {
  schema_version: 1;
  timestamp: string;
  observed_at_unix_nano: string;
  session_id: string;
  event_id: string;
  sequence: number;
  previous_checksum: string;
  checksum: string;
  hook: PiHook;
  correlation: Correlation;
  payload: Record<string, unknown>;
  input_materialization?: InputMaterialization;
  projection: {
    redacted: boolean;
    truncated: boolean;
    fields: string[];
    field_status?: Record<string, { redacted?: boolean; truncated?: boolean }>;
  };
}

/**
 * Deliberate boundary for any future native Pi session enrichment. Native
 * records must be normalized into this shape before the semantic reducer sees
 * them; sinks never consume Pi's session JSONL directly.
 */
export interface CanonicalSupplementalEvent extends CanonicalEvent {
  hook: 'message_end';
  payload: CanonicalEvent['payload'] & { supplemental_source: 'pi_session' };
}

export type SpanKindName = 'CHAT' | 'LLM' | 'TOOL' | 'COMPACTION';

export type SemanticObservation =
  | { type: 'span.start'; key: string; parentKey?: string; linkKeys?: string[]; name: string; kind: SpanKindName; timestamp: string; attributes: Record<string, unknown> }
  | { type: 'span.end'; key: string; timestamp: string; attributes: Record<string, unknown>; error?: { type: string; message: string }; retainForLink?: boolean }
  | { type: 'event'; name: string; spanKey?: string; timestamp: string; attributes: Record<string, unknown> }
  | { type: 'metric'; name: string; value: number; metricKind: 'counter' | 'histogram'; attributes: Record<string, string> };

export interface CollectionIntegrity {
  complete: boolean;
  lastSequence: number;
  lastChecksum: string;
  expectedSequence: number;
  expectedChecksum: string;
  errors: string[];
}
