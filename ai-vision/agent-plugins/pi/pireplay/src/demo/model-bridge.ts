import { readFileSync, writeFileSync, chmodSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';

const SUPPORTED_APIS = new Set(['anthropic-messages', 'openai-completions', 'openai-responses', 'azure-openai-responses', 'openai-codex-responses', 'mistral-conversations', 'google-generative-ai', 'google-vertex', 'bedrock-converse-stream']);

interface Dict { [key: string]: unknown }
export interface ModelBridgeResult { provider: string; model: string; apiKey: string; modelRef: string; baseUrl?: string; }

function object(value: unknown, label: string): Dict {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(`${label} must be an object`);
  return value as Dict;
}

function resolveSecret(value: unknown): string {
  if (typeof value === 'string') {
    if (value.startsWith('!')) throw new Error('command-based secret references are not supported');
    const match = value.match(/^\$\{?([A-Z_][A-Z0-9_]*)\}?$/);
    if (match) {
      const resolved = process.env[match[1]];
      if (!resolved) throw new Error(`required environment variable ${match[1]} is not set`);
      return resolved;
    }
    if (!value) throw new Error('empty API key');
    return value;
  }
  if (value && typeof value === 'object') {
    const ref = value as Dict;
    if (ref.source === 'env' && typeof (ref.name ?? ref.id) === 'string') {
      const name = String(ref.name ?? ref.id);
      const resolved = process.env[name];
      if (!resolved) throw new Error(`required environment variable ${name} is not set`);
      return resolved;
    }
  }
  throw new Error('unsupported SecretRef; only literal and environment references are accepted');
}

function pickModel(models: unknown[], id: string): Dict {
  const model = models.map(item => object(item, 'model')).find(item => String(item.id) === id);
  if (!model) throw new Error('default model was not found in its provider model list');
  return model;
}

export function createPiModelConfig(configPath: string, targetDir: string): ModelBridgeResult {
  const root = object(JSON.parse(readFileSync(configPath, 'utf8')), 'OpenClaw config');
  const agents = object(root.agents, 'agents');
  const defaults = object(agents.defaults, 'agents.defaults');
  const modelSetting = typeof defaults.model === 'string' ? defaults.model : object(defaults.model, 'agents.defaults.model').primary;
  if (typeof modelSetting !== 'string' || !modelSetting.includes('/')) throw new Error('agents.defaults.model.primary must be provider/model');
  const slash = modelSetting.indexOf('/');
  const sourceProvider = modelSetting.slice(0, slash);
  const modelId = modelSetting.slice(slash + 1);
  const modelsRoot = object(root.models, 'models');
  const providers = object(modelsRoot.providers, 'models.providers');
  const providerKey = Object.keys(providers).find(key => key.toLowerCase() === sourceProvider.toLowerCase());
  if (!providerKey) throw new Error('selected provider must be configured');
  const provider = object(providers[providerKey], 'selected provider');
  const model = pickModel(Array.isArray(provider.models) ? provider.models : [], modelId);
  const api = String(model.api ?? provider.api ?? '');
  if (!SUPPORTED_APIS.has(api)) throw new Error(`unsupported Pi API type: ${api || '<missing>'}`);
  const apiKey = resolveSecret(provider.apiKey ?? provider.auth);
  const safeProvider = `openclaw-${providerKey}`.replace(/[^a-zA-Z0-9._-]/g, '-');
  const allow = (source: Dict, keys: string[]): Dict => Object.fromEntries(keys.filter(key => source[key] !== undefined).map(key => [key, source[key]]));
  const output = {
    providers: {
      [safeProvider]: {
        ...allow(provider, ['baseUrl', 'authHeader']),
        api,
        apiKey: '$OBSERV_PI_DEMO_API_KEY',
        models: [{ ...allow(model, ['id', 'name', 'reasoning', 'input', 'contextWindow', 'maxTokens', 'cost']), id: modelId }]
      }
    }
  };
  mkdirSync(targetDir, { recursive: true, mode: 0o700 });
  const outputPath = join(targetDir, 'models.json');
  writeFileSync(outputPath, `${JSON.stringify(output, null, 2)}\n`, { mode: 0o600 });
  chmodSync(outputPath, 0o600);
  return { provider: safeProvider, model: modelId, apiKey, modelRef: `${safeProvider}/${modelId}`, baseUrl: typeof provider.baseUrl === 'string' ? provider.baseUrl : undefined };
}
