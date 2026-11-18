/** 观测凭证相关共享类型与工具（与 Langfuse 兼容的摄入凭证）。 */

export interface CredentialInfo {
  id: number
  public_key: string
  scope: string
  name: string | null
  last_used_at: string | null
  rotated_at: string | null
}

export interface RevealedCredential {
  public_key: string
  secret_key: string
  scope: string
}

/** 提取 API business error 或旧格式响应中的服务端错误信息。 */
export function extractServerMessage(err: unknown): string | null {
  if (!(err instanceof Error)) return null
  const match = err.message.match(/-- (.+)$/)
  if (match) {
    try {
      const body = JSON.parse(match[1])
      if (typeof body.message === "string" && body.message) return body.message
    } catch {
      // Fall through to the original error message.
    }
  }
  return err.message || null
}
