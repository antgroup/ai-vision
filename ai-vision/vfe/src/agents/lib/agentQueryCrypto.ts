/**
 * Agent Dashboard V2 -- Encrypted query payload helpers
 *
 * Implements AES-GCM encryption/decryption for agent query payloads
 * per docs/v2/encrypted-query-protocol.md section 6.
 *
 * Prefers native Web Crypto. Ordinary HTTP can use the same wire format
 * with a lazy-loaded AES-GCM implementation and browser-provided randomness.
 */

import type {
  AgentQueryBatchPayload,
  AgentQueryPayload,
  EncryptedPayload,
} from "@/agents/types"
import {
  AgentQueryCipherLoadError,
  AgentQueryCryptoUnavailableError,
} from "./dashboardQueryError"

// ---------------------------------------------------------------------------
// base64url helpers
// ---------------------------------------------------------------------------

/** Encode a Uint8Array to a base64url string (no padding). */
function bytesToBase64Url(bytes: Uint8Array): string {
  let binary = ""
  for (let i = 0; i < bytes.length; i++) {
    binary += String.fromCharCode(bytes[i])
  }
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "")
}

/** Decode a base64url string (with or without padding) to a Uint8Array. */
function base64UrlToBytes(b64url: string): Uint8Array<ArrayBuffer> {
  let b64 = b64url.replace(/-/g, "+").replace(/_/g, "/")
  const pad = (4 - (b64.length % 4)) % 4
  b64 += "=".repeat(pad)
  const binary = atob(b64)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i)
  }
  return bytes
}

// ---------------------------------------------------------------------------
// Env var access
// ---------------------------------------------------------------------------

function isEncryptionEnabled(): boolean {
  const raw = import.meta.env.VITE_AGENT_QUERY_ENCRYPTION_ENABLED
  if (raw === "true") return true
  if (raw !== "true") return false
  return true
}

/** Browsers expose secure randomness even on ordinary HTTP origins. */
export function requireAgentQueryRandom(): Crypto {
  const crypto = globalThis.crypto
  if (typeof crypto?.getRandomValues !== "function") {
    throw new AgentQueryCryptoUnavailableError()
  }
  return crypto
}

// ---------------------------------------------------------------------------
// Key management (runtime, from backend API)
// ---------------------------------------------------------------------------

type CipherOperation = (
  iv: Uint8Array<ArrayBuffer>,
  bytes: Uint8Array<ArrayBuffer>
) => Uint8Array | Promise<Uint8Array>

type QueryCipher = {
  kid: string
  encrypt: CipherOperation
  decrypt: CipherOperation
}

let cachedCipher: QueryCipher | null = null
let keyImportPromise: Promise<void> | null = null

async function createCipher(
  kid: string,
  rawKey: Uint8Array<ArrayBuffer>,
  subtle: SubtleCrypto | undefined
): Promise<QueryCipher> {
  if (
    typeof subtle?.importKey === "function" &&
    typeof subtle.encrypt === "function" &&
    typeof subtle.decrypt === "function"
  ) {
    const key = await subtle.importKey(
      "raw",
      rawKey,
      { name: "AES-GCM" },
      false,
      ["encrypt", "decrypt"]
    )
    return {
      kid,
      encrypt: async (iv, bytes) =>
        new Uint8Array(
          await subtle.encrypt({ name: "AES-GCM", iv }, key, bytes)
        ),
      decrypt: async (iv, bytes) =>
        new Uint8Array(
          await subtle.decrypt({ name: "AES-GCM", iv }, key, bytes)
        ),
    }
  }

  try {
    const { gcm } = await import("@noble/ciphers/aes.js")
    return {
      kid,
      encrypt: (iv, bytes) => gcm(rawKey, iv).encrypt(bytes),
      decrypt: (iv, bytes) => gcm(rawKey, iv).decrypt(bytes),
    }
  } catch (cause) {
    throw new AgentQueryCipherLoadError(cause)
  }
}

/** Cache the cipher for the key returned by the authenticated query-config. */
export async function setEncryptionKey(
  kid: string,
  keyB64: string
): Promise<void> {
  const crypto = requireAgentQueryRandom()
  if (cachedCipher?.kid === kid) return
  if (keyImportPromise) {
    await keyImportPromise
    return setEncryptionKey(kid, keyB64)
  }

  const rawKey = base64UrlToBytes(keyB64)
  if (![16, 24, 32].includes(rawKey.length)) {
    throw new Error("Invalid AES query key length")
  }
  keyImportPromise = createCipher(kid, rawKey, crypto.subtle)
    .then((cipher) => {
      cachedCipher = cipher
    })
    .finally(() => {
      keyImportPromise = null
    })
  await keyImportPromise
}

/** Returns true if the encryption key has been loaded from the backend. */
export function isKeyReady(): boolean {
  return cachedCipher !== null
}

function getCipher(): QueryCipher {
  if (!cachedCipher) {
    throw new Error(
      "Encryption key not initialized — call setEncryptionKey() first"
    )
  }
  return cachedCipher
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Encrypt an AgentQueryPayload using AES-256-GCM.
 *
 * Follows the protocol in docs/v2/encrypted-query-protocol.md section 6:
 * 1. JSON.stringify the payload
 * 2. Encode to UTF-8 bytes
 * 3. Generate random 12-byte IV
 * 4. AES-GCM encrypt
 * 5. base64url-encode IV and ciphertext+tag
 */
export async function encryptAgentQueryPayload(
  payload: AgentQueryPayload | AgentQueryBatchPayload
): Promise<EncryptedPayload> {
  const crypto = requireAgentQueryRandom()
  const cipher = getCipher()
  const iv = crypto.getRandomValues(new Uint8Array(12))
  const plaintext = new TextEncoder().encode(JSON.stringify(payload))
  const encrypted = await cipher.encrypt(iv, plaintext)

  return {
    payloadVersion: "v1",
    kid: cipher.kid,
    iv: bytesToBase64Url(iv),
    encryptedPayload: bytesToBase64Url(encrypted),
  }
}

/**
 * Decrypt an EncryptedPayload back to an AgentQueryPayload.
 *
 * Intended for local dev debugging only -- production backends
 * handle decryption server-side.
 */
export async function decryptAgentQueryPayload(
  encrypted: EncryptedPayload
): Promise<AgentQueryPayload> {
  const decrypted = await getCipher().decrypt(
    base64UrlToBytes(encrypted.iv),
    base64UrlToBytes(encrypted.encryptedPayload)
  )

  const text = new TextDecoder().decode(decrypted)
  return JSON.parse(text) as AgentQueryPayload
}

/** Returns true when env vars indicate encryption should be applied. */
export { isEncryptionEnabled }
