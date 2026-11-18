export interface Finding { rule_id?: string; level?: string; message?: string; [key: string]: unknown }
export function diagnose(findings: Finding[]): Record<string, unknown> {
  const groups: Record<string, Finding[]> = { resource: [], trace: [], event: [], metric: [], semconv: [], capability: [], integrity: [], other: [] };
  const normalized = findings.map(item => item.finding && typeof item.finding === 'object'
    ? { ...item, ...(item.finding as Finding) }
    : item);
  for (const finding of normalized) {
    const text = `${finding.rule_id ?? ''} ${finding.message ?? ''}`.toLowerCase();
    const key = text.includes('resource') ? 'resource' : text.includes('metric') ? 'metric' : text.includes('event') || text.includes('log') ? 'event' : text.includes('semconv') || text.includes('unknown_attribute') ? 'semconv' : text.includes('capability') ? 'capability' : text.includes('integrity') || text.includes('transcript') ? 'integrity' : text.includes('span') || text.includes('trace') ? 'trace' : 'other';
    groups[key].push(finding);
  }
  return { summary: { total: normalized.length, violations: normalized.filter(item => item.level === 'violation').length }, groups };
}
