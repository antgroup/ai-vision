import type { SessionTranscriptWriter } from './transcript-writer.js';
import type { OpenClawPluginApi } from 'openclaw/plugin-sdk/plugin-entry';

// Retain older callbacks and allow compiling against the supported 2026.7 SDK.
// Hosts ignore registrations for hooks they do not expose.
type PluginHookName = Parameters<OpenClawPluginApi['on']>[0]
  | 'before_agent_start' | 'subagent_spawning' | 'deactivate'
  | 'subagent_progress' | 'cron_reconciled'
  | 'skill_proposal_evaluate' | 'skill_proposal_changed' | 'skill_changed';

/** The runtime-owned typed hook catalog recorded by this plugin. */
export const RECORDED_TYPED_HOOKS = [
  'before_model_resolve',
  'agent_turn_prepare',
  'before_prompt_build',
  'before_agent_start',
  'before_agent_reply',
  'model_call_started',
  'model_call_ended',
  'llm_input',
  'llm_output',
  'before_agent_finalize',
  'agent_end',
  'before_compaction',
  'after_compaction',
  'before_reset',
  'inbound_claim',
  'channel_pairing_requested',
  'message_received',
  'message_sending',
  'reply_payload_sending',
  'message_sent',
  'before_tool_call',
  'after_tool_call',
  'tool_result_persist',
  'before_message_write',
  'session_start',
  'session_end',
  'subagent_spawning',
  'subagent_delivery_target',
  'subagent_spawned',
  'subagent_progress',
  'subagent_ended',
  'deactivate',
  'gateway_start',
  'gateway_stop',
  'heartbeat_prompt_contribution',
  'cron_reconciled',
  'cron_changed',
  'skill_proposal_evaluate',
  'skill_proposal_changed',
  'skill_changed',
  'before_dispatch',
  'reply_dispatch',
  'before_install',
  'before_agent_run',
  'resolve_exec_env',
] as const satisfies readonly PluginHookName[];

/**
 * Registers all known OpenClaw hooks and logs their event + context parameters as JSON.
 *
 * Covers all typed hooks plus the internal gateway:startup and command event hooks.
 * Each transcript entry includes instanceId to diagnose multi-instance registration issues.
 */
export function registerAllHooks(api: OpenClawPluginApi, writer: SessionTranscriptWriter, instanceId: string): void {
  // Use loose-typed wrapper: SDK's on() is typed with PluginHookName,
  // but we want to register any hook name for debugging.
  const on = api.on.bind(api) as (event: string, handler: (event: unknown, ctx: unknown) => unknown, opts?: { priority?: number }) => void;
  const registerHook = api.registerHook.bind(api);

  function makeTypedHandler(hookName: string) {
    return (event: unknown, ctx: unknown): void => {
      writer.record(hookName, event, ctx, instanceId);
    };
  }

  function makeInternalHandler(hookName: string) {
    return (event: unknown): void => {
      writer.record(hookName, event, null, instanceId);
    };
  }

  // Register the runtime-owned catalog instead of maintaining a partial local
  // list. This keeps replay complete when OpenClaw adds lifecycle surfaces such
  // as model_call_started/model_call_ended or before_agent_finalize.
  for (const hookName of RECORDED_TYPED_HOOKS) {
    on(hookName, makeTypedHandler(hookName));
  }

  // ── Internal Hooks (registerHook) ─────────────────────────────────────────

  // gateway:startup fires during gateway startup sequence (before gateway_start)
  registerHook(
    'gateway:startup',
    makeInternalHandler('gateway:startup'),
    { name: `openclaw-replay-gateway-startup-${instanceId}`, description: `Replay: record gateway:startup event (${instanceId})` }
  );

  // command events: /new, /reset, /stop (specific)
  registerHook(
    ['command:new', 'command:reset', 'command:stop'],
    (event: unknown): void => {
      const eventRecord = event as Record<string, unknown>;
      const action = eventRecord?.action as string | undefined;
      const hookName = action ? `command:${action}` : 'command:unknown';
      writer.record(hookName, event, null, instanceId);
    },
    { name: `openclaw-replay-commands-${instanceId}`, description: `Replay: record command events (${instanceId})` }
  );

  // command — general listener for all command events
  registerHook(
    'command',
    makeInternalHandler('command'),
    { name: `openclaw-replay-command-all-${instanceId}`, description: `Replay: record all command events (${instanceId})` }
  );

  // message internal hooks — fired by the internal hook runner
  registerHook(
    'message:received',
    makeInternalHandler('message:received'),
    { name: `openclaw-replay-message-received-${instanceId}`, description: `Replay: record message:received (${instanceId})` }
  );

  registerHook(
    'message:transcribed',
    makeInternalHandler('message:transcribed'),
    { name: `openclaw-replay-message-transcribed-${instanceId}`, description: `Replay: record message:transcribed (${instanceId})` }
  );

  registerHook(
    'message:preprocessed',
    makeInternalHandler('message:preprocessed'),
    { name: `openclaw-replay-message-preprocessed-${instanceId}`, description: `Replay: record message:preprocessed (${instanceId})` }
  );

  registerHook(
    'message:sent',
    makeInternalHandler('message:sent'),
    { name: `openclaw-replay-message-sent-${instanceId}`, description: `Replay: record message:sent (${instanceId})` }
  );

  registerHook(
    'message',
    makeInternalHandler('message'),
    { name: `openclaw-replay-message-all-${instanceId}`, description: `Replay: record all message events (${instanceId})` }
  );

  // session:compact — internal hook format for compaction lifecycle
  registerHook(
    'session:compact:before',
    makeInternalHandler('session:compact:before'),
    { name: `openclaw-replay-session-compact-before-${instanceId}`, description: `Replay: record session:compact:before (${instanceId})` }
  );

  registerHook(
    'session:compact:after',
    makeInternalHandler('session:compact:after'),
    { name: `openclaw-replay-session-compact-after-${instanceId}`, description: `Replay: record session:compact:after (${instanceId})` }
  );

  // agent:bootstrap fires before workspace bootstrap files are injected
  registerHook(
    'agent:bootstrap',
    makeInternalHandler('agent:bootstrap'),
    { name: `openclaw-replay-agent-bootstrap-${instanceId}`, description: `Replay: record agent:bootstrap (${instanceId})` }
  );
}
