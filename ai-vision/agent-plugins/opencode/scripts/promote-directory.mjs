#!/usr/bin/env node

import { existsSync, renameSync, rmSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

export function promoteDirectory(stagedPath, targetPath, options = {}) {
  if (!existsSync(stagedPath)) throw new Error(`Staged directory does not exist: ${stagedPath}`);
  const backupPath = `${targetPath}.backup-${process.pid}`;
  if (existsSync(backupPath)) throw new Error(`Cache backup already exists: ${backupPath}`);
  const hadTarget = existsSync(targetPath);
  let promoted = false;

  try {
    if (hadTarget) renameSync(targetPath, backupPath);
    options.beforePromote?.();
    renameSync(stagedPath, targetPath);
    promoted = true;
  } catch (error) {
    const rollbackErrors = [];
    try {
      if (promoted) rmSync(targetPath, { recursive: true, force: true });
      if (existsSync(backupPath)) renameSync(backupPath, targetPath);
    } catch (rollbackError) {
      rollbackErrors.push(rollbackError);
    }
    if (rollbackErrors.length > 0) {
      throw new AggregateError([error, ...rollbackErrors], 'Cache promotion and rollback failed');
    }
    throw error;
  }

  try {
    rmSync(backupPath, { recursive: true, force: true });
  } catch (error) {
    console.error(`[opencode] promoted cache but could not remove backup ${backupPath}: ${error}`);
  }
}

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : undefined;
if (invokedPath && fileURLToPath(import.meta.url) === invokedPath) {
  const [stagedPath, targetPath] = process.argv.slice(2).map((path) => path && resolve(path));
  if (!stagedPath || !targetPath) throw new Error('Usage: promote-directory.mjs <staged> <target>');
  promoteDirectory(stagedPath, targetPath);
}
