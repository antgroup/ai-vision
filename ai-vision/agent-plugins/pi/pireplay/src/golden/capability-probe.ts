import { readFileSync } from 'node:fs';

export type ProbeState = 'supported' | 'unsupported' | 'indeterminate';
export interface ProbeResult { state: ProbeState; evidence: string[]; }

export function probeCapability(file: string, capability: 'subagent' | 'mcp' | 'skill'): ProbeResult {
  try {
    const parsed = JSON.parse(readFileSync(file, 'utf8')) as {
      tools?: Array<{ name?: string }>;
      commands?: Array<{ name?: string; source?: string }>;
    };
    if (!Array.isArray(parsed.tools)) return { state: 'indeterminate', evidence: ['probe has no tools array'] };
    const pattern = capability === 'subagent' ? /(^|[-_:])(subagent|task)($|[-_:])/i : capability === 'mcp' ? /(^|[-_:])mcp($|[-_:])/i : /(^|[-_:])skills?($|[-_:])/i;
    const evidence = parsed.tools.filter(tool => pattern.test(tool.name ?? '')).map(tool => String(tool.name));
    if (capability === 'skill' && Array.isArray(parsed.commands)) {
      evidence.push(...parsed.commands
        .filter(command => command.source === 'skill' && typeof command.name === 'string')
        .map(command => String(command.name)));
    }
    const commandSummary = capability === 'skill' && Array.isArray(parsed.commands)
      ? ` and ${parsed.commands.length} available commands`
      : '';
    return evidence.length
      ? { state: 'supported', evidence }
      : { state: 'unsupported', evidence: [`no ${capability} capability among ${parsed.tools.length} configured tools${commandSummary}`] };
  } catch (error) {
    return { state: 'indeterminate', evidence: [error instanceof Error ? error.message : String(error)] };
  }
}
