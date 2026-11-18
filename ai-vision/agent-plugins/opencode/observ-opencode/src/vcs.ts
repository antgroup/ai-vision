import { execFile } from 'node:child_process';
import type { ObservOpencodeConfig } from './config.js';

const GIT_TIMEOUT_MS = 1_000;
const GIT_MAX_BUFFER_BYTES = 64 * 1024;

export interface GitCommandRequest {
  cwd: string;
  args: readonly string[];
  timeoutMs: number;
  maxBufferBytes: number;
}

export type GitCommandRunner = (request: GitCommandRequest) => Promise<string | undefined>;

export interface VcsProbeContext {
  worktree?: string;
  project?: { worktree?: string; vcs?: 'git' };
}

export type VcsResourceAttributes = Partial<Record<
  'vcs.repository.url.full' | 'vcs.ref.head.name',
  string
>>;

function runGitCommand(request: GitCommandRequest): Promise<string | undefined> {
  return new Promise(resolve => {
    try {
      execFile(
        'git',
        [...request.args],
        {
          cwd: request.cwd,
          encoding: 'utf8',
          timeout: request.timeoutMs,
          killSignal: 'SIGKILL',
          maxBuffer: request.maxBufferBytes,
          windowsHide: true,
        },
        (error, stdout) => resolve(error ? undefined : stdout),
      );
    } catch {
      resolve(undefined);
    }
  });
}

async function safeRun(
  runner: GitCommandRunner,
  request: GitCommandRequest,
): Promise<string | undefined> {
  try {
    return await runner(request);
  } catch {
    return undefined;
  }
}

export function resolveVcsProbeDirectory(context: VcsProbeContext): string | undefined {
  if (context.project?.vcs !== 'git') return undefined;
  return context.worktree?.trim()
    || context.project?.worktree?.trim()
    || undefined;
}

export async function discoverVcsResourceAttributes(
  directory: string,
  runner: GitCommandRunner = runGitCommand,
): Promise<VcsResourceAttributes> {
  const request = (args: readonly string[]): GitCommandRequest => ({
    cwd: directory,
    args,
    timeoutMs: GIT_TIMEOUT_MS,
    maxBufferBytes: GIT_MAX_BUFFER_BYTES,
  });
  const remotePromise = safeRun(runner, request(['remote', 'get-url', 'origin']));
  const branchPromise = safeRun(runner, request(['rev-parse', '--abbrev-ref', 'HEAD']));
  const [remoteOutput, branchOutput] = await Promise.all([remotePromise, branchPromise]);
  const remote = remoteOutput?.trim();
  const branch = branchOutput?.trim();

  return {
    ...(remote ? { 'vcs.repository.url.full': remote } : {}),
    ...(branch ? { 'vcs.ref.head.name': branch } : {}),
  };
}

export function mergeVcsResourceAttributes(
  config: ObservOpencodeConfig,
  detected: VcsResourceAttributes,
): ObservOpencodeConfig {
  return {
    ...config,
    otelExporter: {
      ...config.otelExporter,
      resourceAttributes: {
        ...detected,
        ...config.otelExporter.resourceAttributes,
      },
    },
  };
}
