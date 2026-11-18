/**
 * Best-effort Git repository discovery for Resource attributes.
 *
 * Mirrors claudecode/cchooks/src/common/git_state.rs: one bounded
 * `git remote -v` snapshot ranked into a single `vcs.repository.url.full`,
 * plus `vcs.ref.head.name` and `vcs.ref.head.revision` probes. Missing Git,
 * non-Git directories, missing remotes, and command failures never break
 * telemetry startup; they just omit the attribute (opencode's
 * `src/vcs.ts` uses the same fail-soft contract).
 */

import { execFileSync } from 'node:child_process';

const GIT_TIMEOUT_MS = 1000;

interface RemoteCandidate {
  name: string;
  url: string;
  kind: 'fetch' | 'push';
}

/** Rank mirrors cchooks `remote_rank`: origin, fetch, then stable lexical order. */
function remoteRank(candidate: RemoteCandidate): readonly [number, number, string, string] {
  const name = candidate.name.toLowerCase();
  const url = candidate.url.toLowerCase();
  return [
    name !== 'origin' ? 1 : 0,
    candidate.kind === 'fetch' ? 0 : 1,
    name,
    url,
  ] as const;
}

function selectRemote(snapshot: string): string | undefined {
  const candidates: RemoteCandidate[] = [];
  for (const line of snapshot.split('\n')) {
    const fields = line.trim().split(/\s+/);
    if (fields.length !== 3) continue;
    const [name, url, kindRaw] = fields;
    if (!name || !url) continue;
    if (kindRaw !== '(fetch)' && kindRaw !== '(push)') continue;
    candidates.push({ name, url, kind: kindRaw === '(fetch)' ? 'fetch' : 'push' });
  }
  if (candidates.length === 0) return undefined;
  candidates.sort((a, b) => {
    const rankA = remoteRank(a);
    const rankB = remoteRank(b);
    for (let i = 0; i < rankA.length; i += 1) {
      const va = rankA[i]!;
      const vb = rankB[i]!;
      if (va === vb) continue;
      return va < vb ? -1 : 1;
    }
    return 0;
  });
  return candidates[0]!.url;
}

function runGit(cwd: string, args: string[]): string | undefined {
  try {
    return execFileSync('git', args, {
      cwd,
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore'],
      timeout: GIT_TIMEOUT_MS,
    }).trim();
  } catch {
    return undefined;
  }
}

export interface VcsResourceAttributes {
  'vcs.repository.url.full'?: string;
  'vcs.ref.head.name'?: string;
  'vcs.ref.head.revision'?: string;
}

/**
 * Probe a directory for Git identity. Each attribute is independent: a broken
 * probe omits only that key (cchooks keeps emitted spans when Git is absent).
 */
export function resolveVcsAttributes(cwd: string): VcsResourceAttributes {
  const snapshot = runGit(cwd, ['remote', '-v']);
  const remote = snapshot ? selectRemote(snapshot) : undefined;
  const branch = runGit(cwd, ['rev-parse', '--abbrev-ref', 'HEAD']);
  const revision = runGit(cwd, ['rev-parse', 'HEAD']);
  return {
    ...(remote ? { 'vcs.repository.url.full': remote } : {}),
    ...(branch ? { 'vcs.ref.head.name': branch } : {}),
    ...(revision ? { 'vcs.ref.head.revision': revision } : {}),
  };
}
