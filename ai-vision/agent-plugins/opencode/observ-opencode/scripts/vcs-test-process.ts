import childProcess from 'node:child_process';
import { syncBuiltinESMExports } from 'node:module';

/** Replace only Git's executable in the test process, preserving the production
 * execFile options and callbacks. The fixture runs as a real Node child on every
 * OS; no shell, PATH shim, or synthetic timeout callback is involved. */
export function interceptGitProcess(script: string): () => void {
  const original = childProcess.execFile;
  childProcess.execFile = ((...args: unknown[]) => {
    if (args[0] === 'git') {
      return Reflect.apply(original, childProcess, [
        process.execPath,
        [script, ...(args[1] as string[])],
        ...args.slice(2),
      ]);
    }
    return Reflect.apply(original, childProcess, args);
  }) as typeof childProcess.execFile;
  syncBuiltinESMExports();
  return () => {
    childProcess.execFile = original;
    syncBuiltinESMExports();
  };
}
