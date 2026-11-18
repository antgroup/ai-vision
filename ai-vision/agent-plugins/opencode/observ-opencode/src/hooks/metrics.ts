import {
  MetricAgentApiRequestCountAttributes,
  MetricAgentApiRequestErrorCountAttributes,
  MetricAgentApiRequestDurationAttributes,
  classifyAgenticErrorCategory,
} from '../semconv.js';
import type { HookContext, TurnState } from './types.js';

interface ApiRequestMetricInput {
  model: string;
  providerName?: string;
  durationMs?: number;
  errorType?: string;
  turn?: TurnState;
  messageId?: string;
}

export function emitApiRequestMetrics(ctx: HookContext, input: ApiRequestMetricInput): void {
  const countAndErrorAlreadyEmitted = !!(
    input.turn
    && input.messageId
    && input.turn.apiRequestMetricEmittedMsgIds.has(input.messageId)
  );

  if (input.turn && input.messageId) {
    input.turn.apiRequestMetricEmittedMsgIds.add(input.messageId);
  }

  const username = ctx.resolveUsername();

  if (ctx.apiRequestCounter && !countAndErrorAlreadyEmitted) {
    ctx.apiRequestCounter.add(1, {
      [MetricAgentApiRequestCountAttributes.GEN_AI_REQUEST_MODEL]: input.model,
      ...(input.providerName ? { [MetricAgentApiRequestCountAttributes.GEN_AI_PROVIDER_NAME]: input.providerName } : {}),
      [MetricAgentApiRequestCountAttributes.ANT_AGENT_TYPE]: ctx.config.otelExporter.resourceAttributes['ant.agent.type'] || 'opencode',
      [MetricAgentApiRequestCountAttributes.ANT_USERNAME]: username,
    });
  }

  if (ctx.apiRequestErrorCounter && input.errorType && !countAndErrorAlreadyEmitted) {
    ctx.apiRequestErrorCounter.add(1, {
      [MetricAgentApiRequestErrorCountAttributes.GEN_AI_REQUEST_MODEL]: input.model,
      [MetricAgentApiRequestErrorCountAttributes.ANT_USERNAME]: username,
      [MetricAgentApiRequestErrorCountAttributes.AGENTIC_ERROR_CATEGORY]: classifyAgenticErrorCategory(input.errorType),
    });
  }

  const durationAlreadyEmitted = !!(
    input.turn
    && input.messageId
    && input.turn.apiRequestDurationMetricEmittedMsgIds.has(input.messageId)
  );

  if (
    ctx.apiRequestDurationHistogram
    && input.durationMs !== undefined
    && Number.isFinite(input.durationMs)
    && input.durationMs >= 0
    && !durationAlreadyEmitted
  ) {
    if (input.turn && input.messageId) {
      input.turn.apiRequestDurationMetricEmittedMsgIds.add(input.messageId);
    }
    ctx.apiRequestDurationHistogram.record(input.durationMs, {
      [MetricAgentApiRequestDurationAttributes.GEN_AI_REQUEST_MODEL]: input.model,
      ...(input.providerName ? { [MetricAgentApiRequestDurationAttributes.GEN_AI_PROVIDER_NAME]: input.providerName } : {}),
      [MetricAgentApiRequestDurationAttributes.ANT_AGENT_TYPE]: ctx.config.otelExporter.resourceAttributes['ant.agent.type'] || 'opencode',
      [MetricAgentApiRequestDurationAttributes.ANT_USERNAME]: username,
    });
  }
}
