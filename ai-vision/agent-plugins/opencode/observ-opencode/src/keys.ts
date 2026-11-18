/**
 * Runtime XOR decryption and embedded Langfuse config resolution.
 *
 * The encrypted key data is generated at build time by scripts/generate-keys.ts.
 * This module decrypts the keys on demand and selects prod/gray based on env vars.
 */

import {
  HAS_EMBEDDED_KEYS,
  EMBEDDED_EDITION,
  PROD_PK_ENC, PROD_PK_MASK,
  PROD_SK_ENC, PROD_SK_MASK,
  PROD_HOST_ENC, PROD_HOST_MASK,
  GRAY_PK_ENC, GRAY_PK_MASK,
  GRAY_SK_ENC, GRAY_SK_MASK,
  GRAY_HOST_ENC, GRAY_HOST_MASK,
} from './keys.generated.js';

export interface LangfuseEmbeddedConfig {
  edition: string;
  env: 'prod' | 'gray';
  publicKey: string;
  secretKey: string;
  host: string;
}

/** XOR-decrypt a byte array pair back to a UTF-8 string. */
function xorDecrypt(enc: number[], mask: number[]): string {
  const buf = Buffer.alloc(enc.length);
  for (let i = 0; i < enc.length; i++) {
    buf[i] = enc[i]! ^ mask[i]!;
  }
  return buf.toString('utf-8');
}

/**
 * Resolve embedded Langfuse config if available.
 *
 * Returns null for common edition (no embedded keys).
 * Selects gray env when ANT_GRAY=true or OBSERV_OPENCODE_LANGFUSE_ENV=gray.
 */
export function resolveEmbeddedLangfuseConfig(): LangfuseEmbeddedConfig | null {
  if (!HAS_EMBEDDED_KEYS) return null;

  const langfuseEnv = process.env.OBSERV_OPENCODE_LANGFUSE_ENV?.trim().toLowerCase() ?? '';
  const antGray = process.env.ANT_GRAY?.trim().toLowerCase() ?? '';
  const isGray = langfuseEnv === 'gray' || antGray === 'true';

  if (isGray) {
    return {
      edition: EMBEDDED_EDITION,
      env: 'gray',
      publicKey: xorDecrypt(GRAY_PK_ENC, GRAY_PK_MASK),
      secretKey: xorDecrypt(GRAY_SK_ENC, GRAY_SK_MASK),
      host: xorDecrypt(GRAY_HOST_ENC, GRAY_HOST_MASK),
    };
  }

  return {
    edition: EMBEDDED_EDITION,
    env: 'prod',
    publicKey: xorDecrypt(PROD_PK_ENC, PROD_PK_MASK),
    secretKey: xorDecrypt(PROD_SK_ENC, PROD_SK_MASK),
    host: xorDecrypt(PROD_HOST_ENC, PROD_HOST_MASK),
  };
}
