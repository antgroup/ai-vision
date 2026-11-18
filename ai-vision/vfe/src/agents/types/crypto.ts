/**
 * Agent Dashboard V2 — Encrypted query payload
 *
 * Matches docs/v2/encrypted-query-protocol.md §4.
 */

import { z } from "zod"

export const EncryptedPayloadSchema = z.object({
  payloadVersion: z.literal("v1"),
  kid: z.string(),
  iv: z.string(),
  encryptedPayload: z.string(),
})
export type EncryptedPayload = z.infer<typeof EncryptedPayloadSchema>
