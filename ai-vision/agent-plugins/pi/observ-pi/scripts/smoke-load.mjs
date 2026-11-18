import net from 'node:net';
import { pathToFileURL } from 'node:url';

const entry = process.argv[2];
const implementationEntry = process.argv[3];
if (!entry || !implementationEntry) {
  throw new Error('usage: smoke-load.mjs <plugin-entry> <implementation-entry>');
}

net.Socket.prototype.connect = function networkDenied() {
  throw new Error('network access is forbidden during release smoke-load');
};
globalThis.fetch = async () => {
  throw new Error('network access is forbidden during release smoke-load');
};

const plugin = await import(pathToFileURL(entry).href);
if (typeof plugin.default !== 'function') {
  throw new Error('plugin default export must be a function');
}

const implementation = await import(pathToFileURL(implementationEntry).href);
const pluginExports = Object.keys(plugin).sort();
const implementationExports = Object.keys(implementation).sort();
if (JSON.stringify(pluginExports) !== JSON.stringify(implementationExports)) {
  throw new Error(
    `package entry exports differ from implementation: ${pluginExports.join(', ')} != ${implementationExports.join(', ')}`,
  );
}
const namedExports = pluginExports.filter(name => name !== 'default');
if (namedExports.length === 0) {
  throw new Error('package entry must preserve named exports');
}
for (const name of pluginExports) {
  if (plugin[name] !== implementation[name]) {
    throw new Error(`package entry must re-export ${name} without wrapping it`);
  }
}
