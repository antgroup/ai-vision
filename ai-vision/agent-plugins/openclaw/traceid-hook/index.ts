/**
 * traceid-hook — POC plugin to verify before_tool_call parameter modification.
 *
 * Hooks before_tool_call for 'exec' tool and prepends a marker comment
 * to the command to verify that parameter modification works.
 *
 * Compatible with OpenClaw < 3.22 (no Plugin SDK types dependency).
 */

// For pre-3.22 compatibility: use `any` instead of Plugin SDK types.
// 3.22+ has OpenClawPluginApi; older versions pass a similar object
// but without typed generics.

export default {
  id: 'traceid-hook',
  name: 'TraceID Hook',
  description: 'Inject trace context into exec tool calls',

  register(api: any) {
    const logger = api.logger || console;
    logger.info?.('[traceid-hook] register() called');

    // Hook before_tool_call — return modified params to prove it works
    api.on('before_tool_call', (event: any, ctx: any) => {
      const toolName = event?.toolName || ctx?.toolName;

      // Only target exec (Bash) tool
      if (toolName !== 'exec') {
        return undefined; // No modification
      }

      const params = event?.params as Record<string, unknown> | undefined;
      const command = params?.command as string | undefined;
      if (!command) {
        return undefined;
      }

      // POC: Prepend a marker env var to the command
      // This proves the hook can modify tool parameters before execution.
      const marker = `TRACEID_HOOK_POC=1`;
      const modifiedCommand = `export ${marker} && ${command}`;

      logger.info?.(`[traceid-hook] exec intercepted: original="${command.slice(0, 80)}..."`);
      logger.info?.(`[traceid-hook] exec modified: prepended ${marker}`);

      // Return modified params — OpenClaw framework will use these
      return {
        params: {
          ...params,
          command: modifiedCommand,
        },
      };
    });

    logger.info?.('[traceid-hook] before_tool_call hook registered');
  },
};
