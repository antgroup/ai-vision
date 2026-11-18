import { SpaceType, type Space } from "@/lib/schemas/space"

const AGENT_DASHBOARD_RE =
  /^\/(?:spaces\/([^/]+)\/)?agents\/([^/]+)\/dashboard\/?$/
const AGENT_QUERY_PARAM = "agent"
const SPACE_QUERY_PARAM = "spaceId"
const AGENT_UUID_QUERY_PARAM = "agent_uuid"
const CODEFUSE_CLI_URL_VALUE = "CodeFuse+CLI"

const AGENT_PRODUCT_URL_VALUES: Record<string, string> = {
  "CodeFuse CLI": CODEFUSE_CLI_URL_VALUE,
  "CodeFuse-CLI": CODEFUSE_CLI_URL_VALUE,
  "codefuse-cli": CODEFUSE_CLI_URL_VALUE,
  CODEFUSE_CLI: CODEFUSE_CLI_URL_VALUE,
}

const AGENT_PRODUCT_URL_ALIASES: Record<string, string> = {
  [CODEFUSE_CLI_URL_VALUE]: "CodeFuse CLI",
  "codefuse+cli": "CodeFuse CLI",
  "codefuse cli": "CodeFuse CLI",
  "codefuse-cli": "CodeFuse CLI",
  codefuse_cli: "CodeFuse CLI",
}

function getRawQueryParam(search: string, name: string) {
  const rawSearch = search.startsWith("?") ? search.slice(1) : search
  if (!rawSearch) return null

  for (const part of rawSearch.split("&")) {
    const [rawKey, rawValue = ""] = part.split("=")
    const key = decodeURIComponent(rawKey.replace(/\+/g, " "))
    if (key === name) {
      return decodeURIComponent(rawValue)
    }
  }

  return null
}

export function encodeAgentProductForUrl(agentProduct: string) {
  return (
    AGENT_PRODUCT_URL_VALUES[agentProduct] ?? encodeURIComponent(agentProduct)
  )
}

export function decodeAgentProductFromUrl(agentProduct: string) {
  const key = agentProduct.toLowerCase()
  return AGENT_PRODUCT_URL_ALIASES[key] ?? agentProduct
}

export function getAgentProductFromSearch(search: string) {
  const rawAgentProduct = getRawQueryParam(search, AGENT_QUERY_PARAM)
  return rawAgentProduct ? decodeAgentProductFromUrl(rawAgentProduct) : null
}

export function getSpaceIdFromSearch(search: string) {
  return getRawQueryParam(search, SPACE_QUERY_PARAM) || null
}

export function getAgentUuidFromSearch(search: string) {
  return getRawQueryParam(search, AGENT_UUID_QUERY_PARAM) || null
}

function buildAgentSearchParams(
  search: string,
  agentProduct: string | null,
  options?: {
    spaceId?: string | null
    agentUuid?: string | null
  }
) {
  const params = new URLSearchParams(search)
  params.delete(AGENT_QUERY_PARAM)
  params.delete(SPACE_QUERY_PARAM)
  params.delete(AGENT_UUID_QUERY_PARAM)

  const pieces: string[] = []
  const rest = params.toString()
  if (rest) {
    pieces.push(rest)
  }
  if (agentProduct) {
    pieces.push(
      `${AGENT_QUERY_PARAM}=${encodeAgentProductForUrl(agentProduct)}`
    )
  }
  if (options?.spaceId) {
    pieces.push(`${SPACE_QUERY_PARAM}=${encodeURIComponent(options.spaceId)}`)
  }
  if (options?.agentUuid) {
    pieces.push(
      `${AGENT_UUID_QUERY_PARAM}=${encodeURIComponent(options.agentUuid)}`
    )
  }

  return pieces.join("&")
}

export function buildAgentScopedUrl(
  pathname: string,
  agentProduct: string,
  search = "",
  agentUuid?: string | null
) {
  const query = buildAgentSearchParams(search, agentProduct, { agentUuid })
  return query ? `${pathname}?${query}` : pathname
}

export function setAgentProductInSearch(
  search: string,
  agentProduct: string | null,
  options?: {
    agentUuid?: string | null
  }
) {
  const existingSpaceId = new URLSearchParams(search).get(SPACE_QUERY_PARAM)
  return buildAgentSearchParams(search, agentProduct, {
    spaceId: existingSpaceId,
    agentUuid: options?.agentUuid ?? null,
  })
}

function getAgentDashboardSearchRoute(search: string) {
  const params = new URLSearchParams(search)
  const agentProduct = getAgentProductFromSearch(search)

  if (!agentProduct) {
    return null
  }

  return {
    spaceId: params.get(SPACE_QUERY_PARAM),
    agentProduct,
    agentUuid: params.get(AGENT_UUID_QUERY_PARAM) || null,
  }
}

export function isAgentDashboardPath(pathname: string, search = "") {
  return Boolean(getAgentDashboardRoute(pathname, search))
}

export function getAgentDashboardRoute(pathname: string, search = "") {
  if (pathname === "/" || pathname === "/dashboard") {
    return getAgentDashboardSearchRoute(search)
  }

  const match = pathname.match(AGENT_DASHBOARD_RE)
  if (!match) {
    return null
  }

  return {
    spaceId: match[1] ? decodeURIComponent(match[1]) : null,
    agentProduct: decodeAgentProductFromUrl(decodeURIComponent(match[2])),
    agentUuid: getRawQueryParam(search, AGENT_UUID_QUERY_PARAM) || null,
  }
}

export function isPersonalAgentDashboardPath(pathname: string, search = "") {
  const route = getAgentDashboardRoute(pathname, search)
  return Boolean(route && !route.spaceId)
}

export function getAgentProductFromDashboardPath(
  pathname: string,
  search = ""
) {
  return getAgentDashboardRoute(pathname, search)?.agentProduct ?? null
}

export function getAgentUuidFromDashboardPath(pathname: string, search = "") {
  return getAgentDashboardRoute(pathname, search)?.agentUuid ?? null
}

export function getSpaceIdFromDashboardPath(pathname: string, search = "") {
  return getAgentDashboardRoute(pathname, search)?.spaceId ?? null
}

export function buildAgentDashboardUrl(
  agentProduct: string,
  options?: {
    search?: string
    spaceId?: string | null
    agentUuid?: string | null
  }
) {
  const query = buildAgentSearchParams(options?.search ?? "", agentProduct, {
    spaceId: options?.spaceId,
    agentUuid: options?.agentUuid,
  })
  return query ? `/dashboard?${query}` : "/dashboard"
}

export function buildAgentDashboardPath(
  agentProduct: string,
  space: Space | null,
  search = "",
  agentUuid?: string | null
) {
  return buildAgentDashboardUrl(agentProduct, {
    search,
    spaceId: space?.type === SpaceType.Business ? space.id : null,
    agentUuid,
  })
}
