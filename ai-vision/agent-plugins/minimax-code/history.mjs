import { createReadStream } from 'node:fs';
import { lstat, readdir } from 'node:fs/promises';
import { join } from 'node:path';
import { createInterface } from 'node:readline';

async function directories(path) {
  try {
    return (await readdir(path, { withFileTypes: true }))
      .filter(entry => entry.isDirectory())
      .map(entry => entry.name)
      .sort().reverse();
  } catch (error) {
    if (error.code === 'ENOENT') return [];
    throw error;
  }
}

// mcode v0.7.2 indexes session history under v2/sessions/<year>/<month>/<day>.
// A resumed session can retain its original date. Select by ID, never by mtime.
export async function readSessionRows(profile, sessionId) {
  const root = join(profile, 'v2', 'sessions');
  const suffix = '-session_' + Buffer.from(sessionId).toString('base64url');
  for (const year of await directories(root)) {
    if (!/^\d{4}$/.test(year)) continue;
    for (const month of await directories(join(root, year))) {
      if (!/^\d{2}$/.test(month)) continue;
      for (const day of await directories(join(root, year, month))) {
        if (!/^\d{2}$/.test(day)) continue;
        const date = join(root, year, month, day);
        const session = (await directories(date)).find(name => name.endsWith(suffix));
        if (session) return readRows(join(date, session, 'messages.jsonl'));
      }
    }
  }
  return [];
}

export async function readRows(path) {
  try {
    if (!(await lstat(path)).isFile()) return [];
  } catch (error) {
    if (error.code === 'ENOENT') return [];
    throw error;
  }

  const input = createReadStream(path);
  const lines = createInterface({ input, crlfDelay: Infinity });
  const rows = [];
  for await (const line of lines) {
    try {
      const row = JSON.parse(line);
      if (row && typeof row === 'object') rows.push(row);
    } catch (error) {
      // An interrupted process can leave a partially written final record.
      if (!(error instanceof SyntaxError)) throw error;
    }
  }
  return rows;
}
