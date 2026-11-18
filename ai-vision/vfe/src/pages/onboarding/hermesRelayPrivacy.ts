import {
  omittedPathError,
  type PathError,
} from "../../labs/privacy/relayPrivacyPolicy.ts"

export interface HermesRelayPrivacyOptions {
  personalInformation: boolean
  omittedPaths: string[]
}

export const HERMES_RELAY_DEFAULT_PRIVACY: HermesRelayPrivacyOptions = {
  personalInformation: false,
  omittedPaths: [],
}

export function parseHermesOmittedPaths(text: string): {
  paths: string[]
  error: PathError | null
} {
  const paths = [
    ...new Set(
      text
        .split(/\r?\n/u)
        .map((line) => line.trim())
        .filter(Boolean)
    ),
  ]
  return { paths, error: omittedPathError(paths) }
}

// Rust-regex syntax, evaluated locally by Relay on observability copies only.
// These bounded known formats are not a detector for every possible secret.
const CREDENTIAL_PATTERN = String.raw`(?i)\b(?:basic\s+[a-z0-9+/=]+|gh[pousr]_[a-z0-9]{20,}|github_pat_[a-z0-9_]{20,}|glpat-[a-z0-9_-]{20,}|xox[baprs]-[a-z0-9-]{10,}|(?:hf|npm)_[a-z0-9]{20,}|nvapi-[a-z0-9_-]{16,})`
const SECRET_NAMES = String.raw`(?:api[-_]?key|access[-_]?token|refresh[-_]?token|client[-_]?secret|proxy[-_]?authorization|authorization|set[-_]?cookie|cookie|password|passwd|secret)`
const JSON_CREDENTIAL_PATTERN = String.raw`(?i)("${SECRET_NAMES}"\s*:\s*)"(?:\\[\s\S]|[^"\\\r\n])*"`
const JSON_TEXT_CREDENTIAL_PATTERN = String.raw`(?i)("(?:\\[\s\S]|[^"\\\r\n])*?\b${SECRET_NAMES}\s*[:=]\s*)(?:\\[\s\S]|[^"\\\r\n])*"`
const LABELED_CREDENTIAL_PATTERN = String.raw`(?i)(\b${SECRET_NAMES}\s*[:=]\s*)(?:"(?:\\[\s\S]|[^"\\\r\n])*"|'(?:\\[\s\S]|[^'\\\r\n])*'|(?:\\[\s\S]|[^\s,;}"'\\])+)`
// Preserve the JSON string terminator when an unterminated PEM is embedded
// in encoded tool arguments. \z means end-of-input, never end-of-line.
const PRIVATE_KEY_PATTERN = String.raw`-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----(?:\\[\s\S]|[^"'\\])*?(?:-----END (?:[A-Z]+ )?PRIVATE KEY-----|(["'])|\\?\z)`
const COOKIE_LINE_PATTERN = String.raw`(?im)^([^\r\n"']*\b(?:set-cookie|cookie)\s*:\s*)[^\r\n]*`

// A global phone matcher also matches UUID and hash fragments. Limit it to
// known content positions and root-string payloads, preserving structured IDs.
const PHONE_CONTENT_PATH_GLOBS = [
  "/messages/*/content",
  "/messages/*/content/*/text",
  // Relay wraps assistant history that carries provider-specific reasoning.
  "/messages/*/value/content",
  "/messages/*/value/reasoning",
  "/messages/*/value/reasoning_content",
  "/messages/*/value/reasoning_details/*/text",
  "/message",
  "/message/content",
  "/message/content/*/text",
  "/choices/*/message/content",
  "/choices/*/message/reasoning",
  "/choices/*/message/reasoning_content",
  "/choices/*/message/reasoning_details/*/text",
  "/choices/*/message/tool_calls/*/function/arguments",
  "/text",
  "/result",
  "/content",
  "/content/*/text",
  "/summary",
  "/input",
  "/input/*/content",
  "/input/*/content/*/text",
  "/output",
  "/output/*/content/*/text",
  "/arguments",
  "/tool_calls/*/function/arguments",
  "/contents/*/parts/*/text",
  "/candidates/*/content/parts/*/text",
  "/phone",
  "/phone_number",
]

// Known structured fields are omitted even when their values have no token
// prefix. Arbitrary nested business fields require explicit user paths.
const CREDENTIAL_PATHS = [
  "/headers",
  "/request/headers",
  "/response/headers",
  "/metadata/headers",
  "/authorization",
  "/Authorization",
  "/proxy-authorization",
  "/Proxy-Authorization",
  "/cookie",
  "/Cookie",
  "/set-cookie",
  "/api_key",
  "/apiKey",
  "/access_token",
  "/refresh_token",
  "/client_secret",
  "/clientSecret",
  "/password",
  "/passwd",
  "/secret",
  "/private_key",
]

function profile(priority: number, settings: string): string {
  return `[[components.config.profiles]]
mode = "builtin"
priority = ${priority}

[components.config.profiles.builtin]
${settings}`
}

function replaceProfile(
  priority: number,
  pattern: string,
  replacement: string
) {
  return profile(
    priority,
    `action = "regex_replace"\npattern = ${JSON.stringify(pattern)}\nreplacement = ${JSON.stringify(replacement)}`
  )
}

export function hermesRelayPrivacyConfiguration(
  options: HermesRelayPrivacyOptions = HERMES_RELAY_DEFAULT_PRIVACY
): string | null {
  if (omittedPathError(options.omittedPaths)) return null
  const profiles = [
    ...[
      "api_key",
      "bearer_token",
      "jwt",
      "aws_access_key_id",
      "gcp_api_key",
    ].map((detector, index) =>
      profile(
        100 + index,
        `action = "redact"\ndetector = ${JSON.stringify(detector)}`
      )
    ),
    replaceProfile(108, PRIVATE_KEY_PATTERN, "[REDACTED]${1}"),
    profile(
      110,
      `action = "redact"\npattern = ${JSON.stringify(CREDENTIAL_PATTERN)}`
    ),
    replaceProfile(111, JSON_CREDENTIAL_PATTERN, '${1}"[REDACTED]"'),
    replaceProfile(112, JSON_TEXT_CREDENTIAL_PATTERN, '${1}[REDACTED]"'),
    replaceProfile(113, COOKIE_LINE_PATTERN, "${1}[REDACTED]"),
    replaceProfile(114, LABELED_CREDENTIAL_PATTERN, "${1}[REDACTED]"),
    profile(
      120,
      `action = "remove"\ntarget_paths = ${JSON.stringify(CREDENTIAL_PATHS)}`
    ),
  ]
  if (options.personalInformation)
    profiles.push(
      profile(130, 'action = "redact"\ndetector = "email"'),
      profile(
        131,
        `action = "redact"\ndetector = "phone"\ntarget_paths = [""]\ntarget_path_globs = ${JSON.stringify(PHONE_CONTENT_PATH_GLOBS)}`
      )
    )
  if (options.omittedPaths.length)
    profiles.push(
      profile(
        140,
        `action = "remove"\ntarget_paths = ${JSON.stringify([...new Set(options.omittedPaths)])}`
      )
    )
  return `[[components]]
kind = "pii_redaction"
enabled = true

[components.config]
version = 1

${profiles.join("\n\n")}`
}
